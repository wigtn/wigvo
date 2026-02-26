"""Structured logging configuration for WIGVO Relay Server.

Cloud Run → JSON stdout (Cloud Logging auto-parsed by severity)
Local     → Color console + RotatingFileHandler (relay.log + error.log)
"""

import contextvars
import json
import logging
import os
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 비동기 컨텍스트 변수 — asyncio.create_task 시 자동 복사
call_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("call_id", default="")
call_mode_var: contextvars.ContextVar[str] = contextvars.ContextVar("call_mode", default="")


class CallContextFilter(logging.Filter):
    """LogRecord에 call_id와 call_mode를 자동 주입."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.call_id = call_id_var.get()  # type: ignore[attr-defined]
        record.call_mode = call_mode_var.get()  # type: ignore[attr-defined]
        return True


class CloudRunJsonFormatter(logging.Formatter):
    """Google Cloud Logging compatible JSON formatter.

    Outputs one JSON object per line with ``severity`` mapped from Python log levels.
    Cloud Logging parses ``severity`` automatically for filtering/alerting.
    """

    _LEVEL_TO_SEVERITY = {
        "DEBUG": "DEBUG",
        "INFO": "INFO",
        "WARNING": "WARNING",
        "ERROR": "ERROR",
        "CRITICAL": "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "severity": self._LEVEL_TO_SEVERITY.get(record.levelname, record.levelname),
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # CallContextFilter가 주입한 call_id/call_mode (빈 문자열이면 생략)
        call_id = getattr(record, "call_id", "")
        if call_id:
            payload["call_id"] = call_id
        call_mode = getattr(record, "call_mode", "")
        if call_mode:
            payload["mode"] = call_mode
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class ColorConsoleFormatter(logging.Formatter):
    """ANSI color console formatter for local development."""

    _COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[1;31m",  # bold red
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelname, self._RESET)
        timestamp = self.formatTime(record, self.datefmt)
        msg = record.getMessage()
        # CallContextFilter가 주입한 call_id/call_mode (있을 때만 표시)
        call_id = getattr(record, "call_id", "")
        call_mode = getattr(record, "call_mode", "")
        ctx = f" [{call_id}|{call_mode}]" if call_id else ""
        base = f"{timestamp} {color}[{record.levelname}]{self._RESET} {record.name}:{ctx} {msg}"
        if record.exc_info and record.exc_info[1] is not None:
            base += "\n" + "".join(traceback.format_exception(*record.exc_info))
        return base


def setup_logging(
    *,
    log_level: str = "INFO",
    log_dir: str = "logs",
    max_bytes: int = 10_485_760,
    backup_count: int = 5,
) -> None:
    """Configure root logger based on environment.

    Cloud Run detection: ``K_SERVICE`` env var is set automatically by Cloud Run.
    """
    is_cloud_run = "K_SERVICE" in os.environ
    level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    # Clear any pre-existing handlers (e.g. basicConfig defaults)
    root.handlers.clear()

    # CallContextFilter — 모든 LogRecord에 call_id/call_mode 자동 주입
    root.addFilter(CallContextFilter())

    if is_cloud_run:
        handler = logging.StreamHandler()
        handler.setFormatter(CloudRunJsonFormatter())
        root.addHandler(handler)
    else:
        # Color console
        console = logging.StreamHandler()
        console.setFormatter(ColorConsoleFormatter(datefmt="%Y-%m-%d %H:%M:%S"))
        root.addHandler(console)

        # File handlers
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_path / "relay.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(file_handler)

        error_handler = RotatingFileHandler(
            log_path / "error.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(error_handler)

    # Propagate uvicorn loggers through root so they use our formatters
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    # Suppress noisy third-party loggers (Cloud Run 로그 분석 효율화)
    for noisy in ("httpcore", "hpack", "httpx", "twilio.http_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
