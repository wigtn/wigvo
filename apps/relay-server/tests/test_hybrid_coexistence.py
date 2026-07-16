"""WI-6 hybrid: inbound (web booth) and outbound (web call) share one relay.

The PoC goal is a hybrid relay where inbound and outbound calls coexist on a
single stateful server (FR-5.5, shared CapacityManager). The one hard coupling
between the two otherwise-independent paths is the process-global
``capacity_manager``: ``active + reserved <= max_concurrent_calls`` must hold
across *both* paths atomically, not per-path.

These tests drive the real inbound path (dispatch pickup -> real seam -> real
``bootstrap_inbound_media``) against a real outbound occupant, both contending
for the real CapacityManager. Only the DB and the OpenAI realtime stack are
faked. The outbound occupant is modeled exactly as ``routes/calls.py`` holds a
slot: ``capacity_manager.reserve`` then ``commit`` plus a registered session.
"""

from __future__ import annotations

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
from src.inbound.media import install_inbound_media_handlers, pending_media_registry
from src.inbound.models import DispatchRecord, DispatchState
from src.inbound.service import DispatchUnavailable, dispatch_service

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
INBOUND_ID = UUID("50000000-0000-0000-0000-000000000005")
USER_ID = UUID("20000000-0000-0000-0000-000000000002")
OUTBOUND_ID = "aa000000-0000-0000-0000-0000000000ff"  # web-outbound call, str key
PUBLIC_BASE = "https://relay.example.com"


class FakeWebSocket:
    async def accept(self, subprotocol: str | None = None) -> None:  # pragma: no cover
        pass

    async def send_json(self, message: dict) -> None:  # pragma: no cover
        pass

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        pass


def _record(state: DispatchState, claimed_by: UUID | None = None) -> DispatchRecord:
    now = datetime.now(timezone.utc)
    return DispatchRecord(
        call_id=INBOUND_ID,
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
async def hybrid_env(monkeypatch):
    monkeypatch.setattr(settings, "relay_server_url", PUBLIC_BASE)
    monkeypatch.setattr(settings, "load_test_mode", True)
    saved_bootstrap = bootstrap_module._bootstrapper
    saved_cleanup = bootstrap_module._cleanup

    async def _clear() -> None:
        for call_id in await pending_media_registry.call_ids():
            pending = await pending_media_registry.pop(call_id)
            if pending and pending.handler:
                await pending.handler.close()
        for key in (str(INBOUND_ID), OUTBOUND_ID):
            await capacity_manager.release(key)
            await capacity_manager.finish(key)
            call_manager._calls.pop(key, None)
            call_manager._sessions.pop(key, None)
            call_manager._routers.pop(key, None)

    await _clear()
    dispatch_service._known_calls.discard(INBOUND_ID)
    install_inbound_media_handlers()
    try:
        yield
    finally:
        with patch("src.inbound.service.dispatch_service.finish", new=AsyncMock()):
            if await pending_media_registry.contains(str(INBOUND_ID)):
                await media_module.cleanup_inbound_media(str(INBOUND_ID), "test_cleanup")
        await _clear()
        dispatch_service._known_calls.discard(INBOUND_ID)
        bootstrap_module._bootstrapper = saved_bootstrap
        bootstrap_module._cleanup = saved_cleanup


async def _occupy_outbound_slot() -> None:
    """Hold one capacity slot exactly as routes/calls.py does for a web call."""
    assert await capacity_manager.reserve(OUTBOUND_ID) is True
    assert await capacity_manager.commit(OUTBOUND_ID) is True
    call_manager.register_session(OUTBOUND_ID, MagicMock())


async def _prepare_inbound_ready(monkeypatch):
    await pending_media_registry.prepare(
        call_id=INBOUND_ID,
        tenant_id=TENANT_ID,
        languages=["ko", "en"],
        provider_call_sid="CA-inbound",
    )
    handler = await pending_media_registry.attach(str(INBOUND_ID), FakeWebSocket())
    assert handler is not None

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

    monkeypatch.setattr(
        repository,
        "claim_dispatch",
        AsyncMock(return_value=_record(DispatchState.CLAIMED, claimed_by=USER_ID)),
    )

    async def fake_transition(*, call_id, tenant_id, from_states, to_state, claimed_by=None):
        return _record(to_state, claimed_by=claimed_by)

    monkeypatch.setattr(repository, "transition_dispatch", fake_transition)
    monkeypatch.setattr(
        repository, "finish_dispatch", AsyncMock(return_value=_record(DispatchState.ENDED))
    )
    return dual


@pytest.mark.asyncio
async def test_outbound_occupancy_blocks_inbound_pickup_via_shared_capacity(monkeypatch):
    """max=1 held by an outbound call -> inbound pickup is refused at the shared cap."""
    monkeypatch.setattr(settings, "max_concurrent_calls", 1)
    await _occupy_outbound_slot()
    await _prepare_inbound_ready(monkeypatch)

    before = await capacity_manager.snapshot()
    assert before.active == 1 and before.occupied >= before.maximum

    with pytest.raises(DispatchUnavailable):
        await dispatch_service.pickup(
            call_id=INBOUND_ID, tenant_id=TENANT_ID, user_id=USER_ID
        )

    # Outbound slot survives; inbound left no reservation or session behind.
    after = await capacity_manager.snapshot()
    assert after.active == 1
    assert after.reserved == 0
    assert call_manager.get_session(OUTBOUND_ID) is not None
    assert call_manager.get_session(str(INBOUND_ID)) is None


@pytest.mark.asyncio
async def test_inbound_and_outbound_coexist_when_capacity_allows(monkeypatch):
    """max=2: an outbound occupant and an inbound pickup run side by side."""
    monkeypatch.setattr(settings, "max_concurrent_calls", 2)
    await _occupy_outbound_slot()
    inbound_dual = await _prepare_inbound_ready(monkeypatch)

    dispatch, result = await dispatch_service.pickup(
        call_id=INBOUND_ID, tenant_id=TENANT_ID, user_id=USER_ID
    )

    assert dispatch.state == DispatchState.CONNECTED
    assert result.relay_ws_url == f"wss://relay.example.com/relay/calls/{INBOUND_ID}/stream"

    # Both calls now hold live slots on the one shared relay.
    snapshot = await capacity_manager.snapshot()
    assert snapshot.active == 2
    assert snapshot.reserved == 0
    assert call_manager.get_session(OUTBOUND_ID) is not None
    assert call_manager.get_session(str(INBOUND_ID)) is inbound_dual
