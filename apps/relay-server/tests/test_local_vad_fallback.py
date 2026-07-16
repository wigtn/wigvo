"""Local VAD preflight 실패 시 Server VAD로 안전하게 내려가는 계약 테스트."""

from unittest.mock import patch

from src.realtime.sessions.session_manager import DualSessionManager
from src.types import CallMode, CommunicationMode, VadMode


def test_dual_session_uses_server_vad_when_local_model_is_unavailable():
    with (
        patch("src.realtime.sessions.session_manager.settings") as mock_settings,
        patch(
            "src.realtime.sessions.session_manager.is_local_vad_available",
            return_value=False,
        ),
    ):
        mock_settings.local_vad_enabled = True
        sessions = DualSessionManager(
            mode=CallMode.RELAY,
            source_language="ko",
            target_language="en",
            communication_mode=CommunicationMode.VOICE_TO_VOICE,
        )

    assert sessions.local_vad_enabled is False
    assert sessions.session_b.config.vad_mode == VadMode.SERVER


def test_dual_session_uses_local_vad_after_successful_preflight():
    with (
        patch("src.realtime.sessions.session_manager.settings") as mock_settings,
        patch(
            "src.realtime.sessions.session_manager.is_local_vad_available",
            return_value=True,
        ),
    ):
        mock_settings.local_vad_enabled = True
        sessions = DualSessionManager(
            mode=CallMode.RELAY,
            source_language="ko",
            target_language="en",
            communication_mode=CommunicationMode.VOICE_TO_VOICE,
        )

    assert sessions.local_vad_enabled is True
    assert sessions.session_b.config.vad_mode == VadMode.LOCAL
