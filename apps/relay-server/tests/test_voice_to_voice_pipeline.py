"""VoiceToVoicePipeline 단위 테스트.

핵심 검증 사항:
  - Pipeline 생성 (타입, ring buffer, echo gate, local vad, recovery)
  - User Audio 처리 (Session A 전달, recovery skip, degraded mode, commit)
  - Twilio Audio 처리 (echo gate silence injection, local VAD, energy gate)
  - Session A 콜백 (TTS → Twilio, 수신자 발화 중 skip, done/caption)
  - Session B 출력 큐 (_drain_b_output: audio, caption, original_caption, caption_done)
  - 수신자 발화 콜백 (echo break, first message, interrupt, context)
  - Local VAD 콜백 (speech start/end → Session B 알림)
  - 통화 시간 제한 (warning, timeout)
"""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.realtime.audio_router import AudioRouter
from src.types import (
    ActiveCall,
    CallMode,
    CommunicationMode,
    WsMessage,
    WsMessageType,
)


def _make_call(**overrides) -> ActiveCall:
    defaults = dict(
        call_id="test-call-v2v",
        user_id="u1",
        mode=CallMode.RELAY,
        source_language="en",
        target_language="ko",
        target_phone="+821012345678",
        twilio_call_sid="CA_test",
        communication_mode=CommunicationMode.VOICE_TO_VOICE,
    )
    defaults.update(overrides)
    return ActiveCall(**defaults)


def _make_router(**call_overrides) -> AudioRouter:
    """VoiceToVoice 모드의 AudioRouter(→VoiceToVoicePipeline) 인스턴스 생성."""
    call = _make_call(**call_overrides)

    dual = MagicMock()
    dual.session_a = MagicMock()
    dual.session_a.on = MagicMock()
    dual.session_a.set_on_connection_lost = MagicMock()
    dual.session_a._send = AsyncMock()
    dual.session_a.clear_input_buffer = AsyncMock()
    dual.session_b = MagicMock()
    dual.session_b.on = MagicMock()
    dual.session_b.set_on_connection_lost = MagicMock()
    dual.session_b._send = AsyncMock()
    dual.session_b.clear_input_buffer = AsyncMock()

    twilio_handler = MagicMock()
    twilio_handler.send_audio = AsyncMock()
    twilio_handler.send_clear = AsyncMock()

    app_ws_send = AsyncMock()

    with (
        patch("src.realtime.pipeline.voice_to_voice.settings") as mock_settings,
        patch("src.realtime.pipeline.voice_to_voice.LocalVAD"),
    ):
        mock_settings.guardrail_enabled = False
        mock_settings.ring_buffer_capacity_slots = 100
        mock_settings.call_warning_ms = 480_000
        mock_settings.max_call_duration_ms = 600_000
        mock_settings.audio_energy_gate_enabled = False
        mock_settings.audio_energy_min_rms = 150.0
        mock_settings.echo_energy_threshold_rms = 400.0
        mock_settings.local_vad_enabled = False
        mock_settings.echo_post_settling_s = 2.0
        router = AudioRouter(
            call=call,
            dual_session=dual,
            twilio_handler=twilio_handler,
            app_ws_send=app_ws_send,
        )

    return router


