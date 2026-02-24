"""STT model configuration tests (gpt-4o-transcribe 전환)."""

import os
from unittest.mock import patch

from src.config import Settings


def test_stt_model_default():
    """기본 STT 모델은 gpt-4o-transcribe."""
    s = Settings()
    assert s.stt_model == "gpt-4o-transcribe"


def test_stt_model_env_override():
    """STT_MODEL 환경변수로 오버라이드 가능."""
    with patch.dict(os.environ, {"STT_MODEL": "whisper-1"}):
        s = Settings()
        assert s.stt_model == "whisper-1"


def test_stt_model_mini_transcribe():
    """gpt-4o-mini-transcribe도 지정 가능."""
    with patch.dict(os.environ, {"STT_MODEL": "gpt-4o-mini-transcribe"}):
        s = Settings()
        assert s.stt_model == "gpt-4o-mini-transcribe"


def test_whisper_model_unchanged():
    """기존 whisper_model (Degraded Mode)은 영향 없음."""
    s = Settings()
    assert s.whisper_model == "whisper-1"
