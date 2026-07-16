"""Health Check 엔드포인트 (M-8)."""

import time

from fastapi import APIRouter

from src.call_manager import call_manager
from src.capacity_manager import capacity_manager
from src.config import settings
from src.observability.operations import operations

router = APIRouter(tags=["health"])

_start_time = time.time()


@router.get("/health")
async def health_check():
    capacity = await capacity_manager.snapshot()
    return {
        "status": "ok",
        "active_sessions": call_manager.active_call_count,
        "active_call_count": capacity.active,
        "reserved_call_count": capacity.reserved,
        "capacity": capacity.as_dict(),
        "operations": operations.snapshot(),
        "tenant_auth_enforced": settings.tenant_auth_enforce,
        "tenant_api_key_tenants": len(settings.tenant_api_key_hashes),
        "uptime": round(time.time() - _start_time),
    }
