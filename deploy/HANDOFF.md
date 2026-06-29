# WIGVO US-demo migration — HANDOFF

Move the relay off the **Korea Mac Mini** to a **US (us-west1) GCP VM** for a live **San Francisco** demo.
Web stays on **Vercel** (`wigvo.wigtn.com`). DB → **Supabase managed (US)**. SSO → Supabase **KR** (unchanged).
Continue on the **Mac Mini** (it has the real secrets). ⚠️ **This repo is PUBLIC — no secrets in here.**

Target: ~10 concurrent active calls (cap 10), stability-first, ~few days.

---
## ✅ Done (from the Korea dev machine)
- **GCE VM** `wigvo-relay` · project `wigtn-voice-only` · zone `us-west1-a` · `e2-standard-2` (2 vCPU/8GB, ~$1.6/day)
  - Static IP **136.66.95.148** (reserved `wigvo-relay-ip`)
  - Firewall: `wigvo-allow-web` (tcp 80,443 / 0.0.0.0/0), `wigvo-allow-ssh-iap` (tcp 22 / IAP 35.235.240.0/20). OS Login on.
  - **Prepped**: Docker 29 + Compose v5 installed; repo cloned at `/opt/wigvo/wigvo` (branch `chore/us-demo-deploy`).
- **DNS**: `relay.wigvo.wigtn.com` → 136.66.95.148, Cloudflare **gray cloud (DNS-only)** — correct. **Keep gray** (Caddy auto-TLS + low-latency WS; orange/proxy breaks WS).
- **Branches pushed (PRs to review/merge)**:
  - `chore/us-demo-deploy` — `deploy/` (docker-compose.cloud.yml · Caddyfile · .env.example · README · this file)
  - `feat/concurrency-cap` — relay hard cap (default 10, env `MAX_CONCURRENT_CALLS`); returns 503 with `active/max` for a wait/queue UX
- **Decision**: single VM, **not** Cloud Run — relay is stateful single-process (`--workers 1`, in-memory `call_manager`; multi-instance/autoscale breaks calls). Cap + (optional) VAD-thread-offload handle the load.

## ⛔ Blockers (why this couldn't finish on the Korea box)
1. **DB password rejected** — the tried value fails `password authentication`. → **Reset it** (Supabase → Settings → Database → Reset database password). This also rotates the value leaked in chat. Use the new one everywhere.
2. **OpenAI/Twilio secrets are only on the Mac Mini `.env`** (not on the Korea machine).
3. gcloud SSH (plink) failed on the Korea Windows box → use **GCP Console SSH (browser)** for the VM.

---
## ▶ Remaining steps (do on Mac Mini)

### 1. Reset Supabase DB password → note the new one.

### 2. Apply DB schema (once; fresh project)
`scripts/db/init/001_schema.sql` via Supabase **SQL Editor** (paste & run), or:
```bash
psql "postgresql://postgres.<REF>:<NEW_PW>@aws-1-us-west-1.pooler.supabase.com:5432/postgres?sslmode=require" \
  -f scripts/db/init/001_schema.sql
```
(`<REF>` = Supabase project ref, from the dashboard.)

### 3. (recommended) Merge both PRs → `main` so the VM gets `deploy/` + the cap together.

### 4. Deploy relay (VM → Console SSH browser terminal)
```bash
cd /opt/wigvo/wigvo && git checkout main && git pull   # if PRs merged; else: git checkout chore/us-demo-deploy && git pull
cd deploy
cat > .env <<'EOF'
DATABASE_URL=postgresql://postgres.<REF>:<NEW_DB_PW>@aws-1-us-west-1.pooler.supabase.com:5432/postgres
OPENAI_API_KEY=<from Mac Mini .env>
OPENAI_REALTIME_MODEL=gpt-realtime
TWILIO_ACCOUNT_SID=<from Mac Mini .env>
TWILIO_AUTH_TOKEN=<from Mac Mini .env>
TWILIO_PHONE_NUMBER=<from Mac Mini .env>
RELAY_SERVER_URL=https://relay.wigvo.wigtn.com
ALLOWED_ORIGINS=https://wigvo.wigtn.com
LOCAL_VAD_ENABLED=true
MAX_CONCURRENT_CALLS=10
EOF
chmod 600 .env
sudo docker compose -f docker-compose.cloud.yml up -d --build
sudo docker compose -f docker-compose.cloud.yml logs -f relay
```
`DATABASE_URL` = **Session Pooler (5432)**. heredoc is single-quoted → `!!` etc. in the password are safe.

