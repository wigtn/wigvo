"""부하테스트 관측 엔드포인트 (load_test_mode에서만 의미 있음).

GET  /loadtest/stats  — 이벤트루프 lag(p50/p95/p99/max) + CPU + 활성 통화 수
POST /loadtest/reset  — 샘플 윈도우 초기화 (부하 단계 전환 시)
"""

from fastapi import APIRouter

from src.call_manager import call_manager
from src.config import settings
from src.observability.loop_lag import sampler

router = APIRouter(tags=["loadtest"])


@router.get("/stats")
async def loadtest_stats() -> dict:
    return {
        "load_test_mode": settings.load_test_mode,
        "active_calls": call_manager.active_call_count,
        "max_concurrent_calls": settings.max_concurrent_calls,
        **sampler.stats(),
    }


@router.post("/reset")
async def loadtest_reset() -> dict:
    sampler.reset()
    return {"status": "reset"}
