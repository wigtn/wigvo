#!/usr/bin/env bash
# check_last_call.sh — 최근 1건 통화 메트릭 상세 리포트 (논문 평가지표 기준)
# 의존성: curl, python3 (추가 패키지 불필요)
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

# ── Fetch ────────────────────────────────────────────────────────────────────
TMPFILE=$(mktemp)
trap "rm -f $TMPFILE" EXIT

curl -s "$API/calls?select=id,call_sid,status,result,source_language,target_language,communication_mode,call_mode,duration_s,total_tokens,auto_ended,call_result_data,cost_tokens,transcript_bilingual,created_at&call_result_data->>metrics=not.is.null&order=created_at.desc&limit=1" \
  "${AUTH_HEADERS[@]}" \
  -H "Accept: application/json" > "$TMPFILE"

# ── Process ──────────────────────────────────────────────────────────────────
python3 - "$TMPFILE" << 'PYEOF'
import json, sys, math

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

if not data:
    print("No calls with metrics found.")
    sys.exit(0)

call = data[0]
metrics = (call.get("call_result_data") or {}).get("metrics", {})
cost_tokens = call.get("cost_tokens") or {}
transcripts = call.get("transcript_bilingual") or []

# ── [기본 정보] ───────────────────────────────────────────────────────────────

print()
print("=" * 64)
print("  WIGVO Call Report")
print("=" * 64)
print()
print(f"  Call ID:    {call['id']}")
print(f"  Call SID:   {call.get('call_sid') or 'N/A'}")
print(f"  Status:     {call.get('status', '?')} / {call.get('result', '?')}")
print(f"  Languages:  {call.get('source_language', '?')} -> {call.get('target_language', '?')}")
comm = call.get("communication_mode") or "N/A"
cmode = call.get("call_mode") or "N/A"
print(f"  Mode:       {cmode} / {comm}")
dur = call.get("duration_s")
print(f"  Duration:   {dur:.1f}s" if dur else "  Duration:   N/A")
print(f"  Auto-ended: {call.get('auto_ended', False)}")
print(f"  Created:    {(call.get('created_at') or '?')[:19]}")

# ── [Session A: Caller -> Recipient] ─────────────────────────────────────────

sa = metrics.get("session_a_latencies_ms", [])
sa_st = fmt_stats(sa)

print()
print("-" * 64)
print("  [Session A: Caller -> Recipient]")
print("-" * 64)
print(f"  Turns: {sa_st['n']}")
if sa_st["n"]:
    print(f"  P50: {sa_st['p50']:.0f}ms  P95: {sa_st['p95']:.0f}ms  Mean: {sa_st['mean']:.0f}ms  Max: {sa_st['max']:.0f}ms")
else:
    print("  (no data)")

# First Message Latency (may be a single float or a list)
fml_raw = metrics.get("first_message_latency_ms")
if isinstance(fml_raw, (int, float)):
    fml = [fml_raw]
elif isinstance(fml_raw, list):
    fml = fml_raw
else:
    fml = []
fml_st = fmt_stats(fml)
print()
if fml_st["n"]:
    print(f"  First Message Latency:")
    print(f"  P50: {fml_st['p50']:.0f}ms  P95: {fml_st['p95']:.0f}ms  Mean: {fml_st['mean']:.0f}ms  (N={fml_st['n']})")
else:
    print("  First Message Latency: (no first message data)")

# ── [Session B: Recipient -> Caller] ─────────────────────────────────────────

sb_e2e = metrics.get("session_b_e2e_latencies_ms", [])
sb_stt = metrics.get("session_b_stt_latencies_ms", [])

# Translation = E2E - STT (paired by turn index)
sb_trans = []
paired_e2e = []
paired_stt = []
paired_n = min(len(sb_e2e), len(sb_stt))
for i in range(paired_n):
    paired_e2e.append(sb_e2e[i])
    paired_stt.append(sb_stt[i])
    t = sb_e2e[i] - sb_stt[i]
    if t >= 0:
        sb_trans.append(t)

e2e_st = fmt_stats(sb_e2e)
stt_st = fmt_stats(sb_stt)
trans_st = fmt_stats(sb_trans)

print()
print("-" * 64)
print("  [Session B: Recipient -> Caller]")
print("-" * 64)
print(f"  Turns: {e2e_st['n']}")
if e2e_st["n"]:
    print(f"  E2E        P50: {e2e_st['p50']:.0f}ms  P95: {e2e_st['p95']:.0f}ms  Mean: {e2e_st['mean']:.0f}ms  Max: {e2e_st['max']:.0f}ms")
if stt_st["n"]:
    print(f"  STT        P50: {stt_st['p50']:.0f}ms  P95: {stt_st['p95']:.0f}ms  Mean: {stt_st['mean']:.0f}ms")
if trans_st["n"]:
    print(f"  Translate  P50: {trans_st['p50']:.0f}ms  P95: {trans_st['p95']:.0f}ms  Mean: {trans_st['mean']:.0f}ms")
