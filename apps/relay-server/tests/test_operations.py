"""WI-5 health metrics and Cloud Logging alert emission."""

import json
import logging
from types import SimpleNamespace

import pytest

from src.config import settings
from src.logging_config import CallContextFilter, CloudRunJsonFormatter
from src.observability.operations import OperationalMetrics
from src.routes.health import health_check


def test_openai_error_threshold_emits_structured_alert(monkeypatch, caplog):
    monkeypatch.setattr(settings, "openai_error_alert_threshold", 2)
    monkeypatch.setattr(settings, "openai_error_window_s", 60.0)
    monkeypatch.setattr(settings, "operations_alert_cooldown_s", 300.0)
    metrics = OperationalMetrics()

    with caplog.at_level(logging.ERROR, logger="wigvo.operations.alert"):
        metrics.record_openai_error("test-a")
        metrics.record_openai_error("test-b")

    assert metrics.snapshot()["openai_errors_window"] == 2
    assert metrics.alerts_total == 1
    record = next(record for record in caplog.records if record.name == "wigvo.operations.alert")
    assert record.alert_type == "openai_errors"
    assert record.alert_value == 2


def test_manual_alert_is_serialized_for_cloud_logging():
    metrics = OperationalMetrics()
    record = logging.LogRecord(
        "wigvo.operations.alert",
        logging.ERROR,
        __file__,
        1,
        "test",
        (),
        None,
    )
    record.alert_type = "manual_test"
    record.alert_value = 1
    record.alert_threshold = 1
    CallContextFilter().filter(record)

    payload = json.loads(CloudRunJsonFormatter().format(record))
    assert payload["severity"] == "ERROR"
    assert payload["alert_type"] == "manual_test"
    assert payload["alert_value"] == 1


def test_cpu_alert_requires_consecutive_samples(monkeypatch, caplog):
    import src.observability.operations as operations_module

    clock = {"wall": 0.0, "cpu": 0.0}
    monkeypatch.setattr(
        operations_module,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock["wall"],
            process_time=lambda: clock["cpu"],
            time=lambda: 1_700_000_000.0,
        ),
    )
    monkeypatch.setattr(settings, "cpu_alert_threshold_percent", 85.0)
    monkeypatch.setattr(settings, "cpu_alert_consecutive_samples", 2)
    monkeypatch.setattr(settings, "operations_alert_cooldown_s", 300.0)
    metrics = OperationalMetrics()

    with caplog.at_level(logging.ERROR, logger="wigvo.operations.alert"):
        clock.update(wall=1.0, cpu=0.9)
        assert metrics.sample_cpu() == 90.0
        assert metrics.alerts_total == 0
        clock.update(wall=2.0, cpu=1.8)
        assert metrics.sample_cpu() == 90.0

    assert metrics.alerts_total == 1
    assert metrics.last_alert and metrics.last_alert["type"] == "high_cpu"


@pytest.mark.asyncio
async def test_health_exposes_capacity_cpu_and_error_counters():
    health = await health_check()

    assert "active_call_count" in health
    assert "reserved_call_count" in health
    assert health["capacity"]["occupied"] <= health["capacity"]["maximum"]
    assert "process_cpu_percent" in health["operations"]
    assert "openai_errors_total" in health["operations"]
    assert "capacity_rejections_total" in health["operations"]


def test_operational_threshold_environment_names(monkeypatch):
    from src.config import Settings

    monkeypatch.setenv("CPU_ALERT_THRESHOLD_PERCENT", "90")
    monkeypatch.setenv("OPENAI_ERROR_ALERT_THRESHOLD", "7")
    configured = Settings(_env_file=None)

    assert configured.cpu_alert_threshold_percent == 90
    assert configured.openai_error_alert_threshold == 7
