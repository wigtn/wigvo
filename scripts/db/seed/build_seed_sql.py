#!/usr/bin/env python3
"""Generate scripts/db/init/002_seed.sql from /tmp/wigvo-dump/*.json.

Applies the wigvo->wigsso user_id remap for accounts that already existed in
both projects (matched by email), so historical data lands on the right
wigsso UUID.

Run this after the dump completes; the resulting SQL is mounted into the
postgres container via docker-entrypoint-initdb.d.
"""
from __future__ import annotations

import json
from pathlib import Path

DUMP_DIR = Path("/tmp/wigvo-dump")
OUT_FILE = Path("/opt/server/services/wigvo-v2/scripts/db/init/002_seed.sql")


def load(name: str):
    return json.load((DUMP_DIR / f"{name}.json").open())


def sql_value(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return "'" + json.dumps(v, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def insert_stmt(table: str, columns: list[str], rows: list[dict], remap_user_id: dict[str, str] | None = None) -> str:
    if not rows:
        return f"-- no rows for {table}\n"
    cols_sql = ", ".join(columns)
    lines = [f"INSERT INTO {table} ({cols_sql}) VALUES"]
    value_lines = []
    for r in rows:
        record = dict(r)
        if remap_user_id and record.get("user_id") in remap_user_id:
            record["user_id"] = remap_user_id[record["user_id"]]
        value_lines.append("  (" + ", ".join(sql_value(record.get(c)) for c in columns) + ")")
    lines.append(",\n".join(value_lines) + "\nON CONFLICT (id) DO NOTHING;\n")
    return "\n".join(lines)


def main() -> None:
    wigvo_users = load("auth_users")["users"]
    wigsso_users = load("wigsso_users")["users"]
    wigvo_by_email = {u["email"]: u for u in wigvo_users if u.get("email")}
    wigsso_by_email = {u["email"]: u for u in wigsso_users if u.get("email")}

    # Build wigvo_user_id -> final_user_id remap. For users present in both
    # projects (matched by email) we use wigsso's existing UUID; for users
    # only in wigvo we keep their UUID (it will be created in wigsso with the
    # same id by the migrate_users_to_wigsso.py script).
    remap: dict[str, str] = {}
    for email, vu in wigvo_by_email.items():
        if email in wigsso_by_email and vu["id"] != wigsso_by_email[email]["id"]:
            remap[vu["id"]] = wigsso_by_email[email]["id"]

    # Build the local users table content. id = final wigsso id.
    users_rows = []
    seen_ids: set[str] = set()
    for vu in wigvo_users:
        email = vu.get("email")
        if not email:
            continue
        final_id = remap.get(vu["id"], vu["id"])
        if final_id in seen_ids:
            continue
        seen_ids.add(final_id)
        meta = vu.get("user_metadata") or {}
        name = meta.get("name") or meta.get("full_name")
        users_rows.append({
            "id": final_id,
            "email": email,
            "name": name,
            "created_at": vu.get("created_at"),
            "updated_at": vu.get("updated_at") or vu.get("created_at"),
        })

    conversations = load("conversations")
    messages = load("messages")
    calls_raw = load("calls")
    entities = load("conversation_entities")
    cache = load("place_search_cache")

    # calls.user_id is NOT NULL. Historical dumps include 49 orphan rows
    # from the early dev period (no user_id, no conversation either).
    # Drop them rather than weakening the schema — they're junk.
    calls = [c for c in calls_raw if c.get("user_id")]
    dropped = len(calls_raw) - len(calls)
    if dropped:
        print(f"  dropped {dropped} orphan calls with null user_id")

    out: list[str] = [
        "-- Auto-generated seed from /tmp/wigvo-dump/. Do not edit by hand.",
        "-- Re-run scripts/db/seed/build_seed_sql.py to regenerate.",
        "",
    ]

    out.append(insert_stmt(
        "users",
        ["id", "email", "name", "created_at", "updated_at"],
        users_rows,
    ))

    out.append(insert_stmt(
        "conversations",
        ["id", "user_id", "status", "collected_data", "created_at", "updated_at"],
        conversations,
        remap_user_id=remap,
    ))

    out.append(insert_stmt(
        "messages",
        ["id", "conversation_id", "role", "content", "metadata", "created_at"],
        messages,
    ))

    out.append(insert_stmt(
        "calls",
        [
            "id", "conversation_id", "user_id", "request_type", "target_phone",
            "target_name", "parsed_date", "parsed_time", "parsed_service",
            "status", "result", "summary", "call_id", "call_mode",
            "relay_ws_url", "call_sid", "source_language", "target_language",
            "communication_mode", "transcript_bilingual", "cost_tokens",
            "guardrail_events", "recovery_events", "function_call_logs",
            "call_result", "call_result_data", "auto_ended", "duration_s",
            "total_tokens", "created_at", "updated_at", "completed_at",
        ],
        calls,
        remap_user_id=remap,
    ))

    out.append(insert_stmt(
        "conversation_entities",
        [
            "id", "conversation_id", "entity_type", "entity_value",
            "confidence", "source_message_id", "created_at", "updated_at",
        ],
        entities,
    ))

    out.append(insert_stmt(
        "place_search_cache",
        ["id", "query_hash", "query_text", "results", "created_at", "expires_at"],
        cache,
    ))

    OUT_FILE.write_text("\n".join(out))
    print(f"wrote {OUT_FILE} ({OUT_FILE.stat().st_size:,} bytes)")
    print(f"users seeded: {len(users_rows)}")
    print(f"remaps applied: {len(remap)} unique wigvo_id -> wigsso_id")
    if remap:
        for src, dst in remap.items():
            print(f"  {src} -> {dst}")


if __name__ == "__main__":
    main()
