"""TextToVoicePipeline 단위 테스트.

핵심 검증 사항:
  - audio 입력 무시 (graceful no-op)
  - text 입력 → per-response instruction override (Relay)
  - text 입력 → 기본 send_user_text (Agent)
  - Dynamic Energy Threshold: echo window 중 높은 에너지 임계값으로 에코 필터링
  - Session A TTS → Twilio 전달 + echo window 활성화
  - First Message: exact utterance 패턴
  - Audio Energy Gate 유지 (Twilio 수신자 무음 필터링)
"""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.realtime.audio_router import AudioRouter
from src.types import ActiveCall, CallMode, CommunicationMode


def _make_call(**overrides) -> ActiveCall:
    defaults = dict(
        call_id="test-call-ttv",
        user_id="u1",
        mode=CallMode.RELAY,
        source_language="en",
        target_language="ko",
        target_phone="+821012345678",
        twilio_call_sid="CA_test",
        communication_mode=CommunicationMode.TEXT_TO_VOICE,
    )
    defaults.update(overrides)
    return ActiveCall(**defaults)


def _make_router(**call_overrides) -> AudioRouter:
    """TextToVoice 모드의 AudioRouter(→TextToVoicePipeline) 인스턴스 생성."""
    call = _make_call(**call_overrides)

    dual = MagicMock()
    dual.session_a = MagicMock()
    dual.session_a.on = MagicMock()
    dual.session_a.set_on_connection_lost = MagicMock()
    dual.session_a._send = AsyncMock()
    dual.session_a.send_text_item = AsyncMock()
    dual.session_a.create_response = AsyncMock()
    dual.session_b = MagicMock()
    dual.session_b.on = MagicMock()
    dual.session_b.set_on_connection_lost = MagicMock()
    dual.session_b._send = AsyncMock()
    dual.session_b.clear_input_buffer = AsyncMock()

    twilio_handler = MagicMock()
    twilio_handler.send_audio = AsyncMock()
    twilio_handler.send_clear = AsyncMock()

    app_ws_send = AsyncMock()

    with patch("src.realtime.pipeline.text_to_voice.settings") as mock_settings:
        mock_settings.guardrail_enabled = False
        mock_settings.ring_buffer_capacity_slots = 100
        mock_settings.call_warning_ms = 480_000
        mock_settings.max_call_duration_ms = 600_000
        mock_settings.audio_energy_gate_enabled = False
        mock_settings.audio_energy_min_rms = 150.0
        mock_settings.echo_energy_threshold_rms = 400.0
        mock_settings.local_vad_enabled = False
        router = AudioRouter(
            call=call,
            dual_session=dual,
            twilio_handler=twilio_handler,
            app_ws_send=app_ws_send,
        )

    return router


class TestTextToVoicePipelineCreation:
    """TextToVoicePipeline이 올바르게 생성되는지 검증."""

    def test_pipeline_type(self):
        """TEXT_TO_VOICE 모드에서 TextToVoicePipeline이 생성된다."""
        from src.realtime.pipeline.text_to_voice import TextToVoicePipeline

        router = _make_router()
        assert isinstance(router._pipeline, TextToVoicePipeline)

    def test_echo_gate_initialized(self):
        """TextToVoice는 Echo Gate (Silence Injection)를 사용한다."""
        router = _make_router()
        assert router._pipeline._in_echo_window is False

    def test_first_message_exact_utterance(self):
        """First Message 핸들러가 exact utterance 모드로 생성된다."""
        router = _make_router()
        assert router.first_message._use_exact_utterance is True


