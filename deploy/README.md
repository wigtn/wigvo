# WIGVO — US demo deployment (relay)

Relay only. **Web → Vercel** (`wigvo.wigtn.com`) · **DB → Supabase managed (US)** · **SSO → Supabase (KR, unchanged)**.

Goal: move the relay off the Korea Mac Mini to a **US region** so transpacific RTT
doesn't kill real-time translation at a US demo (~50–100 concurrent calls, ~2 weeks).

## ⚠️ Security (this repo is PUBLIC)
- **Never commit secrets.** Real values go in `deploy/.env` (gitignored). Only `.env.example` (placeholders) is committed.
- Firewall: open **only 80/443** to the world; keep relay `:8080` internal (Caddy fronts it). Restrict SSH (22) to your IP or use IAP.
- TLS is automatic via Caddy (Let's Encrypt) for `relay.wigvo.wigtn.com`.
- The monitor endpoint `/relay/calls/{id}/monitor` is currently **UNAUTHENTICATED** → on a public relay anyone with a call_id can eavesdrop. Add a short-lived token/guard before going live (PR #17 finding).

## Why a single VM (not Cloud Run)
Relay is **stateful + single-process** (`--workers 1`; all call state in an in-memory singleton; every WebSocket of a call — Twilio media, caller, monitor — must hit the same process). Cloud Run autoscale would split a call's connections across instances and break it; a new revision spins a fresh instance and drops in-flight calls. A **single US VM** avoids those footguns, reuses this docker-compose, and you can watch it live.
**Scale path** (only if one process can't hold the target — load-test first): a **fixed pool** of relay processes (`:8081`, `:8082`…) + per-call routing, or a 2nd VM. Do **not** just add replicas.

## 0) Prereqs (lead time — start now)
- Request **OpenAI Realtime concurrency** + **Twilio concurrent-channel/CPS** increases.
- **Supabase (US)**: create/pick a US project → apply `scripts/db/init/001_schema.sql` → copy the **pooler (Supavisor)** connection string.

## 1) Provision the VM (US region — NOT the default asia-northeast1)
> Use a **dedicated project** (don't mix into the SSO/bot project `wigss-491601`).
```bash
PROJECT=wigvo-demo          # create or pick a dedicated project + link billing
ZONE=us-central1-a          # or near the venue (us-east1 / us-west1)
gcloud config set project "$PROJECT"

gcloud compute instances create wigvo-relay \
  --zone="$ZONE" --machine-type=e2-standard-4 \
  --image-family=ubuntu-2404-lts --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB --tags=wigvo-relay

gcloud compute firewall-rules create wigvo-web \
  --allow=tcp:80,tcp:443 --target-tags=wigvo-relay --source-ranges=0.0.0.0/0
# SSH: prefer IAP, or add a rule restricting tcp:22 to your IP only.
```
Then set DNS: `relay.wigvo.wigtn.com` A-record → the VM's external IP.

## 2) Deploy on the VM
```bash
# install docker engine + compose plugin, then:
git clone https://github.com/wigtn/wigvo && cd wigvo
git checkout chore/us-demo-deploy   # until merged to main
cd deploy
cp .env.example .env && chmod 600 .env   # fill REAL secrets here (never committed)
docker compose -f docker-compose.cloud.yml up -d --build
docker compose -f docker-compose.cloud.yml logs -f relay
```

## 3) Vercel (web) env
- `NEXT_PUBLIC_RELAY_WS_URL = wss://relay.wigvo.wigtn.com`  (browser WS connects directly to the relay, not via Vercel)
- Supabase SSO keys (unchanged) + `DATABASE_URL` = Supabase **pooler** string
- Domain `wigvo.wigtn.com` unchanged.

## 4) Twilio
- Point the voice webhook / media stream to `https://relay.wigvo.wigtn.com` (relay returns the TwiML).
- **Verify** the TwiML `<Stream>` URL is `wss://relay.wigvo.wigtn.com/...` (driven by `RELAY_SERVER_URL`). If it still emits `ws://host:8000`, that needs a small relay patch.

## 5) Verify + demo day
- Test call → audio both ways + monitor screen shows captions.
- **Pre-warm** before the demo; **freeze deploys during the demo** (a rebuild drops in-flight calls).
- Add a **concurrent-call hard cap** (graceful reject) before load.

## Open code items (separate PRs)
1. Concurrent-call **hard cap**.
2. **PR #17** observer broadcast fix (`list(observers)` snapshot + `gather`/timeout) + **monitor auth**.
3. Verify/patch **TwiML `wss://` URL**.
4. (optional) **Offload VAD to a thread** to raise the single-process ceiling.
5. **No-cost load harness** to measure that ceiling (drives VM sizing / shard count).
