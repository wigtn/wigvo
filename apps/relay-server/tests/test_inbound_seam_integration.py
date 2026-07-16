"""WI-6 A<->B seam integration.

Every other inbound test mocks one side of the ``bootstrap_inbound_session``
seam: ``test_inbound_dispatch`` fakes A's bootstrapper, ``test_inbound_media``
calls A directly and never goes through B's dispatch state machine. Nothing
exercises B's real ``dispatch_service.pickup`` driving A's real
``bootstrap_inbound_media`` through the actual registered seam.

These tests cross that seam for real. Only the DB (repository) and the OpenAI
realtime stack (DualSessionManager/AudioRouter) are faked; the seam registry,
A's bootstrap, the capacity manager, the call manager, and the pending media
registry are all the production singletons.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import pytest_asyncio

from src.call_manager import call_manager
from src.capacity_manager import capacity_manager
from src.config import settings
import src.inbound.bootstrap as bootstrap_module
import src.inbound.media as media_module
from src.inbound import repository
from src.inbound.media import (
    install_inbound_media_handlers,
    pending_media_registry,
)
from src.inbound.models import DispatchRecord, DispatchState
from src.inbound.service import (
    DispatchUnavailable,
    dispatch_service,
)

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
CALL_ID = UUID("50000000-0000-0000-0000-000000000005")
USER_ID = UUID("20000000-0000-0000-0000-000000000002")
PUBLIC_BASE = "https://relay.example.com"


class FakeWebSocket:
    """Minimal Twilio media WebSocket — attach()/handoff() never read it here."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.close_calls: list[tuple[int, str | None]] = []

    async def accept(self, subprotocol: str | None = None) -> None:  # pragma: no cover
        pass

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_calls.append((code, reason))


def _record(state: DispatchState, claimed_by: UUID | None = None) -> DispatchRecord:
    now = datetime.now(timezone.utc)
    return DispatchRecord(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        provider_call_sid="CA-inbound",
        state=state,
        claimed_by=claimed_by,
        version=1,
        created_at=now,
        updated_at=now,
        languages=["ko", "en"],
    )


@pytest_asyncio.fixture(autouse=True)
async def real_seam_env(monkeypatch):
    """Register A's real handlers and reset every shared singleton around the test."""
    monkeypatch.setattr(settings, "relay_server_url", PUBLIC_BASE)
    monkeypatch.setattr(settings, "load_test_mode", True)

    # Preserve and restore the process-global seam registry so we do not leak
    # A's handlers into tests that assume an unregistered seam.
    saved_bootstrap = bootstrap_module._bootstrapper
    saved_cleanup = bootstrap_module._cleanup

    async def _clear_shared_state() -> None:
        for call_id in await pending_media_registry.call_ids():
            pending = await pending_media_registry.pop(call_id)
            if pending and pending.handler:
                await pending.handler.close()
        await capacity_manager.release(str(CALL_ID))
        await capacity_manager.finish(str(CALL_ID))
        call_manager._calls.pop(str(CALL_ID), None)
        call_manager._sessions.pop(str(CALL_ID), None)
        call_manager._routers.pop(str(CALL_ID), None)

    await _clear_shared_state()
    dispatch_service._known_calls.discard(CALL_ID)
    install_inbound_media_handlers()  # real A registration — the seam under test
    try:
        yield
    finally:
        with patch("src.inbound.service.dispatch_service.finish", new=AsyncMock()):
            if await pending_media_registry.contains(str(CALL_ID)):
                await media_module.cleanup_inbound_media(str(CALL_ID), "test_cleanup")
        await _clear_shared_state()
        dispatch_service._known_calls.discard(CALL_ID)
        bootstrap_module._bootstrapper = saved_bootstrap
        bootstrap_module._cleanup = saved_cleanup


async def _prepare_pending_media() -> None:
    """Put A into the WAITING state: pending media attached, AI stack faked."""
    await pending_media_registry.prepare(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        languages=["ko", "en"],
        provider_call_sid="CA-inbound",
    )
    handler = await pending_media_registry.attach(str(CALL_ID), FakeWebSocket())
    assert handler is not None