class TestTextToVoiceAudioHandling:
    """TextToVoice의 오디오 입력 처리 검증."""

    @pytest.mark.asyncio
    async def test_user_audio_ignored(self):
        """User audio 입력은 graceful no-op으로 무시된다."""
        router = _make_router()
        # 에러 없이 조용히 무시되어야 함
        await router.handle_user_audio(base64.b64encode(b"\x00" * 100).decode())

    @pytest.mark.asyncio
    async def test_user_audio_commit_ignored(self):
        """User audio commit은 graceful no-op으로 무시된다."""
        router = _make_router()
        await router.handle_user_audio_commit()

    @pytest.mark.asyncio
    async def test_twilio_audio_passes_through(self):
        """Twilio 수신자 오디오는 Session B에 전달된다 (에코가 아닌 경우)."""
        router = _make_router()
        router.session_b = MagicMock()
        router.session_b.send_recipient_audio = AsyncMock()
        router.recovery_b = MagicMock()
        router.recovery_b.is_recovering = False
        router.recovery_b.is_degraded = False
        # Echo window 비활성 상태 (정상 오디오)
        router._pipeline._in_echo_window = False

        audio = b"\x80" * 100  # g711_ulaw 오디오
        await router.handle_twilio_audio(audio)

        router.session_b.send_recipient_audio.assert_called_once()

    @pytest.mark.asyncio
    async def test_twilio_audio_energy_gate_drops_low_energy(self):
        """Audio energy gate가 저에너지 소음을 드롭한다."""
        router = _make_router()
        router.session_b = MagicMock()
        router.session_b.send_recipient_audio = AsyncMock()
        router.recovery_b = MagicMock()
        router.recovery_b.is_recovering = False
        router.recovery_b.is_degraded = False
        # Echo window 비활성 상태
        router._pipeline._in_echo_window = False

        with patch("src.realtime.pipeline.text_to_voice.settings") as mock_s:
            mock_s.audio_energy_gate_enabled = True
            mock_s.audio_energy_min_rms = 150.0

            # PSTN 소음 데이터 (RMS 낮음)
            noise = b"\x7f" * 100  # mu-law silence
            await router.handle_twilio_audio(noise)

            # 저에너지 오디오는 드롭 (전송되지 않음)
            router.session_b.send_recipient_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_echo_window_sends_silence_instead_of_dropping(self):
        """Echo window 중 에코 차단 시 silence 프레임을 Session B에 전송한다.

        VAD 오디오 스트림을 유지하여 speech_stopped를 정상 감지하기 위함.
        """
        router = _make_router()
        router.session_b = MagicMock()
        router.session_b.send_recipient_audio = AsyncMock()
        router.recovery_b = MagicMock()
        router.recovery_b.is_recovering = False
        router.recovery_b.is_degraded = False
        # Echo window 활성 상태
        router._pipeline._in_echo_window = True

        with patch("src.realtime.pipeline.text_to_voice.settings") as mock_s:
            mock_s.audio_energy_gate_enabled = True
            mock_s.echo_energy_threshold_rms = 400.0
            mock_s.audio_energy_min_rms = 30.0

            # 에코 수준 오디오 (0xF0 → mu-law linear ~219, threshold 400 미만)
            echo_audio = b"\xf0" * 100
            await router.handle_twilio_audio(echo_audio)

            # silence 프레임이 전송되어야 함 (drop이 아님)
            router.session_b.send_recipient_audio.assert_called_once()
            sent_b64 = router.session_b.send_recipient_audio.call_args[0][0]
            import base64 as b64
            sent_bytes = b64.b64decode(sent_b64)
            # 전송된 바이트가 mu-law silence (0xFF)로 채워져야 함
            assert sent_bytes == b"\xff" * len(echo_audio)

    @pytest.mark.asyncio
    async def test_non_echo_window_drops_noise(self):
        """Echo window 외에서 저에너지 소음은 드롭된다."""
        router = _make_router()
        router.session_b = MagicMock()
        router.session_b.send_recipient_audio = AsyncMock()
        router.recovery_b = MagicMock()
        router.recovery_b.is_recovering = False
        router.recovery_b.is_degraded = False
        # Echo window 비활성
        router._pipeline._in_echo_window = False

        with patch("src.realtime.pipeline.text_to_voice.settings") as mock_s:
            mock_s.audio_energy_gate_enabled = True
            mock_s.audio_energy_min_rms = 150.0

            # PSTN 소음 데이터 (RMS < 150)
            noise = b"\x7f" * 100
            await router.handle_twilio_audio(noise)

            # 저에너지 오디오는 드롭 (전송되지 않음)
            router.session_b.send_recipient_audio.assert_not_called()


class TestTextToVoiceTextHandling:
    """TextToVoice의 텍스트 입력 처리 검증."""

    @pytest.mark.asyncio
    async def test_relay_mode_per_response_override(self):
        """Relay 모드에서 per-response instruction override가 적용된다."""
        router = _make_router(mode=CallMode.RELAY)
        router.session_a = MagicMock()
        router.session_a.is_generating = False
        router.interrupt = MagicMock()
        router.interrupt.is_recipient_speaking = False
        router.context_manager = MagicMock()
        router.context_manager.inject_context = AsyncMock()

        await router.handle_user_text("예약하고 싶어요")

        # per-response instruction override 사용 확인
        router.dual_session.session_a.send_text_item.assert_called_once_with("예약하고 싶어요")
        router.dual_session.session_a.create_response.assert_called_once()
        # instructions 인자가 전달되었는지 확인
        call_kwargs = router.dual_session.session_a.create_response.call_args
        assert call_kwargs.kwargs.get("instructions") is not None
        assert "translated sentence" in call_kwargs.kwargs["instructions"]

    @pytest.mark.asyncio
    async def test_agent_mode_uses_send_user_text(self):
        """Agent 모드에서는 기본 send_user_text를 사용한다."""
        router = _make_router(mode=CallMode.AGENT)
        router.session_a = MagicMock()
        router.session_a.is_generating = False
        router.session_a.send_user_text = AsyncMock()
        router.interrupt = MagicMock()
        router.interrupt.is_recipient_speaking = False
        router.context_manager = MagicMock()
        router.context_manager.inject_context = AsyncMock()

        await router.handle_user_text("Hello")

        router.session_a.send_user_text.assert_called_once_with("Hello")
        # per-response override는 사용하지 않음
        router.dual_session.session_a.send_text_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_sends_even_during_recipient_speaking(self):
        """수신자가 말하는 중에도 텍스트가 즉시 전송된다 (hold 없음)."""
        router = _make_router()
        router.session_a = MagicMock()
        router.session_a.is_generating = False
        router.interrupt = MagicMock()
        router.interrupt.is_recipient_speaking = True
        router.context_manager = MagicMock()
        router.context_manager.inject_context = AsyncMock()

        await router.handle_user_text("test")

        # Lock으로 직렬화되지만 hold하지 않고 바로 전송
        router.dual_session.session_a.send_text_item.assert_called_once_with("test")
        router.dual_session.session_a.create_response.assert_called_once()


