"""셀프 통화(Loopback) 컴포넌트 테스트.

외부 서비스 없이 VoiceToVoicePipeline의 전체 라운드트립을 검증한다.
Mock DualSessionManager + TwilioHandler로 실제 Pipeline을 생성하고
핸들러를 직접 호출하여 오디오/캡션 흐름을 시뮬레이션한다.

Pipeline.__init__이 SessionAHandler, SessionBHandler를 직접 생성하므로
dual.session_a/b(RealtimeSession mock)의 모든 async 메서드를 AsyncMock으로
설정해야 내부 await가 정상 동작한다.
"""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.realtime.pipeline.voice_to_voice import VoiceToVoicePipeline
from src.types import ActiveCall, CallMode, CommunicationMode, WsMessageType


def _make_call(**overrides) -> ActiveCall:
    """테스트용 ActiveCall을 생성한다."""
    defaults = dict(
        call_id="loopback-call",
        mode=CallMode.RELAY,
        source_language="en",
        target_language="ko",
        communication_mode=CommunicationMode.VOICE_TO_VOICE,
    )
    defaults.update(overrides)
    return ActiveCall(**defaults)


def _make_mock_session(label: str = "MockSession") -> MagicMock:
    """RealtimeSession mock을 생성한다.

    SessionAHandler/SessionBHandler가 내부적으로 호출하는 모든
    async 메서드를 AsyncMock으로 설정한다.
    """
    session = MagicMock()
    session.label = label
    session.session_id = f"sess_{label}"
    session.is_closed = False
    session.on = MagicMock()
    session.send_audio = AsyncMock()
    session.send_text = AsyncMock()
    session.commit_audio = AsyncMock()
    session.cancel_response = AsyncMock()
    session.clear_input_buffer = AsyncMock()
    session.send_function_call_output = AsyncMock()
    session.connect = AsyncMock()
    session.close = AsyncMock()
    session.set_on_connection_lost = MagicMock()
    # conversation.item.delete 호출 대비
    session.delete_item = AsyncMock()
    return session


def _make_pipeline() -> tuple[VoiceToVoicePipeline, MagicMock, MagicMock, AsyncMock]:
    """Create a VoiceToVoicePipeline with all dependencies mocked.

    Returns: (pipeline, dual_session, twilio_handler, app_ws_send)

    주의: pipeline.session_a는 SessionAHandler (real), pipeline.session_a.session이
    mock RealtimeSession이다. pipeline.session_b도 동일.
    """
    call = _make_call()

    mock_sess_a = _make_mock_session("SessionA")
    mock_sess_b = _make_mock_session("SessionB")

    dual = MagicMock()
    dual.session_a = mock_sess_a
    dual.session_b = mock_sess_b

    twilio_handler = MagicMock()
    twilio_handler.send_audio = AsyncMock()
    twilio_handler.send_clear = AsyncMock()

    app_ws_send = AsyncMock()

    with patch("src.realtime.pipeline.voice_to_voice.settings") as mock_settings:
        mock_settings.guardrail_enabled = False
        mock_settings.ring_buffer_capacity_slots = 100
        mock_settings.call_warning_ms = 480_000
        mock_settings.max_call_duration_ms = 600_000
        mock_settings.audio_energy_gate_enabled = False
        mock_settings.audio_energy_min_rms = 150.0
        mock_settings.echo_energy_threshold_rms = 400.0
        mock_settings.local_vad_enabled = False
        mock_settings.echo_post_settling_s = 2.0
        mock_settings.session_b_min_speech_ms = 250
        pipeline = VoiceToVoicePipeline(
            call=call,
            dual_session=dual,
            twilio_handler=twilio_handler,
            app_ws_send=app_ws_send,
        )

    return pipeline, dual, twilio_handler, app_ws_send


