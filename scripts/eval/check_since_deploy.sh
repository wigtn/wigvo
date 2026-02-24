#!/usr/bin/env bash
# check_since_deploy.sh — 특정 날짜 이후 통화 메트릭 (배포 검증용)
# incremental persistence 작동 확인 + 논문 평가지표
#
# Usage:
#   ./check_since_deploy.sh              # 오늘(UTC) 이후
#   ./check_since_deploy.sh 2026-02-22   # 특정 날짜 이후
set -euo pipefail

# ── .env 로드 ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

SUPABASE_URL="${SUPABASE_URL:?SUPABASE_URL is not set}"
SB_KEY="${SUPABASE_SERVICE_ROLE_KEY:-${SUPABASE_SERVICE_KEY:?Neither SUPABASE_SERVICE_ROLE_KEY nor SUPABASE_SERVICE_KEY is set}}"

API="$SUPABASE_URL/rest/v1"
AUTH_HEADERS=(-H "apikey: $SB_KEY" -H "Authorization: Bearer $SB_KEY")

# ── 날짜 파라미터 ─────────────────────────────────────────────────────────────
SINCE="${1:-$(date -u +%Y-%m-%d)}"
SINCE_ISO="${SINCE}T00:00:00Z"

# ── Fetch ────────────────────────────────────────────────────────────────────
TMPFILE=$(mktemp)
trap "rm -f $TMPFILE" EXIT

curl -s "$API/calls?select=id,call_sid,status,result,source_language,target_language,communication_mode,call_mode,duration_s,total_tokens,auto_ended,call_result_data,cost_tokens,transcript_bilingual,created_at,completed_at&created_at=gte.$SINCE_ISO&order=created_at.desc&limit=1000" \
  "${AUTH_HEADERS[@]}" \
  -H "Accept: application/json" > "$TMPFILE"

# ── Process ──────────────────────────────────────────────────────────────────
python3 - "$TMPFILE" "$SINCE" << 'PYEOF'
import json, sys, math
from collections import Counter

# ── Helpers ──────────────────────────────────────────────────────────────────

def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100)
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)

def fmt_stats(values):
    if not values:
        return {"n": 0, "p50": 0, "p95": 0, "mean": 0, "max": 0}
    n = len(values)
    return {
        "n": n,
        "p50": pct(values, 50),
        "p95": pct(values, 95),
        "mean": sum(values) / n,
        "max": max(values),
    }

def pearson_r(x, y):
    n = len(x)
    if n < 2:
        return float("nan")
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sx = sum((xi - mx) ** 2 for xi in x) ** 0.5
    sy = sum((yi - my) ** 2 for yi in y) ** 0.5
    if sx == 0 or sy == 0:
        return float("nan")
    return cov / (sx * sy)

# ── Load ─────────────────────────────────────────────────────────────────────

with open(sys.argv[1]) as f:
    data = json.load(f)

since = sys.argv[2]

if not data:
    print(f"No calls found since {since}.")
    sys.exit(0)

# ── Aggregate ────────────────────────────────────────────────────────────────

total_calls = len(data)

instrumented = []
for c in data:
    m = (c.get("call_result_data") or {}).get("metrics")
    if m:
        instrumented.append((c, m))
n_instrumented = len(instrumented)

status_counter = Counter(c.get("status") or "NULL" for c in data)
MODE_LABELS = {
    "voice_to_voice": "V2V",
    "text_to_voice": "T2V",
    "full_agent": "Agent",
}
mode_counter = Counter()
for c in data:
    raw = c.get("communication_mode") or "unknown"
    mode_counter[MODE_LABELS.get(raw, raw)] += 1

all_sa, all_sb_e2e, all_sb_stt, all_sb_trans = [], [], [], []
all_paired_e2e, all_paired_stt = [], []
all_scatter = []
total_echo_supp = 0
total_echo_loops = 0
total_echo_breakthroughs = 0
total_vad_false = 0
total_hallucinations = 0
total_interrupts = 0
total_guardrail_l2 = 0
total_guardrail_l3 = 0
total_cost_usd = 0.0
n_estimated = 0
total_tokens_sum = 0
total_duration = 0.0