class TestTextToVoiceSessionACallbacks:
    """Session A 콜백 검증."""

    @pytest.mark.asyncio
    async def test_tts_activates_echo_window(self):
        """TTS 오디오가 echo window를 활성화하고 Twilio에 전달된다."""
        router = _make_router()

        await router._on_session_a_tts(b"\x00\x01\x02" * 50)

        router.twilio_handler.send_audio.assert_called_once()
        # Echo window가 활성화되었는지 확인
        assert router._pipeline._in_echo_window is True

    @pytest.mark.asyncio
    async def test_tts_delivered_during_recipient_speech(self):
        """수신자가 말하는 중에도 TTS가 전달된다 (전이중 통화)."""
        router = _make_router()

        await router._on_session_a_tts(b"\x00\x01\x02" * 50)

        router.twilio_handler.send_audio.assert_called_once()


class TestTextToVoiceRaceCondition:
    """create_response() 직후 mark_generating() race condition 방지 검증."""

    @pytest.mark.asyncio
    async def test_mark_generating_sets_state_after_typing_filler(self):
        """handle_typing_started() 후 is_generating=True, done_event 미설정."""
        router = _make_router()
        # SessionAHandler의 실제 상태 머신을 사용 (mock 대신)
        router.session_a._is_generating = False
        router.session_a._done_event = asyncio.Event()
        router.session_a._done_event.set()
        router.session_a.mark_generating = router._pipeline.session_a.mark_generating
        # wait_for_done이 즉시 반환되도록 (이미 완료 상태)
        router.session_a.wait_for_done = AsyncMock(return_value=True)

        await router.handle_typing_started()

        assert router.session_a._is_generating is True
        assert not router.session_a._done_event.is_set()

    @pytest.mark.asyncio
    async def test_mark_generating_sets_state_after_user_text(self):
        """handle_user_text() 후 (Relay mode) is_generating=True."""
        router = _make_router(mode=CallMode.RELAY)
        router.session_a._is_generating = False
        router.session_a._done_event = asyncio.Event()
        router.session_a._done_event.set()
        router.session_a.mark_generating = router._pipeline.session_a.mark_generating
        router.session_a.wait_for_done = AsyncMock(return_value=True)
        router.context_manager = MagicMock()
        router.context_manager.inject_context = AsyncMock()

        await router.handle_user_text("테스트")

        assert router.session_a._is_generating is True
        assert not router.session_a._done_event.is_set()

    @pytest.mark.asyncio
    async def test_user_text_waits_when_filler_generating(self):
        """typing filler 생성 중 handle_user_text()가 wait_for_done()을 호출한다."""
        router = _make_router(mode=CallMode.RELAY)
        # filler가 generating 중인 상태 시뮬레이션
        router.session_a._is_generating = True
        router.session_a._done_event = asyncio.Event()
        # done_event을 설정하지 않아서 wait_for_done이 대기하게 됨
        router.session_a.wait_for_done = AsyncMock(return_value=True)
        router.session_a.mark_generating = router._pipeline.session_a.mark_generating
        router.context_manager = MagicMock()
        router.context_manager.inject_context = AsyncMock()

        await router.handle_user_text("테스트")

        # wait_for_done이 호출되었는지 확인 (filler 완료 대기)
        router.session_a.wait_for_done.assert_called_once_with(timeout=5.0)


class TestTextToVoiceFirstMessage:
    """First Message exact utterance 패턴 검증."""

    @pytest.mark.asyncio
    async def test_exact_utterance_wrapping(self):
        """First Message가 exact utterance 패턴으로 래핑된다."""
        router = _make_router()
        # first_message는 생성 시 session_a를 바인딩하므로 직접 mock
        mock_session_a = MagicMock()
        mock_session_a.is_generating = False
        mock_session_a.send_user_text = AsyncMock()
        router.first_message.session_a = mock_session_a

        await router.first_message.on_recipient_speech_detected()

        mock_session_a.send_user_text.assert_called_once()
        sent_text = mock_session_a.send_user_text.call_args[0][0]
        assert sent_text.startswith('Say exactly this sentence and nothing else:')
        assert router.call.first_message_sent is True
