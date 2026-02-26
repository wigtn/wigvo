"""AudioRouter 단위 테스트.

핵심 검증 사항:
  - CommunicationMode별 올바른 Pipeline 생성 (Strategy 패턴)
  - __getattr__/__setattr__ 프록시: Pipeline 내부 속성 투명 접근
  - _OWN_ATTRS (call, _pipeline): 프록시하지 않고 AudioRouter 자체에 저장
  - 명시적 위임 메서드: start, stop, handle_user_audio, handle_twilio_audio 등
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.realtime.audio_router import AudioRouter
from src.realtime.pipeline.base import BasePipeline
from src.realtime.pipeline.full_agent import FullAgentPipeline
from src.realtime.pipeline.text_to_voice import TextToVoicePipeline
from src.realtime.pipeline.voice_to_voice import VoiceToVoicePipeline
from src.types import ActiveCall, CallMode, CommunicationMode


def _make_call(**overrides) -> ActiveCall:
    defaults = dict(
        call_id="test-call-router",
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


def _make_router(
    communication_mode: CommunicationMode = CommunicationMode.VOICE_TO_VOICE,
    **call_overrides,
) -> AudioRouter:
    """지정된 CommunicationMode로 AudioRouter 인스턴스를 생성한다."""
    call = _make_call(communication_mode=communication_mode, **call_overrides)

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

    # V2V uses settings from voice_to_voice module; T2V and FA use text_to_voice module
    if communication_mode == CommunicationMode.VOICE_TO_VOICE:
        settings_path = "src.realtime.pipeline.voice_to_voice.settings"
    else:
        settings_path = "src.realtime.pipeline.text_to_voice.settings"

    with patch(settings_path) as mock_settings:
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


# ---------------------------------------------------------------------------
# TestAudioRouterDispatch: Pipeline 생성 전략 패턴 검증
# ---------------------------------------------------------------------------


class TestAudioRouterDispatch:
    """CommunicationMode에 따라 올바른 Pipeline이 생성되는지 검증."""

    def test_v2v_creates_voice_pipeline(self):
        """VOICE_TO_VOICE 모드에서 VoiceToVoicePipeline이 생성된다."""
        router = _make_router(CommunicationMode.VOICE_TO_VOICE)
        assert isinstance(router._pipeline, VoiceToVoicePipeline)

    def test_t2v_creates_text_pipeline(self):
        """TEXT_TO_VOICE 모드에서 TextToVoicePipeline이 생성된다."""
        router = _make_router(CommunicationMode.TEXT_TO_VOICE)
        assert isinstance(router._pipeline, TextToVoicePipeline)

    def test_fa_creates_full_agent_pipeline(self):
        """FULL_AGENT 모드에서 FullAgentPipeline이 생성된다."""
        router = _make_router(CommunicationMode.FULL_AGENT)
        assert isinstance(router._pipeline, FullAgentPipeline)

    def test_fa_is_subclass_of_t2v(self):
        """FullAgentPipeline은 TextToVoicePipeline의 서브클래스이다."""
        router = _make_router(CommunicationMode.FULL_AGENT)
        assert isinstance(router._pipeline, TextToVoicePipeline)

    def test_unknown_mode_raises_valueerror(self):
        """정의되지 않은 communication_mode는 ValueError를 발생시킨다."""
        call = _make_call()
        dual = MagicMock()
        dual.session_a = MagicMock()
        dual.session_a.on = MagicMock()
        dual.session_a.set_on_connection_lost = MagicMock()
        dual.session_b = MagicMock()
        dual.session_b.on = MagicMock()
        dual.session_b.set_on_connection_lost = MagicMock()
        twilio_handler = MagicMock()
        app_ws_send = AsyncMock()

        # Override communication_mode to a fake value after construction
        call.communication_mode = "invalid_mode"  # type: ignore

        with patch("src.realtime.pipeline.voice_to_voice.settings") as mock_s:
            mock_s.guardrail_enabled = False
            mock_s.ring_buffer_capacity_slots = 100
            mock_s.call_warning_ms = 480_000
            mock_s.max_call_duration_ms = 600_000
            mock_s.audio_energy_gate_enabled = False
            mock_s.audio_energy_min_rms = 150.0
            mock_s.echo_energy_threshold_rms = 400.0
            mock_s.local_vad_enabled = False
            mock_s.echo_post_settling_s = 2.0
            with pytest.raises(ValueError, match="Unknown communication mode"):
                AudioRouter(
                    call=call,
                    dual_session=dual,
                    twilio_handler=twilio_handler,
                    app_ws_send=app_ws_send,
                )

    def test_pipeline_stored_in_own_attr(self):
        """router._pipeline이 BasePipeline 서브클래스 인스턴스로 저장된다."""
        router = _make_router(CommunicationMode.VOICE_TO_VOICE)
        assert router._pipeline is not None
        assert isinstance(router._pipeline, BasePipeline)


# ---------------------------------------------------------------------------
# TestAudioRouterProxy: __getattr__/__setattr__ 프록시 + 위임 메서드 검증
# ---------------------------------------------------------------------------


class TestAudioRouterProxy:
    """__getattr__/__setattr__ 프록시와 명시적 위임 메서드를 검증."""

    def test_getattr_proxies_to_pipeline(self):
        """Pipeline 내부 속성(echo_gate 등)이 __getattr__를 통해 읽힌다."""
        router = _make_router(CommunicationMode.VOICE_TO_VOICE)
        # echo_gate는 VoiceToVoicePipeline에 존재하는 속성
        pipeline_echo_gate = router._pipeline.echo_gate
        assert router.echo_gate is pipeline_echo_gate

    def test_setattr_proxies_to_pipeline(self):
        """Pipeline 외부 속성을 설정하면 pipeline에 쓰인다."""
        router = _make_router(CommunicationMode.VOICE_TO_VOICE)
        mock_value = MagicMock()
        router.first_message = mock_value
        # Pipeline에 기록되었는지 확인
        assert router._pipeline.first_message is mock_value

    def test_own_attrs_not_proxied_on_set(self):
        """_OWN_ATTRS에 속하는 call은 AudioRouter 자체에 저장된다."""
        router = _make_router(CommunicationMode.VOICE_TO_VOICE)
        new_call = _make_call(call_id="replaced-call")
        router.call = new_call
        # AudioRouter.__dict__에 직접 저장 (Pipeline으로 프록시하지 않음)
        assert object.__getattribute__(router, "call") is new_call
        assert router.call.call_id == "replaced-call"

    @pytest.mark.asyncio
    async def test_start_delegates_to_pipeline(self):
        """router.start()가 pipeline.start()에 위임된다."""
        router = _make_router(CommunicationMode.VOICE_TO_VOICE)
        router._pipeline.start = AsyncMock()
        await router.start()
        router._pipeline.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_delegates_to_pipeline(self):
        """router.stop()이 pipeline.stop()에 위임된다."""
        router = _make_router(CommunicationMode.VOICE_TO_VOICE)
        router._pipeline.stop = AsyncMock()
        await router.stop()
        router._pipeline.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_methods_delegate(self):
        """handle_user_audio, handle_twilio_audio 등이 pipeline에 위임된다."""
        router = _make_router(CommunicationMode.VOICE_TO_VOICE)

        # Mock all pipeline handle methods
        router._pipeline.handle_user_audio = AsyncMock()
        router._pipeline.handle_user_audio_commit = AsyncMock()
        router._pipeline.handle_user_text = AsyncMock()
        router._pipeline.handle_twilio_audio = AsyncMock()
        router._pipeline.handle_typing_started = AsyncMock()

        await router.handle_user_audio("dGVzdA==")
        router._pipeline.handle_user_audio.assert_called_once_with("dGVzdA==")

        await router.handle_user_audio_commit()
        router._pipeline.handle_user_audio_commit.assert_called_once()

        await router.handle_user_text("hello")
        router._pipeline.handle_user_text.assert_called_once_with("hello")

        await router.handle_twilio_audio(b"\x80" * 100)
        router._pipeline.handle_twilio_audio.assert_called_once_with(b"\x80" * 100)

        await router.handle_typing_started()
        router._pipeline.handle_typing_started.assert_called_once()