class TestLoopbackCall:
    """VoiceToVoicePipeline 셀프 통화 라운드트립 테스트 (8개)."""

    @pytest.mark.asyncio
    async def test_user_audio_to_session_a(self):
        """User 오디오가 Session A의 RealtimeSession에 전달된다."""
        pipeline, dual, _, _ = _make_pipeline()
        audio_bytes = b"\x00\x01\x02" * 50
        audio_b64 = base64.b64encode(audio_bytes).decode()

        await pipeline.handle_user_audio(audio_b64)

        # SessionAHandler.send_user_audio -> session.send_audio
        dual.session_a.send_audio.assert_called_once_with(audio_b64)

    @pytest.mark.asyncio
    async def test_session_a_tts_to_twilio(self):
        """Session A TTS 출력이 Twilio에 전달되고 echo window가 활성화된다."""
        pipeline, _, twilio_handler, _ = _make_pipeline()
        pipeline.interrupt = MagicMock()
        pipeline.interrupt.is_recipient_speaking = False

        tts_audio = b"\xAA\xBB" * 100
        await pipeline._on_session_a_tts(tts_audio)

        twilio_handler.send_audio.assert_called_once_with(tts_audio)
        assert pipeline.echo_gate.in_echo_window is True

    @pytest.mark.asyncio
    async def test_twilio_audio_to_session_b(self):
        """Twilio 수신자 오디오가 Session B의 RealtimeSession에 전달된다 (echo window OFF)."""
        pipeline, dual, _, _ = _make_pipeline()
        pipeline.echo_gate.in_echo_window = False

        with patch("src.realtime.pipeline.voice_to_voice.settings") as mock_s:
            mock_s.audio_energy_gate_enabled = False
            audio = b"\x10" * 160
            await pipeline.handle_twilio_audio(audio)

        # SessionBHandler.send_recipient_audio -> session.send_audio
        dual.session_b.send_audio.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_b_audio_to_app(self):
        """Session B 번역 오디오가 큐를 거쳐 App에 RECIPIENT_AUDIO로 전달된다."""
        pipeline, _, _, app_ws_send = _make_pipeline()

        # Put audio into B output queue
        await pipeline._on_session_b_audio(b"\x00\x01\x02")

        # Start drain task and let it process one item
        task = asyncio.create_task(pipeline._drain_b_output())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        app_ws_send.assert_called()
        msg = app_ws_send.call_args[0][0]
        assert msg.type == WsMessageType.RECIPIENT_AUDIO

    @pytest.mark.asyncio
    async def test_echo_prevention(self):
        """TTS 중 Twilio 오디오가 silence(0xFF)로 대체된다 (에코 차단 핵심 invariant)."""
        pipeline, dual, twilio_handler, _ = _make_pipeline()
        pipeline.interrupt = MagicMock()
        pipeline.interrupt.is_recipient_speaking = False

        # Step 1: TTS -> echo window 활성화
        await pipeline._on_session_a_tts(b"\xAA" * 200)
        assert pipeline.echo_gate.in_echo_window is True

        # Step 2: Twilio audio during echo window -> silence 대체
        echo_audio = bytes([0xFE] * 160)
        await pipeline.handle_twilio_audio(echo_audio)

        # SessionBHandler.send_recipient_audio -> session.send_audio(silence_b64)
        dual.session_b.send_audio.assert_called_once()
        sent_b64 = dual.session_b.send_audio.call_args[0][0]
        sent_bytes = base64.b64decode(sent_b64)
        assert all(b == 0xFF for b in sent_bytes), (
            "Echo window 중 Twilio 오디오는 mu-law silence(0xFF)로 대체되어야 한다"
        )

    @pytest.mark.asyncio
    async def test_recipient_interrupt(self):
        """수신자 발화가 감지되면 InterruptHandler에 위임된다."""
        pipeline, _, _, _ = _make_pipeline()
        pipeline.call.first_message_sent = True
        pipeline.interrupt = MagicMock()
        pipeline.interrupt.on_recipient_speech_started = AsyncMock()
        pipeline.echo_gate.in_echo_window = False

        await pipeline._on_recipient_started()

        pipeline.interrupt.on_recipient_speech_started.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_turn_cycle(self):
        """완전한 턴 사이클: User 입력 -> TTS -> 에코 차단 -> TTS 완료 -> 캡션."""
        pipeline, dual, twilio_handler, app_ws_send = _make_pipeline()
        pipeline.interrupt = MagicMock()
        pipeline.interrupt.is_recipient_speaking = False
        pipeline.interrupt.on_recipient_speech_started = AsyncMock()
        pipeline.interrupt.on_recipient_speech_stopped = AsyncMock()
        pipeline.call.first_message_sent = True

        # 1. User audio -> Session A
        audio_b64 = base64.b64encode(b"\x00" * 100).decode()
        await pipeline.handle_user_audio(audio_b64)
        dual.session_a.send_audio.assert_called_once()

        # 2. TTS output -> Twilio
        await pipeline._on_session_a_tts(b"\xAA" * 200)
        twilio_handler.send_audio.assert_called_once()
        assert pipeline.echo_gate.in_echo_window is True

        # 3. Echo blocked during TTS
        echo_audio = bytes([0xFE] * 160)
        await pipeline.handle_twilio_audio(echo_audio)
        sent_b64 = dual.session_b.send_audio.call_args[0][0]
        sent_bytes = base64.b64decode(sent_b64)
        assert all(b == 0xFF for b in sent_bytes)

        # 4. TTS done -> cooldown 시작
        await pipeline._on_session_a_done()

        # 5. Session B 캡션 수신 (수신자 번역 결과)
        await pipeline._on_session_b_caption("recipient", "안녕하세요")

        # Drain the queue
        task = asyncio.create_task(pipeline._drain_b_output())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Verify caption was sent to app
        caption_calls = [
            c for c in app_ws_send.call_args_list
            if c[0][0].type == WsMessageType.CAPTION_TRANSLATED
        ]
        assert len(caption_calls) >= 1
        caption_data = caption_calls[0][0][0].data
        assert caption_data["text"] == "안녕하세요"
        assert caption_data["role"] == "recipient"

    @pytest.mark.asyncio
    async def test_lifecycle_start_stop(self):
        """start() 후 백그라운드 작업이 실행되고 stop() 후 정리된다."""
        pipeline, _, _, _ = _make_pipeline()

        await pipeline.start()

        assert pipeline._call_timer_task is not None
        assert pipeline._b_output_drain_task is not None
        assert not pipeline._call_timer_task.done()
        assert not pipeline._b_output_drain_task.done()

        await pipeline.stop()

        assert pipeline._call_timer_task.done()
        assert pipeline._b_output_drain_task.done()
