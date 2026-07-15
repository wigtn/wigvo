"""Tests for structured logging: CallContextFilter, CloudRunJsonFormatter, ColorConsoleFormatter."""

import contextvars
import json
import logging
import os
from unittest.mock import patch

import pytest

from src.logging_config import (
    CallContextFilter,
    CloudRunJsonFormatter,
    ColorConsoleFormatter,
    call_id_var,
    call_mode_var,
    setup_logging,
    tenant_id_var,
)


@pytest.fixture(autouse=True)
def _reset_contextvars():
    """Reset contextvars before each test."""
    t1 = call_id_var.set("")
    t2 = call_mode_var.set("")
    t3 = tenant_id_var.set("")
    yield
    call_id_var.reset(t1)
    call_mode_var.reset(t2)
    tenant_id_var.reset(t3)


class TestCallContextFilter:
    def _make_record(self) -> logging.LogRecord:
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )

    def test_injects_empty_defaults(self):
        f = CallContextFilter()
        record = self._make_record()
        result = f.filter(record)
        assert result is True
        assert record.call_id == ""  # type: ignore[attr-defined]
        assert record.call_mode == ""  # type: ignore[attr-defined]
        assert record.tenant_id == ""  # type: ignore[attr-defined]

    def test_injects_set_values(self):
        call_id_var.set("call_abc")
        call_mode_var.set("voice_to_voice")
        tenant_id_var.set("tenant_a")
        f = CallContextFilter()
        record = self._make_record()
        f.filter(record)
        assert record.call_id == "call_abc"  # type: ignore[attr-defined]
        assert record.call_mode == "voice_to_voice"  # type: ignore[attr-defined]
        assert record.tenant_id == "tenant_a"  # type: ignore[attr-defined]

    def test_context_isolation(self):
        """Different contexts should have independent values."""
        call_id_var.set("call_1")

        def check_in_copy():
            call_id_var.set("call_2")
            f = CallContextFilter()
            record = self._make_record()
            f.filter(record)
            return record.call_id  # type: ignore[attr-defined]

        ctx = contextvars.copy_context()
        inner_value = ctx.run(check_in_copy)
        assert inner_value == "call_2"
        # Original context unchanged
        assert call_id_var.get() == "call_1"


class TestCloudRunJsonFormatter:
    def _format(
        self,
        *,
        call_id: str = "",
        call_mode: str = "",
        tenant_id: str = "",
        msg: str = "test",
    ) -> dict:
        call_id_var.set(call_id)
        call_mode_var.set(call_mode)
        tenant_id_var.set(tenant_id)
        formatter = CloudRunJsonFormatter()
        filt = CallContextFilter()
        record = logging.LogRecord(
            name="src.test", level=logging.INFO, pathname="test.py", lineno=42,
            msg=msg, args=(), exc_info=None,
        )
        record.funcName = "do_thing"
        filt.filter(record)
        return json.loads(formatter.format(record))

    def test_basic_fields(self):
        payload = self._format()
        assert payload["severity"] == "INFO"
        assert payload["message"] == "test"
        assert payload["logger"] == "src.test"
        assert payload["line"] == 42

    def test_omits_empty_call_id(self):
        payload = self._format()
        assert "call_id" not in payload
        assert "mode" not in payload

    def test_includes_call_id_when_set(self):
        payload = self._format(
            call_id="call_xyz",
            call_mode="text_to_voice",
            tenant_id="tenant_a",
        )
        assert payload["call_id"] == "call_xyz"
        assert payload["mode"] == "text_to_voice"
        assert payload["tenant_id"] == "tenant_a"

    def test_exception_serialization(self):
        call_id_var.set("call_err")
        formatter = CloudRunJsonFormatter()
        filt = CallContextFilter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="src.test", level=logging.ERROR, pathname="test.py", lineno=1,
                msg="failed", args=(), exc_info=sys.exc_info(),
            )
        filt.filter(record)
        payload = json.loads(formatter.format(record))
        assert "exception" in payload
        assert "ValueError: boom" in payload["exception"]
        assert payload["call_id"] == "call_err"

    def test_korean_message(self):
        payload = self._format(msg="안녕하세요 번역 완료")
        assert payload["message"] == "안녕하세요 번역 완료"


class TestColorConsoleFormatter:
    def _format(
        self, *, call_id: str = "", call_mode: str = "", tenant_id: str = ""
    ) -> str:
        call_id_var.set(call_id)
        call_mode_var.set(call_mode)
        tenant_id_var.set(tenant_id)
        formatter = ColorConsoleFormatter(datefmt="%H:%M:%S")
        filt = CallContextFilter()
        record = logging.LogRecord(
            name="src.test", level=logging.INFO, pathname="test.py", lineno=1,
            msg="hello world", args=(), exc_info=None,
        )
        filt.filter(record)
        return formatter.format(record)

    def test_no_context_no_bracket(self):
        output = self._format()
        assert "[|" not in output
        assert "src.test:" in output
        assert "hello world" in output

    def test_with_context_shows_bracket(self):
        output = self._format(
            call_id="call_123", call_mode="voice_to_voice", tenant_id="tenant_a"
        )
        assert "[tenant_a|call_123|voice_to_voice]" in output


class TestSetupLogging:
    def test_cloud_run_uses_json_formatter(self):
        with patch.dict(os.environ, {"K_SERVICE": "test-svc"}):
            setup_logging(log_level="INFO")
            root = logging.getLogger()
            assert len(root.handlers) == 1
            assert isinstance(root.handlers[0].formatter, CloudRunJsonFormatter)
            # CallContextFilter on handler (not root logger)
            assert any(isinstance(f, CallContextFilter) for f in root.handlers[0].filters)
        # Cleanup
        setup_logging(log_level="INFO")

    def test_local_uses_color_formatter(self):
        env = os.environ.copy()
        env.pop("K_SERVICE", None)
        with patch.dict(os.environ, env, clear=True):
            setup_logging(log_level="INFO", log_dir="/tmp/test_logging_wigvo")
            root = logging.getLogger()
            assert any(
                isinstance(h.formatter, ColorConsoleFormatter) for h in root.handlers
            )
            # CallContextFilter on handlers
            assert any(
                isinstance(f, CallContextFilter)
                for h in root.handlers
                for f in h.filters
            )
        # Cleanup
        setup_logging(log_level="INFO")

    def test_propagated_record_gets_call_id(self):
        """자식 로거에서 propagate된 레코드에도 call_id가 주입되는지 확인."""
        env = os.environ.copy()
        env.pop("K_SERVICE", None)
        with patch.dict(os.environ, env, clear=True):
            setup_logging(log_level="INFO", log_dir="/tmp/test_logging_wigvo")

            call_id_var.set("call_propagation_test")
            call_mode_var.set("voice_to_voice")

            child_logger = logging.getLogger("src.routes.test_child")
            # Child logger should propagate to root → handler filter injects call_id
            with patch.object(
                logging.getLogger().handlers[0], "emit", wraps=logging.getLogger().handlers[0].emit
            ) as mock_emit:
                child_logger.info("test message from child")
                assert mock_emit.called
                record = mock_emit.call_args[0][0]
                assert record.call_id == "call_propagation_test"
                assert record.call_mode == "voice_to_voice"
        # Cleanup
        setup_logging(log_level="INFO")