for c, m in instrumented:
    sa = m.get("session_a_latencies_ms", [])
    sb_e2e = m.get("session_b_e2e_latencies_ms", [])
    sb_stt = m.get("session_b_stt_latencies_ms", [])
    all_sa.extend(sa)
    all_sb_e2e.extend(sb_e2e)
    all_sb_stt.extend(sb_stt)

    paired = min(len(sb_e2e), len(sb_stt))
    for i in range(paired):
        all_paired_e2e.append(sb_e2e[i])
        all_paired_stt.append(sb_stt[i])
        t = sb_e2e[i] - sb_stt[i]
        if t >= 0:
            all_sb_trans.append(t)

    transcripts = c.get("transcript_bilingual") or []
    recipients = [t for t in transcripts if t.get("role") == "recipient"]
    for i in range(min(len(recipients), len(sb_e2e))):
        char_len = len(recipients[i].get("original_text", ""))
        all_scatter.append({"char_len": char_len, "latency_ms": sb_e2e[i]})

    total_echo_supp += m.get("echo_suppressions", 0)
    total_echo_loops += m.get("echo_loops_detected", 0)
    total_echo_breakthroughs += m.get("echo_gate_breakthroughs", 0)
    total_vad_false += m.get("vad_false_triggers", 0)
    total_hallucinations += m.get("hallucinations_blocked", 0)
    total_interrupts += m.get("interrupt_count", 0)
    total_guardrail_l2 += m.get("guardrail_level2_count", 0)
    total_guardrail_l3 += m.get("guardrail_level3_count", 0)

    crd = c.get("call_result_data") or {}
    cost = crd.get("cost_usd", 0) or 0
    if not cost:
        ct = c.get("cost_tokens") or {}
        cost = (ct.get("audio_input", 0) * 0.06 + ct.get("audio_output", 0) * 0.24
                + ct.get("text_input", 0) * 0.005 + ct.get("text_output", 0) * 0.02) / 1000
        if cost > 0:
            n_estimated += 1
    total_cost_usd += cost
    total_tokens_sum += c.get("total_tokens") or 0
    dur = c.get("duration_s")
    if dur and dur > 0:
        total_duration += dur

sa_st = fmt_stats(all_sa)
e2e_st = fmt_stats(all_sb_e2e)
stt_st = fmt_stats(all_sb_stt)
trans_st = fmt_stats(all_sb_trans)

buckets = {"<=30": [], "31-60": [], "61-100": [], "100+": []}
for s in all_scatter:
    cl, lat = s["char_len"], s["latency_ms"]
    if cl <= 30: buckets["<=30"].append(lat)
    elif cl <= 60: buckets["31-60"].append(lat)
    elif cl <= 100: buckets["61-100"].append(lat)
    else: buckets["100+"].append(lat)

# ── Output ───────────────────────────────────────────────────────────────────

print()
print("=" * 64)
print(f"  WIGVO Calls Report — Since {since}")
print("=" * 64)

# ── Overview ──
print()
print("-" * 64)
print("  [Overview]")
print("-" * 64)
print(f"  Total calls:               {total_calls}")
print(f"  Instrumented (w/ metrics): {n_instrumented}")
print()
print("  Status:")
for s, cnt in status_counter.most_common():
    pct_v = cnt / total_calls * 100
    bar = "#" * int(pct_v / 5)
    print(f"    {s:<15s} {cnt:>4d} ({pct_v:5.1f}%) {bar}")
print()
print("  Mode:")
for m, cnt in mode_counter.most_common():
    pct_v = cnt / total_calls * 100
    print(f"    {m:<10s} {cnt:>4d} ({pct_v:5.1f}%)")

# ── Incremental Persistence Check ──
print()
print("-" * 64)
print("  [Incremental Persistence Check]")
print("-" * 64)

# Count fields present
has_call_sid = sum(1 for c in data if c.get("call_sid"))
has_duration = sum(1 for c in data if c.get("duration_s"))
has_comm_mode = sum(1 for c in data if c.get("communication_mode"))
has_cost = sum(1 for c in data if (c.get("call_result_data") or {}).get("cost_usd"))
has_transcript = sum(1 for c in data if c.get("transcript_bilingual"))

print(f"  call_sid:          {has_call_sid:>3d}/{total_calls}")
print(f"  duration_s:        {has_duration:>3d}/{total_calls}")
print(f"  communication_mode:{has_comm_mode:>3d}/{total_calls}")
print(f"  cost_usd:          {has_cost:>3d}/{total_calls}")
print(f"  transcript:        {has_transcript:>3d}/{total_calls}")
print(f"  metrics:           {n_instrumented:>3d}/{total_calls}")

# Check completed calls with missing fields
skip_statuses = {"IN_PROGRESS", "INITIATED", "NULL"}
incomplete = []
for c in data:
    if c.get("status") in skip_statuses:
        continue
    issues = []
    if not c.get("call_sid"): issues.append("call_sid")
    if not c.get("duration_s"): issues.append("duration_s")
    if not c.get("communication_mode"): issues.append("comm_mode")
    crd = c.get("call_result_data") or {}
    if not crd.get("metrics"): issues.append("metrics")
    if not crd.get("cost_usd"): issues.append("cost_usd")
    if issues:
        incomplete.append((c["id"][:8], c.get("status", "?"), c.get("result", "?"), issues))

if incomplete:
    print(f"\n  [!] {len(incomplete)} completed call(s) with missing fields:")
    for cid, st, res, issues in incomplete[:10]:
        print(f"    {cid}  {st}/{res}  missing: {', '.join(issues)}")
    if len(incomplete) > 10:
        print(f"    ... and {len(incomplete) - 10} more")
else:
    print(f"\n  [OK] All completed calls have expected fields")

# ── Session A ──
print()
print("-" * 64)
print(f"  [Session A: Caller -> Recipient]  N={sa_st['n']} turns")
print("-" * 64)
if sa_st["n"]:
    print(f"  P50: {sa_st['p50']:.0f}ms  P95: {sa_st['p95']:.0f}ms  Mean: {sa_st['mean']:.0f}ms  Max: {sa_st['max']:.0f}ms")