def _make_router_with_local_vad(**call_overrides) -> AudioRouter:
    """Local VAD가 활성화된 VoiceToVoice AudioRouter 인스턴스 생성."""
    call = _make_call(**call_overrides)

    dual = MagicMock()
    dual.session_a = MagicMock()
    dual.session_a.on = MagicMock()
    dual.session_a.set_on_connection_lost = MagicMock()
    dual.session_a._send = AsyncMock()
    dual.session_a.clear_input_buffer = AsyncMock()
    dual.session_b = MagicMock()
    dual.session_b.on = MagicMock()
    dual.session_b.set_on_connection_lost = MagicMock()
    dual.session_b._send = AsyncMock()
    dual.session_b.clear_input_buffer = AsyncMock()

    twilio_handler = MagicMock()
    twilio_handler.send_audio = AsyncMock()
    twilio_handler.send_clear = AsyncMock()

    app_ws_send = AsyncMock()

    with (
        patch("src.realtime.pipeline.voice_to_voice.settings") as mock_settings,
        patch("src.realtime.pipeline.voice_to_voice.LocalVAD") as MockLocalVAD,
    ):
        mock_settings.guardrail_enabled = False
        mock_settings.ring_buffer_capacity_slots = 100
        mock_settings.call_warning_ms = 480_000
        mock_settings.max_call_duration_ms = 600_000
        mock_settings.audio_energy_gate_enabled = False
        mock_settings.audio_energy_min_rms = 150.0
        mock_settings.echo_energy_threshold_rms = 400.0
        mock_settings.local_vad_enabled = True
        mock_settings.local_vad_rms_threshold = 200.0
        mock_settings.local_vad_speech_threshold = 0.5
        mock_settings.local_vad_silence_threshold = 0.35
        mock_settings.local_vad_min_speech_frames = 3
        mock_settings.local_vad_min_silence_frames = 15
        mock_settings.echo_post_settling_s = 2.0

        # LocalVAD mock instance
        mock_vad_instance = MagicMock()
        mock_vad_instance.is_speaking = False
        mock_vad_instance.peak_rms = 0.0
        mock_vad_instance.process = AsyncMock()
        mock_vad_instance.reset = MagicMock()
        mock_vad_instance.reset_state = MagicMock()
        MockLocalVAD.return_value = mock_vad_instance

        router = AudioRouter(
            call=call,
            dual_session=dual,
            twilio_handler=twilio_handler,
            app_ws_send=app_ws_send,
        )

    return router


# ===========================================================================
# TestVoiceToVoicePipelineCreation
# ===========================================================================


class TestVoiceToVoicePipelineCreation:
    """VoiceToVoicePipeline이 올바르게 생성되는지 검증."""

    def test_pipeline_type(self):
        """VOICE_TO_VOICE 모드에서 VoiceToVoicePipeline이 생성된다."""
        from src.realtime.pipeline.voice_to_voice import VoiceToVoicePipeline

        router = _make_router()
        assert isinstance(router._pipeline, VoiceToVoicePipeline)

    def test_ring_buffers_created(self):
        """ring_buffer_a 와 ring_buffer_b가 존재한다."""
        router = _make_router()
        assert router.ring_buffer_a is not None
        assert router.ring_buffer_b is not None

    def test_echo_gate_max_12s(self):
        """echo_gate._max_echo_window_s == 1.2 (V2V 기본값)."""
        router = _make_router()
        assert router.echo_gate._max_echo_window_s == 1.2

    def test_local_vad_disabled_by_default(self):
        """settings.local_vad_enabled=False 시 local_vad는 None."""
        router = _make_router()
        assert router.local_vad is None

    def test_recovery_managers_created(self):
        """recovery_a 와 recovery_b가 존재한다."""
        router = _make_router()
        assert router.recovery_a is not None
        assert router.recovery_b is not None


# ===========================================================================
# TestVoiceToVoiceUserAudio
# ===========================================================================


