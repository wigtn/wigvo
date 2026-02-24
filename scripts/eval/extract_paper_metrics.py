#!/usr/bin/env python3
"""WIGVO ACL 2026 논문용 전체 메트릭 추출.

Supabase에서 calls 테이블의 call_result_data.metrics + transcript_bilingual을
추출하여 논문 Figure/Table용 JSON + 요약 테이블을 생성한다.

Usage:
    cd apps/relay-server
    uv run python ../../scripts/eval/extract_paper_metrics.py
    uv run python ../../scripts/eval/extract_paper_metrics.py --output paper_metrics.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections import Counter

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ── Supabase ─────────────────────────────────────────────────

def fetch_all_calls() -> list[dict]:
    try:
        from supabase import create_client
    except ImportError:
        logger.error("supabase 패키지 필요: pip install supabase")
        sys.exit(1)

    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("SUPABASE_SERVICE_KEY")
           or os.environ.get("SUPABASE_KEY"))
    if not url or not key:
        logger.error("SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY 환경변수 필요")
        sys.exit(1)

    client = create_client(url, key)
    result = (
        client.table("calls")
        .select("id, status, communication_mode, source_language, target_language, "
                "transcript_bilingual, call_result_data, duration_s, created_at")
        .order("created_at", desc=True)
        .limit(1000)
        .execute()
    )
    return result.data


# ── 유틸리티 ─────────────────────────────────────────────────

def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100)
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": 0, "std": 0, "min": 0,
                "p50": 0, "p75": 0, "p90": 0, "p95": 0, "p99": 0, "max": 0}
    n = len(values)
    mean = sum(values) / n
    std = (sum((v - mean) ** 2 for v in values) / n) ** 0.5
    return {
        "n": n,
        "mean": round(mean, 1),
        "std": round(std, 1),
        "min": round(min(values), 1),
        "p50": round(pct(values, 50), 1),
        "p75": round(pct(values, 75), 1),
        "p90": round(pct(values, 90), 1),
        "p95": round(pct(values, 95), 1),
        "p99": round(pct(values, 99), 1),
        "max": round(max(values), 1),
    }


def histogram_buckets(values: list[float], bins: int = 10) -> list[dict]:
    if not values:
        return []
    mn, mx = min(values), max(values)
    if mn == mx:
        return [{"lo": mn, "hi": mx, "count": len(values)}]
    width = (mx - mn) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - mn) / width), bins - 1)
        counts[idx] += 1
    return [{"lo": round(mn + i * width), "hi": round(mn + (i + 1) * width),
             "count": counts[i]} for i in range(bins)]


def _pearson(x: list[float], y: list[float]) -> float:
    """Pearson 상관계수 계산."""
    n = len(x)
    if n < 2:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sx = sum((xi - mx) ** 2 for xi in x) ** 0.5
    sy = sum((yi - my) ** 2 for yi in y) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)


# ── 추출 ────────────────────────────────────────────────────

def extract_all(calls: list[dict]) -> dict:
    # 메트릭이 있는 통화만 분리
    calls_with_metrics = []
    for c in calls:
        m = (c.get("call_result_data") or {}).get("metrics")
        if m:
            calls_with_metrics.append((c, m))

    total_calls = len(calls)
    metric_calls = len(calls_with_metrics)

    # ── 1. Session A ────────────────────────────────────────
    sa_latencies: list[float] = []
    echo_per_call: list[int] = []
    guardrail_l2_total = 0
    guardrail_l3_total = 0
    hallucinations_total = 0
    interrupt_per_call: list[int] = []
    echo_breakthroughs_total = 0
    echo_loops_total = 0
    turn_total = 0

    for c, m in calls_with_metrics:
        sa_latencies.extend(m.get("session_a_latencies_ms", []))
        echo_per_call.append(m.get("echo_suppressions", 0))
        guardrail_l2_total += m.get("guardrail_level2_count", 0)
        guardrail_l3_total += m.get("guardrail_level3_count", 0)
        hallucinations_total += m.get("hallucinations_blocked", 0)
        interrupt_per_call.append(m.get("interrupt_count", 0))
        echo_breakthroughs_total += m.get("echo_gate_breakthroughs", 0)
        echo_loops_total += m.get("echo_loops_detected", 0)
        turn_total += m.get("turn_count", 0)

    echo_0 = sum(1 for e in echo_per_call if e == 0)
    echo_1 = sum(1 for e in echo_per_call if e == 1)
    echo_2plus = sum(1 for e in echo_per_call if e >= 2)

    int_0 = sum(1 for i in interrupt_per_call if i == 0)
    int_1 = sum(1 for i in interrupt_per_call if i == 1)
    int_2plus = sum(1 for i in interrupt_per_call if i >= 2)

    session_a = {
        "latency_ms": stats(sa_latencies),
        "latency_histogram": histogram_buckets(sa_latencies, 10),
        "latency_raw": [round(v, 1) for v in sa_latencies],
        "echo_suppression": {
            "total": sum(echo_per_call),
            "per_call_distribution": {
                "0_calls": echo_0,
                "1_call": echo_1,
                "2plus_calls": echo_2plus,
                "0_pct": round(echo_0 / metric_calls * 100, 1) if metric_calls else 0,
                "1_pct": round(echo_1 / metric_calls * 100, 1) if metric_calls else 0,
                "2plus_pct": round(echo_2plus / metric_calls * 100, 1) if metric_calls else 0,
            },
            "breakthroughs": echo_breakthroughs_total,
            "loops_detected": echo_loops_total,
        },
        "guardrail": {
            "level2_corrections": guardrail_l2_total,
            "level3_blocks": guardrail_l3_total,
            "total": guardrail_l2_total + guardrail_l3_total,
        },
        "hallucinations_blocked": hallucinations_total,
        "interrupt": {
            "total": sum(interrupt_per_call),
            "per_call_distribution": {
                "0_calls": int_0,
                "1_call": int_1,
                "2plus_calls": int_2plus,
            },
        },
        "total_turns": turn_total,
    }

    # ── 2. Session B ────────────────────────────────────────
    sb_e2e: list[float] = []
    sb_stt: list[float] = []
    first_msg: list[float] = []
    vad_false_total = 0

    for c, m in calls_with_metrics:
        sb_e2e.extend(m.get("session_b_e2e_latencies_ms", []))
        sb_stt.extend(m.get("session_b_stt_latencies_ms", []))
        fm = m.get("first_message_latency_ms", 0)
        if fm > 0:
            first_msg.append(fm)
        vad_false_total += m.get("vad_false_triggers", 0)

    # Translation = E2E - STT (paired)
    sb_trans: list[float] = []
    for c, m in calls_with_metrics:
        e2e_list = m.get("session_b_e2e_latencies_ms", [])
        stt_list = m.get("session_b_stt_latencies_ms", [])
        paired = min(len(e2e_list), len(stt_list))
        for i in range(paired):
            t = e2e_list[i] - stt_list[i]
            if t >= 0:
                sb_trans.append(t)

    # 발화 길이 구간별 레이턴시
    length_buckets: dict[str, list[float]] = {
        "0-30": [], "31-60": [], "61-100": [], "100+": []
    }
    scatter_data: list[dict] = []

    for c, m in calls_with_metrics:
        e2e_list = m.get("session_b_e2e_latencies_ms", [])
        transcripts = c.get("transcript_bilingual") or []
        recipients = [t for t in transcripts if t.get("role") == "recipient"]
        paired = min(len(recipients), len(e2e_list))
        for i in range(paired):
            text = recipients[i].get("original_text", "")
            char_len = len(text)
            lat = e2e_list[i]
            scatter_data.append({"char_len": char_len, "latency_ms": round(lat, 1)})
            if char_len <= 30:
                length_buckets["0-30"].append(lat)
            elif char_len <= 60:
                length_buckets["31-60"].append(lat)
            elif char_len <= 100:
                length_buckets["61-100"].append(lat)
            else:
                length_buckets["100+"].append(lat)

    length_bucket_stats = {}
    for k, v in length_buckets.items():
        length_bucket_stats[k] = {
            "n": len(v),
            "mean": round(sum(v) / len(v), 1) if v else 0,
            "p50": round(pct(v, 50), 1),
            "p95": round(pct(v, 95), 1),
        }

    # Pearson r
    xs = [s["char_len"] for s in scatter_data]
    ys = [s["latency_ms"] for s in scatter_data]
    pearson_r = _pearson(xs, ys)

    session_b = {
        "e2e_latency_ms": stats(sb_e2e),
        "e2e_latency_histogram": histogram_buckets(sb_e2e, 10),
        "e2e_latency_raw": [round(v, 1) for v in sb_e2e],
        "stt_latency_ms": stats(sb_stt),
        "stt_latency_raw": [round(v, 1) for v in sb_stt],
        "translation_latency_ms": stats(sb_trans),
        "translation_latency_raw": [round(v, 1) for v in sb_trans],
        "first_message_latency_ms": stats(first_msg),
        "length_vs_latency": {
            "buckets": length_bucket_stats,
            "pearson_r_char_len": round(pearson_r, 3),
            "scatter": scatter_data,
        },
    }

    # ── 2b. Session B — Mode Breakdown ────────────────────────
    mode_breakdown = {}
    for mode_key in ["voice_to_voice", "text_to_voice", "voice_to_text"]:
        mode_metrics = [(c, m) for c, m in calls_with_metrics
                        if c.get("communication_mode") == mode_key]
        if not mode_metrics:
            continue
        mode_e2e: list[float] = []
        mode_stt: list[float] = []
        mode_proc: list[float] = []
        mode_stt_after: list[float] = []
        for c, m in mode_metrics:
            mode_e2e.extend(m.get("session_b_e2e_latencies_ms", []))
            mode_stt.extend(m.get("session_b_stt_latencies_ms", []))
            mode_proc.extend(m.get("session_b_processing_latencies_ms", []))
            mode_stt_after.extend(m.get("session_b_stt_after_stop_ms", []))
        mode_breakdown[mode_key] = {
            "calls": len(mode_metrics),
            "e2e_latency_ms": stats(mode_e2e),
            "stt_latency_ms": stats(mode_stt),
            "processing_latency_ms": stats(mode_proc),
            "stt_after_stop_ms": stats(mode_stt_after),
        }

    # ── 3. 통화 결과 분포 ──────────────────────────────────
    status_counter = Counter(c.get("status", "UNKNOWN") for c in calls)
    mode_counter = Counter(c.get("communication_mode", "unknown") for c in calls)

    call_results = {
        "total_calls": total_calls,
        "calls_with_metrics": metric_calls,
        "status_distribution": {
            k: {"count": v, "pct": round(v / total_calls * 100, 1)}
            for k, v in status_counter.most_common()
        },
        "mode_distribution": {
            k: {"count": v, "pct": round(v / total_calls * 100, 1)}
            for k, v in mode_counter.most_common()
        },
    }

    # Duration 분포 (성공 통화만)
    durations = [c.get("duration_s", 0) for c in calls
                 if c.get("duration_s") and c.get("duration_s") > 0]
    call_results["duration_s"] = stats(durations)

    # ── 4. VAD 메트릭 ──────────────────────────────────────
    vad_metrics = {
        "vad_false_triggers_total": vad_false_total,
        "vad_false_triggers_per_call": round(vad_false_total / metric_calls, 2) if metric_calls else 0,
    }

    # ── 5. 이상치 ──────────────────────────────────────────
    # Session A top 5
    sa_outliers = _extract_outliers_a(calls_with_metrics)
    # Session B top 5
    sb_outliers = _extract_outliers_b(calls_with_metrics)

    return {
        "meta": {
            "total_calls": total_calls,
            "calls_with_metrics": metric_calls,
            "extraction_note": "ACL 2026 System Demonstrations - WIGVO metrics",
        },
        "session_a": session_a,
        "session_b": session_b,
        "session_b_by_mode": mode_breakdown,
        "call_results": call_results,
        "vad": vad_metrics,
        "outliers": {
            "session_a_top5": sa_outliers,
            "session_b_top5": sb_outliers,
        },
    }


def _extract_outliers_a(calls_with_metrics: list[tuple[dict, dict]]) -> list[dict]:
    """Session A 레이턴시 상위 5건."""
    entries: list[dict] = []
    for c, m in calls_with_metrics:
        sa_list = m.get("session_a_latencies_ms", [])
        transcripts = c.get("transcript_bilingual") or []
        users = [t for t in transcripts if t.get("role") == "user"]
        for i, lat in enumerate(sa_list):
            text = users[i].get("original_text", "N/A") if i < len(users) else "N/A"
            translated = users[i].get("translated_text", "N/A") if i < len(users) else "N/A"
            entries.append({
                "call_id": c["id"][:8],
                "latency_ms": round(lat, 1),
                "original": text[:100],
                "translated": translated[:100],
                "char_len": len(text) if text != "N/A" else 0,
                "mode": c.get("communication_mode", "unknown"),
            })
    entries.sort(key=lambda x: x["latency_ms"], reverse=True)
    return entries[:5]


def _extract_outliers_b(calls_with_metrics: list[tuple[dict, dict]]) -> list[dict]:
    """Session B 레이턴시 상위 5건."""
    entries: list[dict] = []
    for c, m in calls_with_metrics:
        e2e_list = m.get("session_b_e2e_latencies_ms", [])
        stt_list = m.get("session_b_stt_latencies_ms", [])
        transcripts = c.get("transcript_bilingual") or []
        recipients = [t for t in transcripts if t.get("role") == "recipient"]
        for i, lat in enumerate(e2e_list):
            text = recipients[i].get("original_text", "N/A") if i < len(recipients) else "N/A"
            translated = recipients[i].get("translated_text", "N/A") if i < len(recipients) else "N/A"
            stt_ms = stt_list[i] if i < len(stt_list) else None
            entries.append({
                "call_id": c["id"][:8],
                "latency_ms": round(lat, 1),
                "stt_ms": round(stt_ms, 1) if stt_ms is not None else None,
                "translation_ms": round(lat - stt_ms, 1) if stt_ms is not None else None,
                "original": text[:100],
                "translated": translated[:100],
                "char_len": len(text) if text != "N/A" else 0,
                "mode": c.get("communication_mode", "unknown"),
            })
    entries.sort(key=lambda x: x["latency_ms"], reverse=True)
    return entries[:5]


# ── 출력 ────────────────────────────────────────────────────

def print_tables(data: dict) -> None:
    sa = data["session_a"]
    sb = data["session_b"]
    cr = data["call_results"]
    vad = data["vad"]
    out = data["outliers"]
    meta = data["meta"]

    print("\n" + "=" * 76)
    print("  WIGVO ACL 2026 — Full Metrics Report")
    print(f"  Total calls: {meta['total_calls']}  |  With metrics: {meta['calls_with_metrics']}")
    print("=" * 76)

    # ── 1. Session A ──
    s = sa["latency_ms"]
    print(f"\n{'─'*76}")
    print(f"  [1] SESSION A METRICS  (User → Recipient)  N={s['n']}")
    print(f"{'─'*76}")
    print(f"\n  Latency (ms):")
    print(f"  {'P50':>6} {'P75':>7} {'P90':>7} {'P95':>7} {'P99':>7} {'Max':>7} {'Mean':>7} {'Std':>7}")
    print(f"  {s['p50']:6.0f} {s['p75']:7.0f} {s['p90']:7.0f} {s['p95']:7.0f} {s['p99']:7.0f} "
          f"{s['max']:7.0f} {s['mean']:7.0f} {s['std']:7.0f}")

    e = sa["echo_suppression"]
    d = e["per_call_distribution"]
    print(f"\n  Echo Suppression (per call):")
    print(f"    0건: {d['0_calls']}콜 ({d['0_pct']}%)  |  1건: {d['1_call']}콜 ({d['1_pct']}%)  |  "
          f"2+건: {d['2plus_calls']}콜 ({d['2plus_pct']}%)")
    print(f"    Total suppressions: {e['total']}  |  Breakthroughs: {e['breakthroughs']}  |  "
          f"Echo loops: {e['loops_detected']}")

    g = sa["guardrail"]
    print(f"\n  Guardrail: L2(correction)={g['level2_corrections']}  L3(block)={g['level3_blocks']}  "
          f"Total={g['total']}")
    print(f"  Hallucinations blocked: {sa['hallucinations_blocked']}")

    it = sa["interrupt"]
    itd = it["per_call_distribution"]
    print(f"  Interrupt: total={it['total']}  (0건:{itd['0_calls']}콜  1건:{itd['1_call']}콜  "
          f"2+건:{itd['2plus_calls']}콜)")
    print(f"  Total turns: {sa['total_turns']}")

    # ── 2. Session B ──
    print(f"\n{'─'*76}")
    print(f"  [2] SESSION B METRICS  (Recipient → User)")
    print(f"{'─'*76}")

    for label, key in [("E2E", "e2e_latency_ms"),
                       ("STT (Whisper)", "stt_latency_ms"),
                       ("Translation", "translation_latency_ms"),
                       ("First Message", "first_message_latency_ms")]:
        s = sb[key]
        if s["n"] == 0:
            continue
        print(f"\n  {label} Latency (ms)  N={s['n']}:")
        print(f"  {'P50':>6} {'P75':>7} {'P90':>7} {'P95':>7} {'P99':>7} {'Max':>7} {'Mean':>7} {'Std':>7}")
        print(f"  {s['p50']:6.0f} {s['p75']:7.0f} {s['p90']:7.0f} {s['p95']:7.0f} {s['p99']:7.0f} "
              f"{s['max']:7.0f} {s['mean']:7.0f} {s['std']:7.0f}")

    lb = sb["length_vs_latency"]["buckets"]
    print(f"\n  Utterance Length vs Latency:")
    print(f"  {'Range':>8}  {'N':>4}  {'Mean':>7}  {'P50':>7}  {'P95':>7}")
    for rng in ["0-30", "31-60", "61-100", "100+"]:
        b = lb[rng]
        print(f"  {rng:>8}  {b['n']:4d}  {b['mean']:6.0f}ms  {b['p50']:6.0f}ms  {b['p95']:6.0f}ms")
    print(f"\n  Pearson r (char_len vs latency): {sb['length_vs_latency']['pearson_r_char_len']:.3f}")

    # Mode breakdown
    sb_mode = data.get("session_b_by_mode", {})
    if sb_mode:
        print(f"\n  Session B by Communication Mode:")
        print(f"  {'Mode':<20} {'Calls':>5} {'E2E P50':>8} {'E2E P95':>8} {'STT P50':>8} {'Proc P50':>8}")
        print(f"  {'─'*20} {'─'*5} {'─'*8} {'─'*8} {'─'*8} {'─'*8}")
        for mode, ms in sb_mode.items():
            e = ms["e2e_latency_ms"]
            st = ms["stt_latency_ms"]
            pr = ms["processing_latency_ms"]
            print(f"  {mode:<20} {ms['calls']:5d} {e['p50']:7.0f}ms {e['p95']:7.0f}ms "
                  f"{st['p50']:7.0f}ms {pr['p50']:7.0f}ms")

    # ── 3. Call Results ──
    print(f"\n{'─'*76}")
    print(f"  [3] CALL RESULTS DISTRIBUTION")
    print(f"{'─'*76}")

    print(f"\n  Status:")
    for status, info in cr["status_distribution"].items():
        print(f"    {status:<20} {info['count']:>4}콜  ({info['pct']}%)")

    print(f"\n  Communication Mode:")
    for mode, info in cr["mode_distribution"].items():
        print(f"    {mode:<20} {info['count']:>4}콜  ({info['pct']}%)")

    d = cr["duration_s"]
    if d["n"] > 0:
        print(f"\n  Call Duration (seconds):  N={d['n']}")
        print(f"    Mean={d['mean']:.0f}s  P50={d['p50']:.0f}s  P95={d['p95']:.0f}s  Max={d['max']:.0f}s")

    # ── 4. VAD ──
    print(f"\n{'─'*76}")
    print(f"  [4] VAD METRICS")
    print(f"{'─'*76}")
    print(f"  False triggers total: {vad['vad_false_triggers_total']}")
    print(f"  False triggers per call: {vad['vad_false_triggers_per_call']}")

    # ── 5. Outliers ──
    print(f"\n{'─'*76}")
    print(f"  [5] OUTLIERS")
    print(f"{'─'*76}")

    print(f"\n  Session A Top 5:")
    for i, o in enumerate(out["session_a_top5"], 1):
        print(f"    #{i} {o['latency_ms']:.0f}ms  [{o['mode']}]  ({o['char_len']}자)")
        print(f"       원문: {o['original'][:60]}")
        print(f"       번역: {o['translated'][:60]}")

    print(f"\n  Session B Top 5:")
    for i, o in enumerate(out["session_b_top5"], 1):
        stt_str = f"STT={o['stt_ms']:.0f}ms  Trans={o['translation_ms']:.0f}ms" if o["stt_ms"] else "no breakdown"
        print(f"    #{i} {o['latency_ms']:.0f}ms  [{o['mode']}]  ({o['char_len']}자)  {stt_str}")
        print(f"       원문: {o['original'][:60]}")
        print(f"       번역: {o['translated'][:60]}")

    print("\n" + "=" * 76)


def save_json(data: dict, path: str) -> None:
    """matplotlib용 JSON 저장. raw latency 리스트 포함."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("JSON saved to %s", path)


def main() -> None:
    parser = argparse.ArgumentParser(description="WIGVO ACL 2026 Paper Metrics")
    parser.add_argument("--output", default="paper_metrics.json", help="JSON output path")
    args = parser.parse_args()

    logger.info("Fetching all calls from Supabase...")
    calls = fetch_all_calls()
    logger.info("Fetched %d calls", len(calls))

    if not calls:
        logger.error("No calls found")
        sys.exit(1)

    data = extract_all(calls)
    print_tables(data)
    save_json(data, args.output)


if __name__ == "__main__":
    main()
