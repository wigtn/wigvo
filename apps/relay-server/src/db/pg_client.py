"""Local Postgres client — replaces the former Supabase Python SDK usage.

The relay server only writes to the `calls` and `conversations` tables; the
former PostgREST-style `client.table("calls").update({...}).eq("id", id).execute()`
calls become two helpers:

- update_call(call_id, **fields)
- update_conversation(conversation_id, **fields)

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


def _build_update(table: str, columns: dict[str, str], fields: dict[str, Any]) -> tuple[str, list[Any]]:
    """Build an `UPDATE ... WHERE id = $N` statement from a column whitelist."""
    set_parts: list[str] = []
    values: list[Any] = []
    for col, val in fields.items():
        if col not in columns:
            logger.warning("Ignoring unknown column %s.%s", table, col)
            continue
        set_parts.append(f"{col} = ${len(values) + 1}")
        values.append(val)
    if not set_parts:
        return "", values
    set_parts.append(f"updated_at = ${len(values) + 1}")
    values.append(datetime.datetime.now(datetime.timezone.utc))
    placeholder_id = f"${len(values) + 1}"
    sql = f"UPDATE {table} SET {', '.join(set_parts)} WHERE id = {placeholder_id}"
    return sql, values


async def update_call(call_id: str, **fields: Any) -> int:
    """Update arbitrary whitelisted columns on a calls row."""
    if not fields:
        return 0
    sql, params = _build_update("calls", _CALL_COLUMNS, fields)
    if not sql:
        return 0
    params.append(call_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(sql, *params)
    # asyncpg returns "UPDATE <n>"
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


async def update_conversation(conversation_id: str, **fields: Any) -> int:
    if not fields:
        return 0
    sql, params = _build_update("conversations", _CONV_COLUMNS, fields)
    if not sql:
        return 0
    params.append(conversation_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(sql, *params)
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


async def fetch_call_conversation_id(call_id: str) -> str | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT conversation_id FROM calls WHERE id = $1", call_id
        )


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
        await update_call(call.call_id, **data)
        logger.info("Call %s persisted to DB (status=%s)", call.call_id, db_status)
    except Exception:
        logger.exception("Failed to persist call %s", call.call_id)
        return

    try:
        conv_id = await fetch_call_conversation_id(call.call_id)
        if conv_id:
            await update_conversation(conv_id, status="COMPLETED")
            logger.info("Conversation %s status updated to COMPLETED", conv_id)
    except Exception:
        logger.warning(
            "Failed to update conversation status for call %s",
            call.call_id,
            exc_info=True,
        )


async def update_call_field(call_id: str, field: str, value: Any) -> None:
    """Update a single whitelisted column on a calls row."""
    try:
        await update_call(call_id, **{field: value})
    except Exception:
        logger.exception("Failed to update %s for call %s", field, call_id)