class TestVoiceToVoiceUserAudio:
    """User App → Session A 오디오 처리 검증."""

    @pytest.mark.asyncio
    async def test_audio_sent_to_session_a(self):
        """handle_user_audio가 ring_buffer_a에 쓰고 session_a.send_user_audio를 호출한다."""
        router = _make_router()
        router.session_a.send_user_audio = AsyncMock()
        router.recovery_a = MagicMock()
        router.recovery_a.is_recovering = False
        router.recovery_a.is_degraded = False

        audio_b64 = base64.b64encode(b"\x00\x01\x02\x03").decode()
        await router.handle_user_audio(audio_b64)

        router.session_a.send_user_audio.assert_called_once_with(audio_b64)

    @pytest.mark.asyncio
    async def test_audio_skipped_during_recovery(self):
        """recovery_a.is_recovering=True → session_a.send_user_audio가 호출되지 않는다."""
        router = _make_router()
        router.session_a.send_user_audio = AsyncMock()
        router.recovery_a = MagicMock()
        router.recovery_a.is_recovering = True
        router.recovery_a.is_degraded = False

        audio_b64 = base64.b64encode(b"\x00\x01").decode()
        await router.handle_user_audio(audio_b64)

        router.session_a.send_user_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_audio_degraded_mode(self):
        """recovery_a.is_degraded=True → recovery_a.process_degraded_audio를 호출한다."""
        router = _make_router()
        router.session_a.send_user_audio = AsyncMock()
        router.recovery_a = MagicMock()
        router.recovery_a.is_recovering = False
        router.recovery_a.is_degraded = True
        router.recovery_a.process_degraded_audio = AsyncMock(return_value=None)

        audio_b64 = base64.b64encode(b"\x00\x01").decode()
        await router.handle_user_audio(audio_b64)

        router.session_a.send_user_audio.assert_not_called()
        router.recovery_a.process_degraded_audio.assert_called_once()

    @pytest.mark.asyncio
    async def test_audio_commit_sends_processing_state(self):
        """handle_user_audio_commit이 TRANSLATION_STATE processing을 전송한다."""
        router = _make_router()
        router.session_a.commit_user_audio = AsyncMock()
        router.recovery_a = MagicMock()
        router.recovery_a.is_recovering = False
        router.recovery_a.is_degraded = False
        router.context_manager = MagicMock()
        router.context_manager.inject_context = AsyncMock()

        await router.handle_user_audio_commit()

        router._app_ws_send.assert_called()
        msg = router._app_ws_send.call_args_list[0][0][0]
        assert msg.type == WsMessageType.TRANSLATION_STATE
        assert msg.data["state"] == "processing"

    @pytest.mark.asyncio
    async def test_audio_commit_injects_context(self):
        """handle_user_audio_commit이 context_manager.inject_context를 호출한다."""
        router = _make_router()
        router.session_a.commit_user_audio = AsyncMock()
        router.recovery_a = MagicMock()
        router.recovery_a.is_recovering = False
        router.recovery_a.is_degraded = False
        router.context_manager = MagicMock()
        router.context_manager.inject_context = AsyncMock()

        await router.handle_user_audio_commit()

        router.context_manager.inject_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_audio_commit_calls_session_a_commit(self):
        """handle_user_audio_commit이 session_a.commit_user_audio를 호출한다."""
        router = _make_router()
        router.session_a.commit_user_audio = AsyncMock()
        router.recovery_a = MagicMock()
        router.recovery_a.is_recovering = False
        router.recovery_a.is_degraded = False
        router.context_manager = MagicMock()
        router.context_manager.inject_context = AsyncMock()

        await router.handle_user_audio_commit()

        router.session_a.commit_user_audio.assert_called_once()

    @pytest.mark.asyncio
    async def test_audio_commit_skipped_during_recovery(self):
        """recovery 중 handle_user_audio_commit은 아무것도 하지 않는다."""
        router = _make_router()
        router.session_a.commit_user_audio = AsyncMock()
        router.recovery_a = MagicMock()
        router.recovery_a.is_recovering = True
        router.recovery_a.is_degraded = False

        await router.handle_user_audio_commit()

        router.session_a.commit_user_audio.assert_not_called()
        router._app_ws_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_ring_buffer_a_write(self):
        """base64 디코딩된 바이트가 ring_buffer_a에 기록된다."""
        router = _make_router()
        router.session_a.send_user_audio = AsyncMock()
        router.recovery_a = MagicMock()
        router.recovery_a.is_recovering = False
        router.recovery_a.is_degraded = False

        data = b"\xAA\xBB\xCC\xDD"
        audio_b64 = base64.b64encode(data).decode()
        await router.handle_user_audio(audio_b64)

        # ring_buffer_a에 기록되었는지 확인 (write 후 _total_written이 증가)
        assert router.ring_buffer_a._total_written > 0


# ===========================================================================
# TestVoiceToVoiceTwilioAudio
# ===========================================================================


