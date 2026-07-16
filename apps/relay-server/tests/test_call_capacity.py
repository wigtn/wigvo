"""Outbound route capacity failure and cancellation cleanup."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException, Request

from src.auth import AuthContext
from src.capacity_manager import capacity_manager
from src.config import settings
from src.routes.calls import start_call
from src.types import CallStartRequest

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")


def _request(call_id: str) -> CallStartRequest:
    return CallStartRequest(
        call_id=call_id,
        tenant_id=TENANT_ID,
        phone_number="+821012345678",
        source_language="ko",
        target_language="en",
    )


def _http_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/relay/calls/start",
            "headers": [],
        }
    )


def _auth_patches():
    return (
        patch(
            "src.routes.calls.authenticate_http_request",
            new=AsyncMock(
                return_value=AuthContext(
                    verified=False,
                    credential="observe",
                    tenant_id=TENANT_ID,
                )
            ),
        ),
        patch("src.routes.calls.authorize_tenant", return_value=TENANT_ID),
    )


@pytest.mark.asyncio
async def test_openai_session_failure_releases_route_reservation():
    call_id = "capacity-openai-failure"
    session = MagicMock()
    session.connect = AsyncMock(side_effect=RuntimeError("openai unavailable"))
    session.close = AsyncMock()
    auth_patch, tenant_patch = _auth_patches()
    with (
        auth_patch,
        tenant_patch,
        patch("src.routes.calls.DualSessionManager", return_value=session),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await start_call(_request(call_id), _http_request())

    assert exc_info.value.status_code == 502
    assert call_id not in capacity_manager._reserved
    assert call_id not in capacity_manager._active


@pytest.mark.asyncio
async def test_cancelled_openai_session_start_releases_route_reservation():
    call_id = "capacity-openai-cancelled"
    session = MagicMock()
    session.connect = AsyncMock(side_effect=asyncio.CancelledError)
    session.close = AsyncMock()
    auth_patch, tenant_patch = _auth_patches()
    with (
        auth_patch,
        tenant_patch,
        patch("src.routes.calls.DualSessionManager", return_value=session),
    ):
        with pytest.raises(asyncio.CancelledError):
            await start_call(_request(call_id), _http_request())

    session.close.assert_awaited_once()
    assert call_id not in capacity_manager._reserved
    assert call_id not in capacity_manager._active


@pytest.mark.asyncio
async def test_capacity_503_reports_active_reserved_and_max(monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_calls", 1)
    assert await capacity_manager.reserve("existing") is True
    assert await capacity_manager.commit("existing") is True
    auth_patch, tenant_patch = _auth_patches()
    try:
        with auth_patch, tenant_patch:
            with pytest.raises(HTTPException) as exc_info:
                await start_call(_request("rejected"), _http_request())
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == {
            "error": "at_capacity",
            "active": 1,
            "reserved": 0,
            "occupied": 1,
            "max": 1,
            "message": "지금 통화가 가득 찼어요. 잠시 후 다시 시도해 주세요.",
        }
        assert (await capacity_manager.snapshot()).active == 1
    finally:
        await capacity_manager.finish("existing")