### 5. Verify
- `https://relay.wigvo.wigtn.com` serves (Caddy auto-cert; DNS live + 80/443 open ✓).
- Twilio voice webhook / Media Streams → `https://relay.wigvo.wigtn.com`.
- **Check** the relay's TwiML `<Stream>` URL = `wss://relay.wigvo.wigtn.com/...` (from `RELAY_SERVER_URL`). If still `ws://host:8000`, patch the TwiML builder.

### 6. Vercel (web)
- `NEXT_PUBLIC_RELAY_WS_URL = wss://relay.wigvo.wigtn.com`
- `DATABASE_URL` = **Transaction Pooler (6543)** (same host, port **6543**) + client `prepare: false`
- Supabase SSO keys unchanged. Domain `wigvo.wigtn.com` unchanged.

### 7. (optional code) Web 503 / queue UX — handle relay 503 `at_capacity` (`active/max`) with a "wait, retry" message.

---
## CI/CD — ⚠️ NOT set up for cloud/Vercel/Supabase yet
The existing **`.github/workflows/deploy-prod.yml` deploys to the MAC MINI only** (self-hosted runner, `cd /opt/server/services/wigvo-v2`, docker compose). It triggers on **push to main**.

- **🔴 Must do**: **disable / repoint `deploy-prod.yml`** before merging to main — otherwise every main push keeps deploying to the Korea Mac Mini (the box we're migrating off). (Comment out the `on.push`, or gate it, or delete.)
- **Vercel (web)**: CI/CD is via Vercel's GitHub integration — in the **Vercel dashboard**: connect this repo, set **Root Directory = `apps/web`**, add the env vars (step 6). Then it auto-deploys on push to main (+ PR previews). No repo file needed (Next.js auto-detected).
- **Supabase**: schema is applied manually (step 2). Full migration CI/CD (supabase CLI action) is overkill for a few-day demo.
- **VM relay**: for a short demo, **manual deploy** (step 4) is fine. If you want CI/CD, add a workflow that SSHes (IAP) to the VM and `git pull && docker compose up -d --build`, e.g.:
  ```yaml
  # .github/workflows/deploy-relay-vm.yml  (ENABLE deliberately; needs secrets)
  name: Deploy Relay (VM)
  on: { push: { branches: [main], paths: ['apps/relay-server/**','deploy/**','uv.lock'] } }
  jobs:
    deploy:
      runs-on: ubuntu-latest
      steps:
        - uses: google-github-actions/auth@v2
          with: { credentials_json: '${{ secrets.GCP_SA_KEY }}' }   # SA with compute.osLogin + IAP
        - uses: google-github-actions/setup-gcloud@v2
        - run: |
            gcloud compute ssh wigvo-relay --project=wigtn-voice-only --zone=us-west1-a --tunnel-through-iap \
              --command='cd /opt/wigvo/wigvo && git pull && cd deploy && sudo docker compose -f docker-compose.cloud.yml up -d --build'
  ```
  Needs GitHub secret `GCP_SA_KEY` (service-account JSON with IAP + OS Login). The `.env` on the VM stays put across deploys.

---
## Demo-day checklist
- Pre-warm; **freeze deploys during the demo** (rebuild = relay restart = drops in-flight calls).
- Raise **OpenAI Realtime concurrency** + **Twilio concurrent-channel/CPS** limits in advance (lead time).
- Watch: concurrent calls, CPU, OpenAI errors. **OpenAI budget alarm**. Cap = 10 (tune via load test).
- Keep DNS **gray**.

## Security
- **Reset the leaked DB password.** Never commit secrets (`.env` gitignored; repo PUBLIC).
- SSH only via IAP/Console. Relay `/calls/{id}/monitor` is **unauthenticated** → add a token/guard before wide exposure (PR #17 review).

## Cost (rough)
VM ~$1.6/day · Supabase Pro ~$25/mo · Vercel existing. **Real driver = OpenAI Realtime + Twilio usage** (bounded by the cap).

## Not yet done (pick up here)
- [ ] Reset DB password + apply schema
- [ ] Fill VM `deploy/.env` + `docker compose up` (step 4)
- [ ] Vercel env + connect (step 6) · disable `deploy-prod.yml`
- [ ] Verify TwiML `wss://` URL
- [ ] (opt) web 503 UX · VAD thread-offload · load test (10 concurrent) · monitor-endpoint auth