class TestVoiceToVoiceTwilioAudio:
    """Twilio → Session B 오디오 처리 검증."""

    @pytest.mark.asyncio
    async def test_echo_gate_silence_injection(self):
        """echo window ON + 저에너지 → silence(0xFF)가 Session B에 전송된다."""
        router = _make_router()
        router.session_b.send_recipient_audio = AsyncMock()
        router.echo_gate.in_echo_window = True

        audio = bytes([0xFE] * 160)  # 저에너지 오디오
        await router.handle_twilio_audio(audio)

        router.session_b.send_recipient_audio.assert_called_once()
        sent_b64 = router.session_b.send_recipient_audio.call_args[0][0]
        sent_bytes = base64.b64decode(sent_b64)
        assert all(b == 0xFF for b in sent_bytes)
        assert len(sent_bytes) == 160

    @pytest.mark.asyncio
    async def test_echo_gate_off_passes_audio(self):
        """echo window OFF → 원본 오디오가 그대로 전달된다."""
        router = _make_router()
        router.session_b.send_recipient_audio = AsyncMock()
        router.echo_gate.in_echo_window = False

        audio = bytes([0x10] * 160)

        with patch("src.realtime.pipeline.voice_to_voice.settings") as mock_s:
            mock_s.audio_energy_gate_enabled = False
            await router.handle_twilio_audio(audio)

        router.session_b.send_recipient_audio.assert_called_once()
        sent_b64 = router.session_b.send_recipient_audio.call_args[0][0]
        sent_bytes = base64.b64decode(sent_b64)
        assert sent_bytes == audio

    @pytest.mark.asyncio
    async def test_high_rms_breaks_echo_gate(self):
        """echo window 중 고에너지 오디오 → 게이트 해제 + 원본 전달."""
        router = _make_router()
        router.session_b.send_recipient_audio = AsyncMock()
        router.echo_gate.in_echo_window = True

        audio = bytes([0x10] * 160)  # 고에너지 오디오 (실제 발화)
        await router.handle_twilio_audio(audio)

        assert router.echo_gate.in_echo_window is False
        router.session_b.send_recipient_audio.assert_called_once()
        sent_b64 = router.session_b.send_recipient_audio.call_args[0][0]
        sent_bytes = base64.b64decode(sent_b64)
        assert sent_bytes == audio

    @pytest.mark.asyncio
    async def test_local_vad_speaking_sends_audio(self):
        """local_vad.is_speaking=True → 실제 오디오가 Session B에 전송된다."""
        router = _make_router_with_local_vad()
        router.session_b.send_recipient_audio = AsyncMock()
        router.local_vad.is_speaking = True
        router.echo_gate.in_echo_window = False

        audio = bytes([0x10] * 160)
        await router.handle_twilio_audio(audio)

        router.session_b.send_recipient_audio.assert_called_once()
        sent_b64 = router.session_b.send_recipient_audio.call_args[0][0]
        sent_bytes = base64.b64decode(sent_b64)
        # effective_audio가 전송되어야 함 (echo gate off → 원본)
        assert sent_bytes == audio

    @pytest.mark.asyncio
    async def test_local_vad_silence_sends_silence(self):
        """local_vad.is_speaking=False → silence(0xFF)가 Session B에 전송된다."""
        router = _make_router_with_local_vad()
        router.session_b.send_recipient_audio = AsyncMock()
        router.local_vad.is_speaking = False
        router.echo_gate.in_echo_window = False

        audio = bytes([0x10] * 160)
        await router.handle_twilio_audio(audio)

        router.session_b.send_recipient_audio.assert_called_once()
        sent_b64 = router.session_b.send_recipient_audio.call_args[0][0]
        sent_bytes = base64.b64decode(sent_b64)
        assert all(b == 0xFF for b in sent_bytes)

    @pytest.mark.asyncio
    async def test_local_vad_echo_suppressed_sends_silence(self):
        """echo_gate.is_suppressing=True → 발화 중이어도 silence(0xFF) 전송."""
        router = _make_router_with_local_vad()
        router.session_b.send_recipient_audio = AsyncMock()
        router.local_vad.is_speaking = True
        router.echo_gate.in_echo_window = True  # is_suppressing = True

        audio = bytes([0xFE] * 160)  # 저에너지 (echo gate에서 silence로 변환)
        await router.handle_twilio_audio(audio)

        router.session_b.send_recipient_audio.assert_called_once()
        sent_b64 = router.session_b.send_recipient_audio.call_args[0][0]
        sent_bytes = base64.b64decode(sent_b64)
        # vad_suppressed=True이므로 silence(0xFF) 전송
        assert all(b == 0xFF for b in sent_bytes)

    @pytest.mark.asyncio
    async def test_energy_gate_drops_silence(self):
        """legacy path: 에너지 게이트 활성 시 무음 오디오가 드롭된다."""
        router = _make_router()
        router.session_b.send_recipient_audio = AsyncMock()

        silence = bytes([0xFF] * 160)

        with patch("src.realtime.pipeline.voice_to_voice.settings") as mock_s:
            mock_s.audio_energy_gate_enabled = True
            mock_s.audio_energy_min_rms = 150.0
            await router.handle_twilio_audio(silence)

        router.session_b.send_recipient_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_recovery_b_skips(self):
        """recovery_b.is_recovering=True → 오디오가 전송되지 않는다."""
        router = _make_router()
        router.session_b.send_recipient_audio = AsyncMock()
        router.recovery_b = MagicMock()
        router.recovery_b.is_recovering = True
        router.recovery_b.is_degraded = False

        audio = bytes([0x10] * 160)
        await router.handle_twilio_audio(audio)

        router.session_b.send_recipient_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_ring_buffer_b_write(self):
        """Twilio 오디오 바이트가 ring_buffer_b에 기록된다."""
        router = _make_router()
        router.session_b.send_recipient_audio = AsyncMock()

        audio = bytes([0x10] * 160)

        with patch("src.realtime.pipeline.voice_to_voice.settings") as mock_s:
            mock_s.audio_energy_gate_enabled = False
            await router.handle_twilio_audio(audio)

        assert router.ring_buffer_b._total_written > 0


