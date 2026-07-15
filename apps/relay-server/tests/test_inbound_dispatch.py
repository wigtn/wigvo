"""WI-6 B dispatch state, atomic claim, and pickup authorization contracts."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

import src.auth as auth
import src.inbound.repository as repository
import src.inbound.service as service_module
from src.inbound.bootstrap import BootstrapResult
from src.inbound.models import DispatchRecord, DispatchState
from src.inbound.service import DispatchConflict, DispatchForbidden, InboundDispatchService

TENANT_A = UUID("10000000-0000-0000-0000-000000000001")
TENANT_B = UUID("20000000-0000-0000-0000-000000000002")
USER_A = UUID("30000000-0000-0000-0000-000000000003")
USER_B = UUID("40000000-0000-0000-0000-000000000004")
CALL_ID = UUID("50000000-0000-0000-0000-000000000005")


def make_dispatch(
    state: DispatchState,
    *,
    tenant_id: UUID = TENANT_A,
    claimed_by: UUID | None = None,
) -> DispatchRecord:
    now = datetime.now(timezone.utc)
    return DispatchRecord(
        call_id=CALL_ID,
        tenant_id=tenant_id,
        state=state,
        claimed_by=claimed_by,
        claim_expires_at=now + timedelta(seconds=30) if claimed_by else None,
        version=1,
        created_at=now,
        updated_at=now,
        languages=["ko", "en"],
    )


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_atomic_claim_is_one_conditional_update(monkeypatch):
    captured: dict[str, object] = {}

    class _Conn:
        async def fetchrow(self, query: str, *params):
            captured["query"] = query
            captured["params"] = params
            return make_dispatch(DispatchState.CLAIMED, claimed_by=USER_A).model_dump()

    async def fake_pool():
        return _Pool(_Conn())

    monkeypatch.setattr(repository, "get_pool", fake_pool)
    result = await repository.claim_dispatch(
        call_id=CALL_ID,
        tenant_id=TENANT_A,
        user_id=USER_A,
        claim_ttl_s=30,
    )

    query = str(captured["query"])
    assert result is not None and result.claimed_by == USER_A
    assert "UPDATE inbound_call_dispatch" in query
    assert "state = 'WAITING_FOR_AGENT'" in query
    assert "claimed_by IS NULL" in query
    assert "version = d.version + 1" in query
    assert captured["params"] == (CALL_ID, TENANT_A, USER_A, 30)


@pytest.mark.asyncio
async def test_waiting_list_is_tenant_scoped_fifo(monkeypatch):
    captured: dict[str, object] = {}

    class _Conn:
        async def fetch(self, query: str, *params):
            captured["query"] = query
            captured["params"] = params
            return []

    async def fake_pool():
        return _Pool(_Conn())

    monkeypatch.setattr(repository, "get_pool", fake_pool)
    assert await repository.list_waiting(TENANT_A) == []

    query = str(captured["query"])
    assert "d.tenant_id = $1" in query
    assert "d.state = 'WAITING_FOR_AGENT'" in query
    assert "ORDER BY d.created_at ASC, d.call_id ASC" in query
    assert captured["params"] == (TENANT_A, 50)


@pytest.mark.asyncio
async def test_two_agents_have_exactly_one_pickup_winner(monkeypatch):
    dispatch = make_dispatch(DispatchState.WAITING_FOR_AGENT)
    winner: UUID | None = None
    lock = asyncio.Lock()

    async def claim_dispatch(**kwargs):
        nonlocal winner, dispatch
        async with lock:
            if winner is not None:
                return None
            winner = kwargs["user_id"]
            dispatch = make_dispatch(DispatchState.CLAIMED, claimed_by=winner)
            return dispatch

    async def get_dispatch(_call_id):
        return dispatch

    async def transition_dispatch(*, to_state, **_kwargs):
        nonlocal dispatch
        dispatch = make_dispatch(to_state, claimed_by=winner)
        return dispatch

    async def bootstrap(_call_id, _tenant_id):
        await asyncio.sleep(0)
        return BootstrapResult(
            relay_ws_url="ws://relay/stream",
            source_language="ko",
            target_language="en",
        )

    monkeypatch.setattr(service_module, "media_handlers_registered", lambda: True)
    monkeypatch.setattr(repository, "claim_dispatch", claim_dispatch)
    monkeypatch.setattr(repository, "get_dispatch", get_dispatch)
    monkeypatch.setattr(repository, "transition_dispatch", transition_dispatch)
    monkeypatch.setattr(service_module, "bootstrap_inbound_session", bootstrap)

    service = InboundDispatchService()
    results = await asyncio.gather(
        service.pickup(call_id=CALL_ID, tenant_id=TENANT_A, user_id=USER_A),
        service.pickup(call_id=CALL_ID, tenant_id=TENANT_A, user_id=USER_B),
        return_exceptions=True,
    )

    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, DispatchConflict) for result in results) == 1
    assert dispatch.state == DispatchState.CONNECTED
    assert dispatch.claimed_by == winner


@pytest.mark.asyncio
async def test_same_agent_pickup_is_idempotent_when_connected(monkeypatch):
    connected = make_dispatch(DispatchState.CONNECTED, claimed_by=USER_A)
    monkeypatch.setattr(service_module, "media_handlers_registered", lambda: True)
    monkeypatch.setattr(repository, "claim_dispatch", AsyncMock(return_value=None))
    monkeypatch.setattr(repository, "get_dispatch", AsyncMock(return_value=connected))

    service = InboundDispatchService()
    row, result = await service.pickup(
        call_id=CALL_ID,
        tenant_id=TENANT_A,
        user_id=USER_A,
    )

    assert row == connected
    assert result.relay_ws_url.endswith(f"/relay/calls/{CALL_ID}/stream")


@pytest.mark.asyncio
async def test_cross_tenant_pickup_is_forbidden_not_conflict(monkeypatch):
    waiting = make_dispatch(DispatchState.WAITING_FOR_AGENT, tenant_id=TENANT_B)
    monkeypatch.setattr(service_module, "media_handlers_registered", lambda: True)
    monkeypatch.setattr(repository, "claim_dispatch", AsyncMock(return_value=None))
    monkeypatch.setattr(repository, "get_dispatch", AsyncMock(return_value=waiting))

    service = InboundDispatchService()
    with pytest.raises(DispatchForbidden):
        await service.pickup(
            call_id=CALL_ID,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )


def test_invalid_state_transition_is_rejected_before_query():
    with pytest.raises(ValueError, match="CONNECTED -> WAITING_FOR_AGENT"):
        asyncio.run(
            repository.transition_dispatch(
                call_id=CALL_ID,
                tenant_id=TENANT_A,
                from_states=[DispatchState.CONNECTED],
                to_state=DispatchState.WAITING_FOR_AGENT,
            )
        )


class _FakeWebSocket:
    def __init__(self, token: str, call_id: UUID = CALL_ID):
        self.headers = {
            "sec-websocket-protocol": f"{auth.PICKUP_WS_PROTOCOL}, {token}"
        }
        self.path_params = {"call_id": str(call_id)}


@pytest.mark.asyncio
async def test_pickup_websocket_revalidates_live_dispatch(monkeypatch):
    monkeypatch.setattr(auth.settings, "pickup_token_secret", "x" * 32)
    token = auth.issue_pickup_token(
        call_id=str(CALL_ID),
        tenant_id=TENANT_A,
        user_id=USER_A,
        role="agent",
    )
    authorize = AsyncMock(return_value=True)
    monkeypatch.setattr(service_module.dispatch_service, "authorize_pickup", authorize)

    context, protocol = await auth.authenticate_websocket(_FakeWebSocket(token))

    assert context.credential == "pickup"
    assert context.user_id == USER_A
    assert protocol == auth.PICKUP_WS_PROTOCOL
    authorize.assert_awaited_once_with(
        call_id=CALL_ID,
        tenant_id=TENANT_A,
        user_id=USER_A,
    )


@pytest.mark.asyncio
async def test_pickup_token_for_another_call_is_rejected(monkeypatch):
    monkeypatch.setattr(auth.settings, "pickup_token_secret", "x" * 32)
    token = auth.issue_pickup_token(
        call_id=str(CALL_ID),
        tenant_id=TENANT_A,
        user_id=USER_A,
        role="agent",
    )
    other_call = UUID("60000000-0000-0000-0000-000000000006")

    with pytest.raises(auth.AuthError, match="another call"):
        await auth.authenticate_websocket(_FakeWebSocket(token, other_call))


@pytest.mark.asyncio
async def test_agent_disconnect_has_reconnect_grace(monkeypatch):
    from src.call_manager import call_manager

    cleanup = AsyncMock()
    monkeypatch.setattr(service_module.settings, "inbound_reconnect_grace_s", 0.01)
    monkeypatch.setattr(call_manager, "get_app_ws", lambda _call_id: None)
    monkeypatch.setattr(call_manager, "cleanup_call", cleanup)

    service = InboundDispatchService()
    service.schedule_reconnect_cleanup(CALL_ID)
    await asyncio.sleep(0.03)

    cleanup.assert_awaited_once_with(str(CALL_ID), reason="app_reconnect_timeout")


@pytest.mark.asyncio
async def test_reconnect_cancels_pending_cleanup(monkeypatch):
    from src.call_manager import call_manager

    cleanup = AsyncMock()
    monkeypatch.setattr(service_module.settings, "inbound_reconnect_grace_s", 0.01)
    monkeypatch.setattr(call_manager, "cleanup_call", cleanup)

    service = InboundDispatchService()
    service.schedule_reconnect_cleanup(CALL_ID)
    service.cancel_reconnect_cleanup(CALL_ID)
    await asyncio.sleep(0.03)

    cleanup.assert_not_awaited()


def test_migration_keeps_dispatch_off_the_supabase_data_api():
    migration = (
        __import__("pathlib").Path(__file__).parents[1]
        / "migrations"
        / "004_inbound_call_dispatch.up.sql"
    ).read_text()

    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "REVOKE ALL ON TABLE inbound_call_dispatch FROM anon, authenticated" in migration
    assert "idx_inbound_dispatch_tenant_waiting_fifo" in migration
