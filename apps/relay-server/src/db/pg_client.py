"""Local Postgres client — replaces the former Supabase Python SDK usage.

The relay server only writes to the `calls` and `conversations` tables; the
former PostgREST-style `client.table("calls").update({...}).eq("id", id).execute()`
calls become two helpers:

- update_call(call_id, tenant_id, **fields)
- update_conversation(conversation_id, tenant_id, **fields)

The high-level persist_call() / update_call_field() helpers keep their names
and shapes so callers don't need wholesale refactors.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from typing import Any
from uuid import UUID

import asyncpg

from src.config import settings
from src.types import ActiveCall, CallStatus, CALL_RESULT_MAP

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()

# Whitelist of columns the relay server is allowed to update. Prevents
# typos and bad data from corrupting unrelated columns.
_CALL_COLUMNS: dict[str, str] = {
    "status": "text",
    "result": "text",
    "summary": "text",
    "call_sid": "text",
    "call_mode": "text",
    "source_language": "text",
    "target_language": "text",
    "target_name": "text",
    "target_phone": "text",
    "communication_mode": "text",
    "transcript_bilingual": "jsonb",
    "cost_tokens": "jsonb",
    "guardrail_events": "jsonb",
    "recovery_events": "jsonb",
    "function_call_logs": "jsonb",
    "call_result": "text",
    "call_result_data": "jsonb",
    "auto_ended": "bool",
    "duration_s": "real",
    "total_tokens": "int",
    "completed_at": "timestamptz",
    "relay_ws_url": "text",
}

_CONV_COLUMNS: dict[str, str] = {
    "status": "text",
    "collected_data": "jsonb",
}


def _as_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(value)


def _is_jsonb(col: str) -> bool:
    return _CALL_COLUMNS.get(col) == "jsonb" or _CONV_COLUMNS.get(col) == "jsonb"


async def _init_codec(conn: asyncpg.Connection) -> None:
    # asyncpg returns jsonb as text by default; install JSON codecs so we
    # can pass/receive dicts directly.
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: json.dumps(v, default=str),
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=lambda v: json.dumps(v, default=str),
        decoder=json.loads,
        schema="pg_catalog",
    )


async def get_pool() -> asyncpg.Pool:
    """Lazy-init shared asyncpg pool. Idempotent."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            if not settings.database_url:
                raise RuntimeError("DATABASE_URL is not set")
            _pool = await asyncpg.create_pool(
                dsn=settings.database_url,
                min_size=settings.db_pool_min_size,
                max_size=settings.db_pool_max_size,
                init=_init_codec,
            )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _build_tenant_update(
    table: str,
    columns: dict[str, str],
    record_id: str,
    tenant_id: UUID | str,
    fields: dict[str, Any],
) -> tuple[str, list[Any]]:
    """Build an update that cannot cross a tenant boundary."""
    set_parts: list[str] = []
    values: list[Any] = []
    for col, val in fields.items():
        if col not in columns:
            logger.warning("Ignoring unknown column %s.%s", table, col)
            continue
        cast = "::uuid" if columns[col] == "uuid" else ""
        set_parts.append(f"{col} = ${len(values) + 1}{cast}")
        values.append(val)
    if not set_parts:
        return "", values
    set_parts.append(f"updated_at = ${len(values) + 1}")
    values.append(datetime.datetime.now(datetime.timezone.utc))
    placeholder_id = f"${len(values) + 1}::uuid"
    placeholder_tenant = f"${len(values) + 2}::uuid"
    sql = (
        f"UPDATE {table} SET {', '.join(set_parts)} "
        f"WHERE id = {placeholder_id} AND tenant_id = {placeholder_tenant}"
    )
    values.extend([_as_uuid(record_id), _as_uuid(tenant_id)])
    return sql, values


async def update_call(call_id: str, tenant_id: UUID | str, **fields: Any) -> int:
    """Update a call only when both id and tenant_id match (fail-closed)."""
    if not fields:
        return 0
    sql, params = _build_tenant_update(
        "calls", _CALL_COLUMNS, call_id, tenant_id, fields
    )
    if not sql:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(sql, *params)
    # asyncpg returns "UPDATE <n>"
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


async def update_conversation(
    conversation_id: str, tenant_id: UUID | str, **fields: Any
) -> int:
    if not fields:
        return 0
    sql, params = _build_tenant_update(
        "conversations",
        _CONV_COLUMNS,
        conversation_id,
        tenant_id,
        fields,
    )
    if not sql:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(sql, *params)
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