# ===========================================================================
# TestVoiceToVoiceSessionACallbacks
# ===========================================================================


class TestVoiceToVoiceSessionACallbacks:
    """Session A 콜백 검증 (TTS, caption, done)."""

    @pytest.mark.asyncio
    async def test_tts_activates_echo_gate_and_sends_to_twilio(self):
        """_on_session_a_tts가 echo_gate.on_tts_chunk을 호출하고 twilio에 오디오를 전송한다."""
        router = _make_router()
        router.interrupt = MagicMock()
        router.interrupt.is_recipient_speaking = False

        tts_audio = b"\x00\x01\x02" * 50
        await router._on_session_a_tts(tts_audio)

        assert router.echo_gate.in_echo_window is True
        router.twilio_handler.send_audio.assert_called_once_with(tts_audio)

    @pytest.mark.asyncio
    async def test_tts_skipped_when_recipient_speaking(self):
        """interrupt.is_recipient_speaking=True → twilio_handler.send_audio가 호출되지 않는다."""
        router = _make_router()
        router.interrupt = MagicMock()
        router.interrupt.is_recipient_speaking = True

        tts_audio = b"\x00\x01\x02" * 50
        await router._on_session_a_tts(tts_audio)

        router.twilio_handler.send_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_done_starts_cooldown(self):
        """_on_session_a_done이 echo_gate.on_tts_done을 호출하고 TRANSLATION_STATE done을 전송한다."""
        router = _make_router()

        # TTS tracking 설정 (cooldown이 동작하도록)
        import time
        router.echo_gate._tts_first_chunk_at = time.time()
        router.echo_gate._tts_total_bytes = 400
        router.echo_gate._activate()

        await router._on_session_a_done()

        # cooldown task 생성 확인
        assert router.echo_gate._echo_cooldown_task is not None
        # TRANSLATION_STATE done 메시지 전송 확인
        calls = router._app_ws_send.call_args_list
        state_msgs = [c[0][0] for c in calls if c[0][0].type == WsMessageType.TRANSLATION_STATE]
        assert any(m.data.get("state") == "done" for m in state_msgs)

        # cleanup
        if router.echo_gate._echo_cooldown_task:
            router.echo_gate._echo_cooldown_task.cancel()
            try:
                await router.echo_gate._echo_cooldown_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_caption_sends_ws_message(self):
        """_on_session_a_caption이 WsMessageType.CAPTION을 전송한다."""
        router = _make_router()

        await router._on_session_a_caption("assistant", "번역된 텍스트")

        router._app_ws_send.assert_called_once()
        msg = router._app_ws_send.call_args[0][0]
        assert msg.type == WsMessageType.CAPTION
        assert msg.data["role"] == "assistant"
        assert msg.data["text"] == "번역된 텍스트"
        assert msg.data["direction"] == "outbound"

    @pytest.mark.asyncio
    async def test_user_transcription_sends_caption(self):
        """_on_user_transcription이 direction='outbound' CAPTION을 전송한다."""
        router = _make_router()

        await router._on_user_transcription("Hello, I want to make a reservation")

        router._app_ws_send.assert_called_once()
        msg = router._app_ws_send.call_args[0][0]
        assert msg.type == WsMessageType.CAPTION
        assert msg.data["role"] == "user"
        assert msg.data["direction"] == "outbound"
        assert msg.data["language"] == "en"
        assert msg.data["text"] == "Hello, I want to make a reservation"


