"""PoC 운영 지표와 단일 Cloud Logging 알림 채널 (FR-5.1)."""

from __future__ import annotations

import asyncio
from collections import deque
import logging
import time

from src.capacity_manager import CapacitySnapshot
from src.config import settings

logger = logging.getLogger("wigvo.operations.alert")


class OperationalMetrics:
    def __init__(self) -> None:
        self.process_cpu_percent = 0.0
        self.openai_errors_total = 0
        self.capacity_rejections_total = 0
        self.alerts_total = 0
        self.last_alert: dict[str, object] | None = None
        self._openai_error_times: deque[float] = deque()
        self._last_alert_at: dict[str, float] = {}
        self._high_cpu_samples = 0
        self._last_wall = time.monotonic()
        self._last_cpu = time.process_time()
        self._task: asyncio.Task[None] | None = None

    def _prune_openai_errors(self, now: float) -> None:
        cutoff = now - settings.openai_error_window_s
        while self._openai_error_times and self._openai_error_times[0] < cutoff:
            self._openai_error_times.popleft()

    def _emit_alert(
        self,
        alert_type: str,
        *,
        value: float,
        threshold: float,
        force: bool = False,
    ) -> bool:
        now = time.monotonic()
        last = self._last_alert_at.get(alert_type, float("-inf"))
        if not force and now - last < settings.operations_alert_cooldown_s:
            return False
        self._last_alert_at[alert_type] = now
        self.alerts_total += 1
        self.last_alert = {
            "type": alert_type,
            "value": value,
            "threshold": threshold,
            "at_unix": round(time.time()),
        }
        logger.error(
            "OPERATIONAL_ALERT type=%s value=%s threshold=%s",
            alert_type,
            value,
            threshold,
            extra={
                "alert_type": alert_type,
                "alert_value": value,
                "alert_threshold": threshold,
            },
        )
        return True

    def emit_test_alert(self) -> None:
        """배포 후 Cloud Monitoring 알림 채널의 end-to-end 시험에 사용한다."""
        self._emit_alert("manual_test", value=1, threshold=1, force=True)

    def record_capacity_rejection(self, snapshot: CapacitySnapshot) -> None:
        self.capacity_rejections_total += 1
        self._emit_alert(
            "capacity_reached",
            value=snapshot.occupied,
            threshold=snapshot.maximum,
        )

    def record_openai_error(self, source: str) -> None:
        now = time.monotonic()
        self.openai_errors_total += 1
        self._openai_error_times.append(now)
        self._prune_openai_errors(now)
        count = len(self._openai_error_times)
        if count >= settings.openai_error_alert_threshold:
            self._emit_alert(
                "openai_errors",
                value=count,
                threshold=settings.openai_error_alert_threshold,
            )
        logger.warning("OpenAI error recorded source=%s total=%d", source, self.openai_errors_total)

    def sample_cpu(self) -> float:
        now_wall = time.monotonic()
        now_cpu = time.process_time()
        elapsed = now_wall - self._last_wall
        if elapsed > 0:
            self.process_cpu_percent = round(
                max(0.0, (now_cpu - self._last_cpu) / elapsed * 100),
                1,
            )
        self._last_wall = now_wall
        self._last_cpu = now_cpu

        if self.process_cpu_percent >= settings.cpu_alert_threshold_percent:
            self._high_cpu_samples += 1
            if self._high_cpu_samples >= settings.cpu_alert_consecutive_samples:
                self._emit_alert(
                    "high_cpu",
                    value=self.process_cpu_percent,
                    threshold=settings.cpu_alert_threshold_percent,
                )
        else:
            self._high_cpu_samples = 0
        return self.process_cpu_percent

    def snapshot(self) -> dict[str, object]:
        self._prune_openai_errors(time.monotonic())
        return {
            "process_cpu_percent": self.process_cpu_percent,
            "openai_errors_total": self.openai_errors_total,
            "openai_errors_window": len(self._openai_error_times),
            "capacity_rejections_total": self.capacity_rejections_total,
            "alerts_total": self.alerts_total,
            "last_alert": self.last_alert,
        }

    async def _sample_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(settings.operations_sample_interval_s)
                self.sample_cpu()
        except asyncio.CancelledError:
            return

    def start(self) -> None:
        if self._task is None:
            self._last_wall = time.monotonic()
            self._last_cpu = time.process_time()
            self._task = asyncio.create_task(self._sample_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await self._task
            self._task = None


operations = OperationalMetrics()
