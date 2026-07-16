"""부하테스트 관측 엔드포인트 (load_test_mode에서만 의미 있음).

GET  /loadtest/stats  — 이벤트루프 lag(p50/p95/p99/max) + CPU + 활성 통화 수
POST /loadtest/reset  — 샘플 윈도우 초기화 (부하 단계 전환 시)
"""

from functools import lru_cache

from fastapi import APIRouter, HTTPException

from src.call_manager import call_manager
from src.config import settings
from src.observability.loop_lag import sampler

router = APIRouter(tags=["loadtest"])


def _require_load_test_mode() -> None:
    """운영 환경에서는 프로세스 지표와 reset 제어면을 노출하지 않는다."""
    if not settings.load_test_mode:
        raise HTTPException(status_code=404, detail="Not found")


@lru_cache(maxsize=1)
def _local_vad_runtime_ready() -> bool:
    """하네스가 실제 Silero hot path를 재는지 한 번만 preflight한다."""
    if not settings.local_vad_enabled:
        return False
    from src.realtime.local_vad import LocalVAD

    return LocalVAD()._model is not None


@router.get("/stats")
async def loadtest_stats() -> dict:
    _require_load_test_mode()
    return {
        "load_test_mode": settings.load_test_mode,
        "active_calls": call_manager.active_call_count,
        "max_concurrent_calls": settings.max_concurrent_calls,
        "local_vad_enabled": settings.local_vad_enabled,
        "local_vad_runtime_ready": _local_vad_runtime_ready(),
        **sampler.stats(),
    }


@router.post("/reset")
async def loadtest_reset() -> dict:
    _require_load_test_mode()
    sampler.reset()
    return {"status": "reset"}