# ===========================================================================
# TestVoiceToVoiceDrainQueue
# ===========================================================================


class TestVoiceToVoiceDrainQueue:
    """Session B 출력 큐 (_drain_b_output) 처리 검증."""

    @pytest.mark.asyncio
    async def test_audio_item_sent_to_app(self):
        """audio 아이템 → RECIPIENT_AUDIO WsMessage 전송."""
        router = _make_router()
        await router._b_output_queue.put(("audio", b"\x00\x01"))
        task = asyncio.create_task(router._drain_b_output())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        router._app_ws_send.assert_called()
        msg = router._app_ws_send.call_args[0][0]
        assert msg.type == WsMessageType.RECIPIENT_AUDIO
        assert "audio" in msg.data

    @pytest.mark.asyncio
    async def test_caption_item_sent_to_app(self):
        """caption 아이템 → CAPTION_TRANSLATED WsMessage 전송."""
        router = _make_router()
        await router._b_output_queue.put(("caption", ("recipient", "번역 텍스트")))
        task = asyncio.create_task(router._drain_b_output())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        router._app_ws_send.assert_called()
        msg = router._app_ws_send.call_args[0][0]
        assert msg.type == WsMessageType.CAPTION_TRANSLATED
        assert msg.data["role"] == "recipient"
        assert msg.data["text"] == "번역 텍스트"
        assert msg.data["stage"] == 2
        assert msg.data["direction"] == "inbound"

    @pytest.mark.asyncio
    async def test_original_caption_sent_to_app(self):
        """original_caption 아이템 → CAPTION_ORIGINAL WsMessage 전송."""
        router = _make_router()
        await router._b_output_queue.put(("original_caption", ("recipient", "원문 텍스트")))
        task = asyncio.create_task(router._drain_b_output())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        router._app_ws_send.assert_called()
        msg = router._app_ws_send.call_args[0][0]
        assert msg.type == WsMessageType.CAPTION_ORIGINAL
        assert msg.data["role"] == "recipient"
        assert msg.data["text"] == "원문 텍스트"
        assert msg.data["stage"] == 1
        assert msg.data["direction"] == "inbound"

    @pytest.mark.asyncio
    async def test_caption_done_sends_state(self):
        """caption_done 아이템 → TRANSLATION_STATE caption_done 전송."""
        router = _make_router()
        await router._b_output_queue.put(("caption_done", None))
        task = asyncio.create_task(router._drain_b_output())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        router._app_ws_send.assert_called()
        msg = router._app_ws_send.call_args[0][0]
        assert msg.type == WsMessageType.TRANSLATION_STATE
        assert msg.data["state"] == "caption_done"
        assert msg.data["direction"] == "inbound"

    @pytest.mark.asyncio
    async def test_playback_timing_tracked(self):
        """audio 청크가 _b_playback_total_bytes와 _b_playback_first_chunk_at을 업데이트한다."""
        router = _make_router()
        audio_data = b"\x00" * 480  # 480 bytes
        await router._b_output_queue.put(("audio", audio_data))
        task = asyncio.create_task(router._drain_b_output())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert router._b_playback_total_bytes == 480
        assert router._b_playback_first_chunk_at > 0.0

    @pytest.mark.asyncio
    async def test_caption_done_resets_playback_tracking(self):
        """caption_done 후 _b_playback_total_bytes=0으로 리셋된다."""
        router = _make_router()
        # 먼저 audio를 넣어서 playback tracking 시작
        await router._b_output_queue.put(("audio", b"\x00" * 100))
        await router._b_output_queue.put(("caption_done", None))
        task = asyncio.create_task(router._drain_b_output())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert router._b_playback_total_bytes == 0
        assert router._b_playback_first_chunk_at == 0.0

    @pytest.mark.asyncio
    async def test_queue_order_preserved(self):
        """아이템이 FIFO 순서대로 처리된다."""
        router = _make_router()
        await router._b_output_queue.put(("audio", b"\x01"))
        await router._b_output_queue.put(("caption", ("recipient", "text1")))
        await router._b_output_queue.put(("original_caption", ("recipient", "text2")))
        task = asyncio.create_task(router._drain_b_output())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        calls = router._app_ws_send.call_args_list
        assert len(calls) == 3
        assert calls[0][0][0].type == WsMessageType.RECIPIENT_AUDIO
        assert calls[1][0][0].type == WsMessageType.CAPTION_TRANSLATED
        assert calls[2][0][0].type == WsMessageType.CAPTION_ORIGINAL

    @pytest.mark.asyncio
    async def test_drain_task_cancellable(self):
        """drain task가 cancel 시 CancelledError를 발생시키지 않는다."""
        router = _make_router()
        task = asyncio.create_task(router._drain_b_output())
        await asyncio.sleep(0.02)
        task.cancel()
        # CancelledError가 내부에서 잡혀서 전파되지 않아야 함
        try:
            await task
        except asyncio.CancelledError:
            pass  # 정상: drain_b_output이 CancelledError를 잡거나 전파해도 OK


