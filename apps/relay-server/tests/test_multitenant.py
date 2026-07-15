"""WI-3 tenant propagation and isolation contracts."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

import src.db.pg_client as pg_client
from src.twilio.outbound import resolve_outbound_number
from src.types import CallStartRequest

TENANT_A = UUID("10000000-0000-0000-0000-000000000001")
TENANT_B = UUID("20000000-0000-0000-0000-000000000002")
CALL_ID = "30000000-0000-0000-0000-000000000003"


def test_call_start_requires_tenant_id():
    with pytest.raises(ValidationError):
        CallStartRequest(
            call_id=CALL_ID,
            phone_number="+821012345678",
            source_language="ko",
            target_language="en",
        )


def test_tenant_update_sql_scopes_by_record_and_tenant():
    sql, params = pg_client._build_tenant_update(
        "calls",
        pg_client._CALL_COLUMNS,
        CALL_ID,
        TENANT_A,
        {"status": "COMPLETED"},
    )

    assert "WHERE id = $3::uuid AND tenant_id = $4::uuid" in sql
    assert params[0] == "COMPLETED"
    assert params[-2:] == [UUID(CALL_ID), TENANT_A]
    assert str(TENANT_B) not in params


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


class _ConfigConn:
    def __init__(self, numbers: dict[str, str]):
        self.numbers = numbers
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def fetchval(self, query: str, *params):
        self.queries.append((query, params))
        return self.numbers.get(str(params[0]))


@pytest.mark.asyncio
async def test_outbound_number_is_tenant_scoped(monkeypatch):
    conn = _ConfigConn({str(TENANT_A): "+821011111111", str(TENANT_B): "+821022222222"})

    async def fake_pool():
        return _Pool(conn)

    monkeypatch.setattr(pg_client, "get_pool", fake_pool)

    assert await resolve_outbound_number(TENANT_A) == "+821011111111"
    assert await resolve_outbound_number(TENANT_B) == "+821022222222"
    assert conn.queries[0][1] == (TENANT_A,)
    assert conn.queries[1][1] == (TENANT_B,)


@pytest.mark.asyncio
async def test_missing_tenant_config_fails_closed(monkeypatch):
    conn = _ConfigConn({})

    async def fake_pool():
        return _Pool(conn)

    monkeypatch.setattr(pg_client, "get_pool", fake_pool)
    monkeypatch.setattr(
        pg_client,
        "settings",
        SimpleNamespace(twilio_phone_number=""),
    )

    with pytest.raises(LookupError, match="No outbound number configured"):
        await resolve_outbound_number(TENANT_A)