def _fake_ai_stack(monkeypatch):
    dual = MagicMock()
    dual.session_a = SimpleNamespace(session_id="sess-a")
    dual.session_b = SimpleNamespace(session_id="sess-b")
    dual.connect = AsyncMock()
    dual.close = AsyncMock()
    dual.listen_all = AsyncMock()
    router = MagicMock()
    router.start = AsyncMock()
    router.stop = AsyncMock()
    router.handle_twilio_audio = AsyncMock()
    monkeypatch.setattr(media_module, "DualSessionManager", MagicMock(return_value=dual))
    monkeypatch.setattr(media_module, "AudioRouter", MagicMock(return_value=router))
    return dual, router


@pytest.mark.asyncio
async def test_pickup_drives_real_bootstrap_through_registered_seam(monkeypatch):
    """B.pickup -> real _bootstrapper -> A.bootstrap_inbound_media, no seam mock."""
    await _prepare_pending_media()
    dual, router = _fake_ai_stack(monkeypatch)

    # Fake only the DB boundary. bootstrap_inbound_session is NOT patched, so the
    # seam and A's bootstrap run for real.
    monkeypatch.setattr(
        repository,
        "claim_dispatch",
        AsyncMock(return_value=_record(DispatchState.CLAIMED, claimed_by=USER_ID)),
    )

    async def fake_transition(*, call_id, tenant_id, from_states, to_state, claimed_by=None):
        return _record(to_state, claimed_by=claimed_by)

    monkeypatch.setattr(repository, "transition_dispatch", fake_transition)

    # Guard rail: the seam really is A's production bootstrapper, un-mocked.
    assert bootstrap_module.media_handlers_registered() is True
    assert bootstrap_module._bootstrapper is media_module.bootstrap_inbound_media

    dispatch, result = await dispatch_service.pickup(
        call_id=CALL_ID, tenant_id=TENANT_ID, user_id=USER_ID
    )

    # B's half: dispatch reached CONNECTED.
    assert dispatch.state == DispatchState.CONNECTED
    assert dispatch.claimed_by == USER_ID

    # A's half: the BootstrapResult is the one A actually built (URL + call_mode
    # + communication_mode come from A, not from B's _connected_result fallback).
    assert result.relay_ws_url == f"wss://relay.example.com/relay/calls/{CALL_ID}/stream"
    assert result.role == "agent"
    assert result.source_language == "ko"
    assert result.target_language == "en"
    assert result.call_mode == "relay"
    assert result.communication_mode == "voice_to_voice"

    # A's side effects: media handed off, session/router registered, capacity committed.
    handler = await pending_media_registry.get_handler(str(CALL_ID))
    assert handler is not None and handler.handed_off is True
    assert call_manager.get_session(str(CALL_ID)) is dual
    assert call_manager.get_router(str(CALL_ID)) is router
    dual.connect.assert_awaited_once()
    snapshot = await capacity_manager.snapshot()
    assert snapshot.active == 1
    assert snapshot.reserved == 0


@pytest.mark.asyncio
async def test_pickup_maps_real_capacity_failure_across_seam_to_503(monkeypatch):
    """A raising BootstrapUnavailable (capacity) surfaces as B's DispatchUnavailable."""
    await _prepare_pending_media()
    _fake_ai_stack(monkeypatch)

    # Real A checks capacity_manager.reserve; force it to refuse so A raises
    # BootstrapUnavailable from inside the real bootstrap, across the real seam.
    monkeypatch.setattr(
        media_module.capacity_manager, "reserve", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        repository,
        "claim_dispatch",
        AsyncMock(return_value=_record(DispatchState.CLAIMED, claimed_by=USER_ID)),
    )

    async def fake_transition(*, call_id, tenant_id, from_states, to_state, claimed_by=None):
        return _record(to_state, claimed_by=claimed_by)

    monkeypatch.setattr(repository, "transition_dispatch", fake_transition)
    finish = AsyncMock(return_value=_record(DispatchState.ENDED))
    monkeypatch.setattr(repository, "finish_dispatch", finish)

    with pytest.raises(DispatchUnavailable):
        await dispatch_service.pickup(
            call_id=CALL_ID, tenant_id=TENANT_ID, user_id=USER_ID
        )

    # Single cleanup ran and nothing leaked into active/reserved capacity.
    finish.assert_awaited()
    assert call_manager.get_session(str(CALL_ID)) is None
    snapshot = await capacity_manager.snapshot()
    assert snapshot.active == 0
    assert snapshot.reserved == 0
