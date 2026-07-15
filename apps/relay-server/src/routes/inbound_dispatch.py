import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from src.auth import (
    AuthError,
    authenticate_http_request,
    issue_pickup_token,
)
from src.config import settings
from src.inbound.models import InboundCallListResponse, PickupResponse
from src.inbound.service import DispatchError, dispatch_service

router = APIRouter(prefix="/inbound", tags=["inbound-dispatch"])
logger = logging.getLogger(__name__)


async def _require_agent(request: Request) -> tuple[UUID, UUID]:
    try:
        auth = await authenticate_http_request(request)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if (
        not auth.verified
        or auth.credential != "user_jwt"
        or auth.tenant_id is None
        or auth.user_id is None
    ):
        raise HTTPException(status_code=401, detail="Authenticated tenant agent required")
    return auth.tenant_id, auth.user_id


@router.get("/calls", response_model=InboundCallListResponse)
async def list_inbound_calls(request: Request) -> InboundCallListResponse:
    tenant_id, _user_id = await _require_agent(request)
    calls = await dispatch_service.list_waiting(tenant_id)
    return InboundCallListResponse(calls=calls)


@router.post("/calls/{call_id}/pickup", response_model=PickupResponse)
async def pickup_inbound_call(call_id: UUID, request: Request) -> PickupResponse:
    tenant_id, user_id = await _require_agent(request)
    if not settings.pickup_token_secret:
        raise HTTPException(status_code=503, detail="Pickup token signing is not configured")
    try:
        dispatch, bootstrap = await dispatch_service.pickup(
            call_id=call_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    except DispatchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    token = issue_pickup_token(
        call_id=str(dispatch.call_id),
        tenant_id=dispatch.tenant_id,
        user_id=user_id,
        role=bootstrap.role,
    )
    return PickupResponse(
        call_id=dispatch.call_id,
        state=dispatch.state,
        relay_ws_url=bootstrap.relay_ws_url,
        pickup_token=token,
        role=bootstrap.role,
        source_language=bootstrap.source_language,
        target_language=bootstrap.target_language,
        communication_mode=bootstrap.communication_mode,
        call_mode=bootstrap.call_mode,
    )
