"""Deprecated. The relay server now talks directly to local Postgres via
asyncpg. This module re-exports the new helpers under their old names so
older imports keep working until callers can be migrated.

Prefer importing from `src.db.pg_client` in new code.
"""

from src.db.pg_client import (  # noqa: F401
    fetch_call_conversation_id,
    get_pool,
    persist_call,
    update_call,
    update_call_field,
    update_conversation,
)


async def get_client():
    """Legacy name. Returns the asyncpg pool.

    Callers that used `client.table("calls").update({...}).eq("id", id).execute()`
    must migrate to `update_call(id, **fields)` directly.
    """
    return await get_pool()
