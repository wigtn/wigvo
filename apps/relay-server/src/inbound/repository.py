from __future__ import annotations

from typing import Iterable
from uuid import UUID

import asyncpg

from src.db.pg_client import get_pool
from src.inbound.models import ALLOWED_TRANSITIONS, DispatchRecord, DispatchState


def _record(row: asyncpg.Record | None) -> DispatchRecord | None:
    if row is None:
        return None
    return DispatchRecord.model_validate(dict(row))


async def resolve_inbound_tenant(inbound_number: str) -> tuple[UUID, list[str]] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT tenant_id, languages
            FROM tenant_call_config
            WHERE provider = 'twilio' AND inbound_number = $1
            """,
            inbound_number,
        )
    if row is None:
        return None
    return UUID(str(row["tenant_id"])), list(row["languages"] or [])


async def count_preconnected_dispatches() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT count(*)
                FROM inbound_call_dispatch
                WHERE state IN ('RINGING', 'WAITING_FOR_AGENT', 'CLAIMED', 'SESSION_STARTING')
                """
            )
        )


async def create_dispatch(
    *,
    call_id: UUID,
    tenant_id: UUID,
    provider_call_sid: str | None,
) -> DispatchRecord:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO inbound_call_dispatch (
              call_id, tenant_id, provider_call_sid, state
            )
            VALUES ($1, $2, $3, 'RINGING')
            ON CONFLICT DO NOTHING
            RETURNING *, '[]'::jsonb AS languages
            """,
            call_id,
            tenant_id,
            provider_call_sid,
        )
        if row is None:
            row = await conn.fetchrow(
                """
                SELECT d.*, c.languages
                FROM inbound_call_dispatch d
                JOIN tenant_call_config c ON c.tenant_id = d.tenant_id
                WHERE d.call_id = $1
                   OR ($2::text IS NOT NULL AND d.provider_call_sid = $2)
                """,
                call_id,
                provider_call_sid,
            )
    result = _record(row)
    if result is None:
        raise RuntimeError("Failed to create inbound dispatch")
    if result.tenant_id != tenant_id:
        raise RuntimeError("Inbound provider call is already bound to another tenant")
    return result


async def mark_waiting(call_id: UUID, tenant_id: UUID) -> DispatchRecord | None:
    return await transition_dispatch(
        call_id=call_id,
        tenant_id=tenant_id,
        from_states=[DispatchState.RINGING],
        to_state=DispatchState.WAITING_FOR_AGENT,
    )


async def list_waiting(tenant_id: UUID, limit: int = 50) -> list[DispatchRecord]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT d.*, c.languages
            FROM inbound_call_dispatch d
            JOIN tenant_call_config c ON c.tenant_id = d.tenant_id
            WHERE d.tenant_id = $1 AND d.state = 'WAITING_FOR_AGENT'
            ORDER BY d.created_at ASC, d.call_id ASC
            LIMIT $2
            """,
            tenant_id,
            limit,
        )
    return [DispatchRecord.model_validate(dict(row)) for row in rows]