if paired_e2e:
    valid_ratios = []
    misaligned = 0
    for stt, e2e in zip(paired_stt, paired_e2e):
        if e2e <= 0: continue
        if stt > e2e:
            misaligned += 1
            continue
        valid_ratios.append(stt / e2e)
    stt_pct = sum(valid_ratios) / len(valid_ratios) * 100 if valid_ratios else 0
    mis_tag = f"  ({misaligned} misaligned skipped)" if misaligned else ""
    print(f"  STT % of E2E: {stt_pct:.1f}%  (N={len(valid_ratios)} paired turns){mis_tag}")

sb_speech_dur = metrics.get("session_b_speech_durations_ms", [])
sb_proc_lat = metrics.get("session_b_processing_latencies_ms", [])
sb_stt_after = metrics.get("session_b_stt_after_stop_ms", [])
sd_st = fmt_stats(sb_speech_dur)
pl_st = fmt_stats(sb_proc_lat)
sa_stop_st = fmt_stats(sb_stt_after)
if sd_st["n"]:
    print(f"  Speech Dur P50: {sd_st['p50']:.0f}ms  P95: {sd_st['p95']:.0f}ms  Mean: {sd_st['mean']:.0f}ms")
if pl_st["n"]:
    print(f"  Proc Lat   P50: {pl_st['p50']:.0f}ms  P95: {pl_st['p95']:.0f}ms  Mean: {pl_st['mean']:.0f}ms")
if sa_stop_st["n"]:
    print(f"  STT>Stop   P50: {sa_stop_st['p50']:.0f}ms  P95: {sa_stop_st['p95']:.0f}ms  Mean: {sa_stop_st['mean']:.0f}ms")

# ── [Utterance Analysis] ─────────────────────────────────────────────────────

recipients = [t for t in transcripts if t.get("role") == "recipient"]
scatter = []
paired_utt = min(len(recipients), len(sb_e2e))
for i in range(paired_utt):
    char_len = len(recipients[i].get("original_text", ""))
    scatter.append({"char_len": char_len, "latency_ms": sb_e2e[i]})

buckets = {"<=30": [], "31-60": [], "61-100": [], "100+": []}
for s in scatter:
    cl = s["char_len"]
    lat = s["latency_ms"]
    if cl <= 30:
        buckets["<=30"].append(lat)
    elif cl <= 60:
        buckets["31-60"].append(lat)
    elif cl <= 100:
        buckets["61-100"].append(lat)
    else:
        buckets["100+"].append(lat)

print()
print("-" * 64)
print("  [Utterance Analysis]")
print("-" * 64)

if scatter:
    xs = [s["char_len"] for s in scatter]
    ys = [s["latency_ms"] for s in scatter]
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
    print("  (no transcript data for scatter analysis)")

# ── [Echo & VAD] ─────────────────────────────────────────────────────────────

print()
print("-" * 64)
print("  [Echo & VAD]")
print("-" * 64)
print(f"  Echo gate activations:  {metrics.get('echo_suppressions', 0)}")
print(f"  Echo gate breakthroughs:{metrics.get('echo_gate_breakthroughs', 0)}  (callee interrupted during TTS — expected)")
print(f"  Settling breakthroughs: {metrics.get('settling_breakthroughs', 0)}")
print(f"  VAD false triggers:     {metrics.get('vad_false_triggers', 0)}")
print(f"  Hallucinations blocked: {metrics.get('hallucinations_blocked', 0)}")
print(f"  Interrupts:             {metrics.get('interrupt_count', 0)}")
print(f"  Guardrail L2/L3:        {metrics.get('guardrail_level2_count', 0)}/{metrics.get('guardrail_level3_count', 0)}")

# ── [Cost] ────────────────────────────────────────────────────────────────────

crd = call.get("call_result_data") or {}
cost_usd = crd.get("cost_usd", 0) or 0
cost_estimated = False
# fallback: compute from cost_tokens
if not cost_usd and cost_tokens:
    cost_usd = (
        cost_tokens.get("audio_input", 0) * 0.06
        + cost_tokens.get("audio_output", 0) * 0.24
        + cost_tokens.get("text_input", 0) * 0.005
        + cost_tokens.get("text_output", 0) * 0.02
        + cost_tokens.get("chat_input", 0) * 0.00015
        + cost_tokens.get("chat_output", 0) * 0.0006
    ) / 1000
    cost_estimated = True

total_tok = call.get("total_tokens") or 0

print()
print("-" * 64)
print("  [Cost]")
print("-" * 64)
print(f"  Total tokens:   {total_tok:,}")
cost_tag = " (est.)" if cost_estimated else ""
print(f"  Cost:           ${cost_usd:.4f}{cost_tag}")
if dur and dur > 0:
    cpm = cost_usd / (dur / 60)
    print(f"  Cost per minute: ${cpm:.4f}{cost_tag}")
if cost_tokens:
    print(f"  Breakdown:  audio_in={cost_tokens.get('audio_input',0):,}  audio_out={cost_tokens.get('audio_output',0):,}  text_in={cost_tokens.get('text_input',0):,}  text_out={cost_tokens.get('text_output',0):,}  chat_in={cost_tokens.get('chat_input',0):,}  chat_out={cost_tokens.get('chat_output',0):,}")

print()
print("=" * 64)
PYEOF