# ── Session B ──
print()
print("-" * 64)
print(f"  [Session B: Recipient -> Caller]  N={e2e_st['n']} turns")
print("-" * 64)
if e2e_st["n"]:
    print(f"  E2E        P50: {e2e_st['p50']:.0f}ms  P95: {e2e_st['p95']:.0f}ms  Mean: {e2e_st['mean']:.0f}ms  Max: {e2e_st['max']:.0f}ms")
if stt_st["n"]:
    print(f"  STT        P50: {stt_st['p50']:.0f}ms  P95: {stt_st['p95']:.0f}ms  Mean: {stt_st['mean']:.0f}ms")
if trans_st["n"]:
    print(f"  Translate  P50: {trans_st['p50']:.0f}ms  P95: {trans_st['p95']:.0f}ms  Mean: {trans_st['mean']:.0f}ms")
if all_paired_e2e:
    ratios = [stt / e2e for stt, e2e in zip(all_paired_stt, all_paired_e2e) if e2e > 0]
    stt_pct = sum(ratios) / len(ratios) * 100 if ratios else 0
    print(f"  STT % of E2E: {stt_pct:.1f}%  (N={len(ratios)} paired turns)")

# ── Utterance Analysis ──
print()
print("-" * 64)
print("  [Utterance Analysis]")
print("-" * 64)
if all_scatter:
    xs = [s["char_len"] for s in all_scatter]
    ys = [s["latency_ms"] for s in all_scatter]
    r = pearson_r(xs, ys)
    print(f"  Pearson r (char_len vs SB latency): {r:.3f}")
    print()
    print(f"  {'Range':>8s}  {'N':>4s}  {'P50':>8s}  {'P95':>8s}  {'Mean':>8s}")
    for label, vals in buckets.items():
        if vals:
            st = fmt_stats(vals)
            print(f"  {label:>8s}  {st['n']:4d}  {st['p50']:7.0f}ms  {st['p95']:7.0f}ms  {st['mean']:7.0f}ms")
        else:
            print(f"  {label:>8s}     0         -         -         -")
else:
    print("  (no scatter data)")

# ── Echo & VAD ──
print()
print("-" * 64)
print("  [Echo & VAD]")
print("-" * 64)
echo_per = total_echo_supp / n_instrumented if n_instrumented else 0
vad_per = total_vad_false / n_instrumented if n_instrumented else 0
print(f"  Echo gate activations:   {total_echo_supp:>4d} total  ({echo_per:.1f}/call)")
print(f"  Echo gate breakthroughs: {total_echo_breakthroughs:>4d}  (callee interrupted during TTS — expected)")
print(f"  Echo-induced loops:      {total_echo_loops:>4d} / {n_instrumented} calls")
print(f"  VAD false triggers:      {total_vad_false:>4d} total  ({vad_per:.1f}/call)")
print(f"  Hallucinations blocked:  {total_hallucinations:>4d}")
print(f"  Interrupts:              {total_interrupts:>4d}")
print(f"  Guardrail L2/L3:         {total_guardrail_l2}/{total_guardrail_l3}")

# ── Cost ──
print()
print("-" * 64)
print("  [Cost]")
print("-" * 64)
print(f"  Total tokens:    {total_tokens_sum:>10,d}")
est_tag = f"  ({n_estimated} est.)" if n_estimated else ""
print(f"  Total cost:      ${total_cost_usd:.4f}{est_tag}")
print(f"  Total duration:  {total_duration:.0f}s ({total_duration/60:.1f}min)")
if total_duration > 0:
    cpm = total_cost_usd / (total_duration / 60)
    print(f"  Cost per minute: ${cpm:.4f}")
if n_instrumented:
    print(f"  Avg cost/call:   ${total_cost_usd/n_instrumented:.4f}")

# ── Call List ──
print()
print("-" * 64)
print("  [Call List]")
print("-" * 64)
print(f"  {'ID':>8s}  {'Status':>12s}  {'Result':>8s}  {'Mode':>6s}  {'Dur':>6s}  {'Tokens':>7s}  {'Cost':>8s}  Created")
for c in data:
    cid = c["id"][:8]
    st = c.get("status") or "?"
    res = c.get("result") or "?"
    raw_mode = c.get("communication_mode") or "?"
    mode = MODE_LABELS.get(raw_mode, raw_mode[:6])
    dur = c.get("duration_s")
    dur_s = f"{dur:.0f}s" if dur else "-"
    tok = c.get("total_tokens") or 0
    crd = c.get("call_result_data") or {}
    cost = crd.get("cost_usd", 0) or 0
    cost_s = f"${cost:.4f}" if cost else "- (missing)"
    created = (c.get("created_at") or "?")[:16]
    print(f"  {cid:>8s}  {st:>12s}  {res:>8s}  {mode:>6s}  {dur_s:>6s}  {tok:>7,d}  {cost_s:>8s}  {created}")

print()
print("=" * 64)
PYEOF