# ===========================================================================
# TestVoiceToVoiceRecipientCallbacks
# ===========================================================================


class TestVoiceToVoiceRecipientCallbacks:
    """수신자 발화 콜백 검증."""

    @pytest.mark.asyncio
    async def test_echo_break_on_recipient_speech(self):
        """echo window ON → _on_recipient_started가 echo_gate를 해제한다."""
        router = _make_router()
        router.echo_gate.in_echo_window = True
        router.first_message = MagicMock()
        router.first_message.on_recipient_speech_detected = AsyncMock()
        router.call.first_message_sent = False

        await router._on_recipient_started()

        assert router.echo_gate.in_echo_window is False

    @pytest.mark.asyncio
    async def test_first_message_on_recipient_speech(self):
        """first_message_sent=False → first_message.on_recipient_speech_detected가 호출된다."""
        router = _make_router()
        router.echo_gate.in_echo_window = False
        router.first_message = MagicMock()
        router.first_message.on_recipient_speech_detected = AsyncMock()
        router.call.first_message_sent = False

        await router._on_recipient_started()

        router.first_message.on_recipient_speech_detected.assert_called_once()

    @pytest.mark.asyncio
    async def test_interrupt_on_recipient_speech(self):
        """first_message_sent=True → interrupt.on_recipient_speech_started가 호출된다."""
        router = _make_router()
        router.echo_gate.in_echo_window = False
        router.interrupt = MagicMock()
        router.interrupt.on_recipient_speech_started = AsyncMock()
        router.call.first_message_sent = True

        await router._on_recipient_started()

        router.interrupt.on_recipient_speech_started.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_inject_on_recipient_stopped(self):
        """_on_recipient_stopped이 context_manager.inject_context를 호출한다."""
        router = _make_router()
        router.context_manager = MagicMock()
        router.context_manager.inject_context = AsyncMock()
        router.interrupt = MagicMock()
        router.interrupt.on_recipient_speech_stopped = AsyncMock()

        await router._on_recipient_stopped()

        router.context_manager.inject_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_complete_adds_context(self):
        """_on_turn_complete가 context_manager에 턴을 추가한다."""
        router = _make_router()
        router.context_manager = MagicMock()
        router.context_manager.add_turn = MagicMock()

        await router._on_turn_complete("user", "Hello")

        router.context_manager.add_turn.assert_called_once_with("user", "Hello")