async def get_dispatch(call_id: UUID) -> DispatchRecord | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT d.*, c.languages
            FROM inbound_call_dispatch d
            JOIN tenant_call_config c ON c.tenant_id = d.tenant_id
            WHERE d.call_id = $1
            """,
            call_id,
        )
    return _record(row)


async def claim_dispatch(
    *,
    call_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    claim_ttl_s: int,
) -> DispatchRecord | None:
    """Single-statement conditional update: exactly one concurrent claimant wins."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE inbound_call_dispatch d
            SET state = 'CLAIMED',
                claimed_by = $3,
                claim_expires_at = now() + make_interval(secs => $4),
                version = d.version + 1
            FROM tenant_call_config c
            WHERE d.call_id = $1
              AND d.tenant_id = $2
              AND d.state = 'WAITING_FOR_AGENT'
              AND d.claimed_by IS NULL
              AND c.tenant_id = d.tenant_id
            RETURNING d.*, c.languages
            """,
            call_id,
            tenant_id,
            user_id,
            claim_ttl_s,
        )
    return _record(row)


async def transition_dispatch(
    *,
    call_id: UUID,
    tenant_id: UUID,
    from_states: Iterable[DispatchState],
    to_state: DispatchState,
    claimed_by: UUID | None = None,
    end_reason: str | None = None,
) -> DispatchRecord | None:
    source_states = list(from_states)
    invalid = [state for state in source_states if to_state not in ALLOWED_TRANSITIONS[state]]
    if invalid:
        raise ValueError(
            f"Invalid inbound dispatch transition: {invalid[0].value} -> {to_state.value}"
        )
    states = [str(state) for state in source_states]
    assignments = ["state = $4", "version = d.version + 1"]
    params: list[object] = [call_id, tenant_id, states, str(to_state)]
    if to_state == DispatchState.CONNECTED:
        assignments.append("connected_at = now()")
        assignments.append("claim_expires_at = NULL")
    if to_state in {
        DispatchState.ENDED,
        DispatchState.CANCELLED,
        DispatchState.TIMEOUT,
        DispatchState.REJECTED,
    }:
        assignments.extend(
            [
                "ended_at = now()",
                f"end_reason = ${len(params) + 1}",
                "claim_expires_at = NULL",
            ]
        )
        params.append(end_reason or to_state.value.lower())

    claimed_filter = ""
    if claimed_by is not None:
        params.append(claimed_by)
        claimed_filter = f" AND d.claimed_by = ${len(params)}"

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE inbound_call_dispatch d
            SET {', '.join(assignments)}
            FROM tenant_call_config c
            WHERE d.call_id = $1
              AND d.tenant_id = $2
              AND d.state = ANY($3::text[])
              AND c.tenant_id = d.tenant_id
              {claimed_filter}
            RETURNING d.*, c.languages
            """,
            *params,
        )
    return _record(row)


async def pickup_token_is_current(
    *, call_id: UUID, tenant_id: UUID, user_id: UUID
) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return bool(
            await conn.fetchval(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM inbound_call_dispatch
                  WHERE call_id = $1
                    AND tenant_id = $2
                    AND claimed_by = $3
                    AND state = 'CONNECTED'
                )
                """,
                call_id,
                tenant_id,
                user_id,
            )
        )


async def is_inbound_dispatch(call_id: UUID) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM inbound_call_dispatch WHERE call_id = $1)",
                call_id,
            )
        )


async def release_expired_claims() -> list[UUID]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE inbound_call_dispatch
            SET state = 'WAITING_FOR_AGENT',
                claimed_by = NULL,
                claim_expires_at = NULL,
                version = version + 1
            WHERE state = 'CLAIMED' AND claim_expires_at <= now()
            RETURNING call_id
            """
        )
    return [UUID(str(row["call_id"])) for row in rows]


async def timeout_waiting_calls(wait_timeout_s: int) -> list[UUID]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE inbound_call_dispatch
            SET state = 'TIMEOUT',
                ended_at = now(),
                end_reason = 'agent_timeout',
                version = version + 1
            WHERE state = 'WAITING_FOR_AGENT'
              AND updated_at <= now() - make_interval(secs => $1)
            RETURNING call_id
            """,
            wait_timeout_s,
        )
    return [UUID(str(row["call_id"])) for row in rows]


async def recover_after_restart() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE inbound_call_dispatch
            SET state = CASE WHEN state = 'CONNECTED' THEN 'ENDED' ELSE 'CANCELLED' END,
                claimed_by = CASE WHEN state = 'CONNECTED' THEN claimed_by ELSE NULL END,
                claim_expires_at = NULL,
                ended_at = now(),
                end_reason = 'server_restart',
                version = version + 1
            WHERE state IN (
              'RINGING', 'WAITING_FOR_AGENT', 'CLAIMED', 'SESSION_STARTING', 'CONNECTED'
            )
            """
        )
    return int(result.split()[-1])


async def finish_dispatch(call_id: UUID, reason: str) -> DispatchRecord | None:
    current = await get_dispatch(call_id)
    if current is None or current.state in {
        DispatchState.ENDED,
        DispatchState.CANCELLED,
        DispatchState.TIMEOUT,
        DispatchState.REJECTED,
    }:
        return current
    terminal = (
        DispatchState.ENDED
        if current.state == DispatchState.CONNECTED
        else DispatchState.CANCELLED
    )
    return await transition_dispatch(
        call_id=call_id,
        tenant_id=current.tenant_id,
        from_states=[current.state],
        to_state=terminal,
        end_reason=reason,
    )
