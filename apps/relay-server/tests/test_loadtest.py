"""WI-1 load-test control plane and configuration tests."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.config import Settings
from src.routes.loadtest import loadtest_reset, loadtest_stats, sampler


@pytest.mark.asyncio
async def test_loadtest_endpoints_are_hidden_when_mode_is_off():
    with patch("src.routes.loadtest.settings") as mock_settings:
        mock_settings.load_test_mode = False
        with pytest.raises(HTTPException) as exc_info:
            await loadtest_stats()

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_loadtest_stats_and_reset_are_available_when_mode_is_on():
    sampler._samples.extend([1.0, 2.0, 3.0])
    with (
        patch("src.routes.loadtest.settings") as mock_settings,
        patch("src.routes.loadtest._local_vad_runtime_ready", return_value=True),
    ):
        mock_settings.load_test_mode = True
        mock_settings.max_concurrent_calls = 42
        mock_settings.local_vad_enabled = True
        stats = await loadtest_stats()
        reset = await loadtest_reset()

    assert stats["load_test_mode"] is True
    assert stats["max_concurrent_calls"] == 42
    assert stats["local_vad_runtime_ready"] is True
    assert stats["loop_lag_ms"]["samples"] == 3
    assert reset == {"status": "reset"}
    assert sampler.stats()["loop_lag_ms"]["samples"] == 0


def test_load_test_mode_uses_documented_environment_name(monkeypatch):
    monkeypatch.setenv("LOAD_TEST_MODE", "true")

    assert Settings(_env_file=None).load_test_mode is True