async def fetch_call_conversation_id(
    call_id: str, tenant_id: UUID | str
) -> str | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT conversation_id FROM calls WHERE id = $1::uuid AND tenant_id = $2::uuid",
            _as_uuid(call_id),
            _as_uuid(tenant_id),
        )


async def get_user_tenant_id(user_id: UUID | str) -> UUID | None:
    """Resolve active WIGVO membership for a verified WIGTN-SSO user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT tenant_id
            FROM users
            WHERE id = $1::uuid AND deleted_at IS NULL
            """,
            _as_uuid(user_id),
        )
    return _as_uuid(value) if value is not None else None


async def get_tenant_outbound_number(tenant_id: UUID | str) -> str:
    """Resolve tenant telephony config; missing/blank config is rejected."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        number = await conn.fetchval(
            """
            SELECT outbound_number
            FROM tenant_call_config
            WHERE tenant_id = $1::uuid AND provider = 'twilio'
            """,
            _as_uuid(tenant_id),
        )
        # Preserve existing single-tenant behavior during rollout without
        # hard-coding a phone number into migration history. The legacy value
        # is copied into the default tenant config once, then DB config wins.
        default_tenant = "00000000-0000-0000-0000-000000000001"
        if (
            not number
            and str(tenant_id) == default_tenant
            and settings.twilio_phone_number
        ):
            number = await conn.fetchval(
                """
                UPDATE tenant_call_config
                SET outbound_number = $2, updated_at = now()
                WHERE tenant_id = $1::uuid AND outbound_number = ''
                RETURNING outbound_number
                """,
                UUID(default_tenant),
                settings.twilio_phone_number,
            )
    if not number:
        raise LookupError(f"No outbound number configured for tenant {tenant_id}")
    return str(number)


async def persist_call(call: ActiveCall) -> None:
    """End-of-call snapshot. Mirrors the former Supabase persist_call.

    Updates the existing calls row identified by call.call_id and marks
    the linked conversation COMPLETED.
    """
    db_status = "COMPLETED" if call.status == CallStatus.ENDED else "FAILED"
    if call.call_result:
        db_result = CALL_RESULT_MAP.get(call.call_result, "ERROR")
    else:
        db_result = "SUCCESS" if call.status == CallStatus.ENDED else "ERROR"

    data: dict[str, Any] = {
        "call_sid": call.call_sid,
        "call_mode": call.mode.value,
        "source_language": call.source_language,
        "target_language": call.target_language,
        "target_name": call.collected_data.get("target_name") or None,
        "target_phone": call.collected_data.get("target_phone") or None,
        "status": db_status,
        "result": db_result,
        "completed_at": datetime.datetime.now(datetime.timezone.utc),
        "communication_mode": call.communication_mode.value if call.communication_mode else None,
        "transcript_bilingual": [
            t.model_dump() if hasattr(t, "model_dump") else t for t in call.transcript_bilingual
        ],
        "cost_tokens": call.cost_tokens.model_dump(),
        "guardrail_events": call.guardrail_events_log,
        "recovery_events": [
            e.model_dump() if hasattr(e, "model_dump") else e for e in call.recovery_events
        ],
        "call_result": call.call_result,
        "call_result_data": call.call_result_data,
        "function_call_logs": call.function_call_logs,
        "duration_s": round(time.time() - call.started_at, 1) if call.started_at > 0 else None,
        "total_tokens": call.cost_tokens.total,
    }

    try:
        await update_call(call.call_id, call.tenant_id, **data)
        logger.info("Call %s persisted to DB (status=%s)", call.call_id, db_status)
    except Exception:
        logger.exception("Failed to persist call %s", call.call_id)
        return

    try:
        conv_id = await fetch_call_conversation_id(call.call_id, call.tenant_id)
        if conv_id:
            await update_conversation(conv_id, call.tenant_id, status="COMPLETED")
            logger.info("Conversation %s status updated to COMPLETED", conv_id)
    except Exception:
        logger.warning(
            "Failed to update conversation status for call %s",
            call.call_id,
            exc_info=True,
        )


async def update_call_field(
    call_id: str, tenant_id: UUID | str, field: str, value: Any
) -> None:
    """Update a single whitelisted column on a calls row."""
    try:
        await update_call(call_id, tenant_id, **{field: value})
    except Exception:
        logger.exception("Failed to update %s for call %s", field, call_id)
