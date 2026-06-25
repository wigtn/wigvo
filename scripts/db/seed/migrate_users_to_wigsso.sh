#!/bin/bash
# Migrate users that only exist in wigvo Supabase auth to the shared wigsso
# project, preserving their UUIDs so that historical wigvo data (already
# seeded into local Postgres) stays referentially intact.
#
# Idempotent: re-running skips users that already exist in wigsso.

set -euo pipefail

# 자격증명은 환경변수로 주입한다 (키를 소스에 하드코딩하지 않는다).
#   export WIGSSO_SRK=<wigsso service_role key>   # .env 의 SUPABASE_SERVICE_ROLE_KEY 와 동일
#   ./migrate_users_to_wigsso.sh
WIGSSO_URL="${WIGSSO_URL:-https://juzzvlbadnfqdibtuhdi.supabase.co}"
WIGSSO_SRK="${WIGSSO_SRK:?WIGSSO_SRK 환경변수를 설정하세요 (wigsso service_role key)}"
DUMP_DIR="${DUMP_DIR:-/tmp/wigvo-dump}"
export WIGSSO_URL WIGSSO_SRK DUMP_DIR

python3 - <<'PYEOF'
import json, os, sys, urllib.request, urllib.error

WIGSSO_URL = os.environ['WIGSSO_URL']
WIGSSO_SRK = os.environ['WIGSSO_SRK']
DUMP_DIR = os.environ['DUMP_DIR']

# Use unverified SSL (system-Python certs missing on this host)
import ssl
ctx = ssl._create_unverified_context()

with open(f'{DUMP_DIR}/auth_users.json') as f:
    wigvo_users = json.load(f)['users']
with open(f'{DUMP_DIR}/wigsso_users.json') as f:
    wigsso_users = json.load(f)['users']

wigsso_emails = {u.get('email') for u in wigsso_users if u.get('email')}
to_migrate = [u for u in wigvo_users if u.get('email') and u['email'] not in wigsso_emails]

print(f'wigsso current: {len(wigsso_users)} users')
print(f'to migrate from wigvo: {len(to_migrate)}')

def admin_create_user(payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f'{WIGSSO_URL}/auth/v1/admin/users',
        data=json.dumps(payload).encode('utf-8'),
        method='POST',
        headers={
            'apikey': WIGSSO_SRK,
            'Authorization': f'Bearer {WIGSSO_SRK}',
            'Content-Type': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, {'error': body}

ok = 0
fail = 0
for u in to_migrate:
    meta = u.get('user_metadata') or {}
    payload = {
        'id': u['id'],
        'email': u['email'],
        'email_confirm': True,
        'user_metadata': {
            'name': meta.get('name') or meta.get('full_name') or '',
            'full_name': meta.get('full_name') or meta.get('name') or '',
            'avatar_url': meta.get('avatar_url') or '',
            'migrated_from': 'wigvo',
        },
        'app_metadata': {
            'provider': u.get('app_metadata', {}).get('provider', 'email'),
            'providers': u.get('app_metadata', {}).get('providers', []),
        },
    }
    status, body = admin_create_user(payload)
    if status in (200, 201):
        ok += 1
        print(f'  OK  {u["email"]:30s} id={u["id"][:8]}')
    elif status == 422 and isinstance(body.get('error'), dict) and 'already' in str(body.get('error', '')).lower():
        print(f'  SKIP {u["email"]:30s} (already exists)')
    else:
        fail += 1
        print(f'  FAIL {u["email"]:30s} status={status} body={body}')

print(f'\nDone: {ok} created, {fail} failed')
sys.exit(0 if fail == 0 else 1)
PYEOF