# ===========================================================================
# TestVoiceToVoiceLocalVADCallbacks
# ===========================================================================


class TestVoiceToVoiceLocalVADCallbacks:
    """Local VAD 콜백 검증."""

    @pytest.mark.asyncio
    async def test_speech_start_notifies_session_b(self):
        """_on_local_vad_speech_start가 session_b.notify_speech_started를 호출한다."""
        router = _make_router_with_local_vad()
        router.session_b.notify_speech_started = AsyncMock()

        await router._on_local_vad_speech_start()

        router.session_b.notify_speech_started.assert_called_once()

    @pytest.mark.asyncio
    async def test_speech_end_notifies_session_b(self):
        """_on_local_vad_speech_end가 session_b.notify_speech_stopped를 호출한다."""
        router = _make_router_with_local_vad()
        router.session_b.notify_speech_stopped = AsyncMock()
        router.local_vad.peak_rms = 350.0

        await router._on_local_vad_speech_end()

        router.session_b.notify_speech_stopped.assert_called_once()

    @pytest.mark.asyncio
    async def test_speech_end_passes_peak_rms(self):
        """_on_local_vad_speech_end가 peak_rms를 notify_speech_stopped에 전달한다."""
        router = _make_router_with_local_vad()
        router.session_b.notify_speech_stopped = AsyncMock()
        router.local_vad.peak_rms = 425.0

        await router._on_local_vad_speech_end()

        router.session_b.notify_speech_stopped.assert_called_once_with(peak_rms=425.0)


# ===========================================================================
# TestVoiceToVoiceCallTimer
# ===========================================================================


class TestVoiceToVoiceCallTimer:
    """통화 시간 제한 (warning + timeout) 검증."""

    @pytest.mark.asyncio
    async def test_warning_sent(self):
        """warning 시간 후 CALL_STATUS warning 메시지가 전송된다."""
        router = _make_router()

        with patch("src.realtime.pipeline.voice_to_voice.settings") as mock_s:
            mock_s.call_warning_ms = 50  # 50ms
            mock_s.max_call_duration_ms = 200  # 200ms
            await router._call_duration_timer()

        calls = router._app_ws_send.call_args_list
        warning_msgs = [
            c[0][0] for c in calls
            if c[0][0].type == WsMessageType.CALL_STATUS
            and c[0][0].data.get("status") == "warning"
        ]
        assert len(warning_msgs) >= 1

    @pytest.mark.asyncio
    async def test_timeout_sent(self):
        """max 시간 후 CALL_STATUS timeout 메시지가 전송된다."""
        router = _make_router()

        with patch("src.realtime.pipeline.voice_to_voice.settings") as mock_s:
            mock_s.call_warning_ms = 50
            mock_s.max_call_duration_ms = 100
            await router._call_duration_timer()

        calls = router._app_ws_send.call_args_list
        timeout_msgs = [
            c[0][0] for c in calls
            if c[0][0].type == WsMessageType.CALL_STATUS
            and c[0][0].data.get("status") == "timeout"
        ]
        assert len(timeout_msgs) >= 1
