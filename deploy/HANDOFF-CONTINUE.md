# WIGVO US-demo — CONTINUE handoff (CI/CD + Supabase)

Picks up after the Mac Mini session that moved the **relay** to a US GCP VM and cut the
**web** over to Vercel. Continue from **Windows**. ⚠️ This repo is **PUBLIC — no secrets here.**

---
## ✅ Done & live now
- **Relay VM** — project `wigtn-voice-only`, instance `wigvo-relay` (e2-standard-2, us-west1-a), static IP **136.66.95.148**.
  - `deploy/docker-compose.cloud.yml`: `relay` + `caddy` (Caddy = auto-HTTPS reverse proxy on 80/443 → relay:8080).
  - VM `deploy/.env`: OpenAI + Twilio set. **`DATABASE_URL` intentionally omitted** — relay is DB-optional (boots & serves calls; only *post-call persistence* is skipped). `ALLOWED_ORIGINS=["https://wigvo.wigtn.com"]` **must be a JSON array** (a plain string crashes pydantic-settings).
  - VM is on branch `chore/us-demo-deploy`; **OS Login enabled**.
  - Verified: `https://relay.wigvo.wigtn.com/health` → 200, valid Let's Encrypt cert.
- **DNS** — `relay.wigvo.wigtn.com` → 136.66.95.148, Cloudflare **gray (DNS-only)**. Keep gray (Caddy TLS + WS; orange breaks WS).
- **Web on Vercel** — project `wigtn/wigvo-web` (root `apps/web`, Next.js 16), production deployed.
  - Env set: `NEXT_PUBLIC_RELAY_WS_URL=wss://relay.wigvo.wigtn.com`, KR Supabase (`NEXT_PUBLIC_SUPABASE_*`, `SUPABASE_*`), NAVER, `NEXT_PUBLIC_BASE_URL=https://wigvo.wigtn.com`. **`DATABASE_URL` not set yet** (see Supabase below).
  - **Domain cut over**: `wigvo.wigtn.com` → CNAME `cname.vercel-dns.com` (gray), Vercel **verified**, serving 200. (Was tunnel → Mac Mini.)
- **CI/CD infra (ready, not yet wired)**:
  - Service account `github-deploy@wigtn-voice-only` with roles `iap.tunnelResourceAccessor`, `compute.osLogin`, `compute.viewer`.
  - GitHub secret **`GCP_SA_KEY`** already set on `wigtn/wigvo-v2`.

---
## ⬜ Remaining 1 — CI/CD on `main`
Goal: push to `main` → auto-deploy relay to the VM; stop auto-deploying to the Mac Mini.

> ⚠️ Pushing `.github/workflows/**` requires a token with the **`workflow`** scope.
> On Windows: `gh auth refresh -h github.com -s workflow` (or push with a PAT that has it).

Do these **in order** — do NOT merge to `main` before step 1, or the merge will redeploy the Mac Mini:

1. **Park the Mac Mini workflow** (keeps it for post-demo restore, removes it from `main`):
   ```bash
   git checkout main && git pull
   git branch ci/mac-mini-deploy && git push -u origin ci/mac-mini-deploy   # snapshot still has deploy-prod.yml
   git rm .github/workflows/deploy-prod.yml
   ```
2. **Add** `.github/workflows/deploy-relay-vm.yml` (uses the `GCP_SA_KEY` secret already set):
   ```yaml
   name: Deploy Relay (US VM)
   on:
     push:
       branches: [main]
       paths: ['apps/relay-server/**', 'deploy/**', 'uv.lock']
   concurrency: { group: deploy-relay-vm, cancel-in-progress: false }
   jobs:
     deploy:
       runs-on: ubuntu-latest
       steps:
         - uses: google-github-actions/auth@v2
           with: { credentials_json: '${{ secrets.GCP_SA_KEY }}' }
         - uses: google-github-actions/setup-gcloud@v2
         - run: |
             gcloud compute ssh wigvo-relay \
               --project=wigtn-voice-only --zone=us-west1-a --tunnel-through-iap \
               --command='cd /opt/wigvo/wigvo && git fetch origin && git reset --hard origin/main && cd deploy && sudo docker compose -f docker-compose.cloud.yml up -d --build'
   ```
   Commit steps 1+2 together (workflow-only commit → its own `paths` filter doesn't match, so it won't trigger a VM deploy by itself — safe).
3. **Merge** `chore/us-demo-deploy` **and** `feat/concurrency-cap` → `main`. Brings `deploy/` + the concurrency cap onto main; the `deploy-relay-vm` workflow then fires and redeploys the VM.
4. **Point the VM at `main`** (it currently tracks `chore/us-demo-deploy`):
   ```bash
   gcloud compute ssh wigvo-relay --project=wigtn-voice-only --zone=us-west1-a --tunnel-through-iap \
     --command='cd /opt/wigvo/wigvo && git checkout main && git pull'
   ```
   After this the cap takes effect (`MAX_CONCURRENT_CALLS=10` is already in the VM `.env`; the enforcing code lives on `feat/concurrency-cap`).

---
## ⬜ Remaining 2 — Supabase (DB)
Relay + web run without it now; this adds post-call persistence + web DB access.

1. **Reset DB password** — Supabase dashboard → Settings → Database → *Reset database password*. Note the new value (also rotates the leaked one).
2. **Apply schema** (fresh project) — `scripts/db/init/001_schema.sql` (then `002_seed.sql`) via SQL Editor, or:
   ```bash
   psql "postgresql://postgres.<REF>:<NEW_PW>@<pooler-host>:5432/postgres?sslmode=require" -f scripts/db/init/001_schema.sql
   ```
3. **Wire it in**:
   - **Relay VM** `/opt/wigvo/wigvo/deploy/.env` → add `DATABASE_URL=postgresql://...@<pooler>:5432/postgres` (**Session Pooler 5432**), then `cd deploy && sudo docker compose -f docker-compose.cloud.yml up -d relay`.
   - **Vercel `wigvo-web`** env → add `DATABASE_URL` = **Transaction Pooler (6543)** (client `prepare: false`), then redeploy.
   - SSO keys (Supabase **KR**) stay unchanged.

---
## Notes / cleanup
- **Stale tunnel route**: `wigvo.wigtn.com` entry still in `/etc/cloudflared/config.yml` (no traffic now). Remove it + restart `cloudflared` to tidy — other routes unaffected. (Confirm before restart.)
- **Local Mac Mini containers** `wigvo-web` / `wigvo-relay` / `wigvo-postgres` are still up — harmless to leave during the demo; stop after.
- Keep relay DNS **gray**. **Freeze deploys during the live demo** (rebuild = relay restart = drops in-flight calls).
- Pre-raise **OpenAI Realtime** concurrency + **Twilio** concurrent-channel/CPS limits (lead time).
- Relay `/calls/{id}/monitor` is unauthenticated — add a guard before wide exposure.
