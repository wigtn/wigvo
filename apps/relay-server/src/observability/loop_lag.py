"""이벤트루프 지연(lag) 샘플러 — 부하테스트용 포화 측정.

단일 asyncio 이벤트루프가 밀리는지를 직접 측정한다. 백그라운드 태스크가
고정 간격으로 sleep하고 '실제 경과 - 목표 간격'을 lag으로 기록한다. 통화가
늘어 VAD/오디오 처리가 이벤트루프를 오래 붙잡으면 이 태스크가 제때 깨어나지
못해 lag이 커진다 → "동시 통화 N에서 단일 스레드가 얼마나 밀리는가"의 직접 지표.

부하테스트 모드에서만 lifespan에서 start()된다(프로덕션 오버헤드 없음).
"""

import asyncio
import resource
import time
from collections import deque


class LoopLagSampler:
    """이벤트루프 스케줄링 지연을 주기적으로 샘플링한다."""

    def __init__(self, interval_s: float = 0.05, window: int = 4000) -> None:
        self._interval_s = interval_s
        self._samples: deque[float] = deque(maxlen=window)  # ms 단위 lag
        self._task: asyncio.Task | None = None
        self._started_at: float = 0.0

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            expected = loop.time() + self._interval_s
            await asyncio.sleep(self._interval_s)
            lag_ms = max(0.0, (loop.time() - expected) * 1000.0)
            self._samples.append(lag_ms)

    def start(self) -> None:
        if self._task is None:
            self._started_at = time.monotonic()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def reset(self) -> None:
        self._samples.clear()
        self._started_at = time.monotonic()

    def stats(self) -> dict:
        s = sorted(self._samples)
        n = len(s)

        def pct(p: float) -> float:
            if n == 0:
                return 0.0
            idx = min(n - 1, int(round(p / 100.0 * (n - 1))))
            return round(s[idx], 2)

        ru = resource.getrusage(resource.RUSAGE_SELF)
        return {
            "loop_lag_ms": {
                "samples": n,
                "p50": pct(50),
                "p95": pct(95),
                "p99": pct(99),
                "max": round(s[-1], 2) if n else 0.0,
                "mean": round(sum(s) / n, 2) if n else 0.0,
            },
            # 누적 CPU 초 (user+sys). 하니스가 두 번 읽어 구간 CPU%를 계산한다.
            "cpu_seconds_total": round(ru.ru_utime + ru.ru_stime, 3),
            # ru_maxrss: Linux=KB, macOS=bytes (단위 주의 — 상대 추이로 해석).
            "rss_maxrss": ru.ru_maxrss,
            "uptime_s": round(time.monotonic() - self._started_at, 1),
        }


# 모듈 싱글톤 — main.py lifespan(load_test_mode)에서 start, 라우트에서 읽음.
sampler = LoopLagSampler()
