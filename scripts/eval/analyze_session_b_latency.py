#!/usr/bin/env python3
"""WIGVO Session B 레이턴시 심층 분석.

call_result_data.metrics에서 Session B 레이턴시를 추출하여
분포, 원인 분리, 발화 길이 상관관계, 이상치 분석을 수행한다.

Usage:
    cd apps/relay-server
    uv run python ../../scripts/eval/analyze_session_b_latency.py --limit 50
    uv run python ../../scripts/eval/analyze_session_b_latency.py --all
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ── Supabase 조회 ──────────────────────────────────────────────

def fetch_calls(limit: int = 50) -> list[dict]:
    """Supabase에서 metrics + transcript가 있는 통화를 조회."""
    try:
        from supabase import create_client
    except ImportError:
        logger.error("supabase 패키지 필요: pip install supabase")
        sys.exit(1)

    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY 환경변수 필요")
        sys.exit(1)

    client = create_client(url, key)
    result = (
        client.table("calls")
        .select("id, status, communication_mode, source_language, target_language, "
                "transcript_bilingual, call_result_data, duration_s, created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    # metrics가 있는 통화만 필터 (call_result_data == {} 이면 falsy)
    calls = result.data
    logger.info("Raw rows from DB: %d", len(calls))
    # 빈 dict도 허용, metrics 키 존재 여부는 분석 단계에서 필터
    return calls


# ── 유틸리티 ─────────────────────────────────────────────────

def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100)
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def histogram(values: list[float], bins: int = 10) -> list[tuple[str, int, str]]:
    """값을 bins개 버킷으로 나눠 히스토그램 데이터 반환."""
    if not values:
        return []
    mn, mx = min(values), max(values)
    if mn == mx:
        return [(f"{mn:.0f}", len(values), "█" * 40)]

    width = (mx - mn) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - mn) / width), bins - 1)
        counts[idx] += 1

    max_count = max(counts) if counts else 1
    rows = []
    for i in range(bins):
        lo = mn + i * width
        hi = lo + width
        bar_len = int(counts[i] / max_count * 40) if max_count > 0 else 0
        rows.append((f"{lo:7.0f}-{hi:7.0f}", counts[i], "█" * bar_len))
    return rows


# ── 분석 1: Session B 레이턴시 분포 ────────────────────────────

def analyze_distribution(calls: list[dict]) -> dict:
    """Session B E2E + STT 레이턴시 분포 분석."""
    e2e_all: list[float] = []
    stt_all: list[float] = []

    for call in calls:
        metrics = (call.get("call_result_data") or {}).get("metrics")
        if not metrics:
            continue
        e2e_all.extend(metrics.get("session_b_e2e_latencies_ms", []))
        stt_all.extend(metrics.get("session_b_stt_latencies_ms", []))

    result = {}
    for name, vals in [("session_b_e2e", e2e_all), ("session_b_stt", stt_all)]:
        if not vals:
            result[name] = {"n": 0}
            continue
        result[name] = {
            "n": len(vals),
            "mean": sum(vals) / len(vals),
            "std": (sum((v - sum(vals)/len(vals))**2 for v in vals) / len(vals)) ** 0.5,
            "min": min(vals),
            "p50": percentile(vals, 50),
            "p75": percentile(vals, 75),
            "p90": percentile(vals, 90),
            "p95": percentile(vals, 95),
            "p99": percentile(vals, 99),
            "max": max(vals),
            "histogram": histogram(vals, 10),
            "raw": vals,
        }
    return result


# ── 분석 2: upstream vs TTS 분리 ─────────────────────────────

def analyze_latency_breakdown(calls: list[dict], mode_filter: str | None = None) -> dict:
    """Session B STT(upstream) vs 번역(translation) 시간 분리.

    Args:
        calls: Supabase에서 가져온 통화 목록
        mode_filter: communication_mode 필터 (e.g. "voice_to_voice", "text_to_voice")
    """
    stt_only: list[float] = []
    translation_only: list[float] = []
    e2e_paired: list[float] = []
    skipped_inverted = 0

    for call in calls:
        if mode_filter and call.get("communication_mode") != mode_filter:
            continue
        metrics = (call.get("call_result_data") or {}).get("metrics")
        if not metrics:
            continue
        e2e_list = metrics.get("session_b_e2e_latencies_ms", [])
        stt_list = metrics.get("session_b_stt_latencies_ms", [])

        # 새 필드가 있으면 직접 사용 (E2E - STT 역산 대신)
        proc_list = metrics.get("session_b_processing_latencies_ms", [])
        if proc_list:
            for proc_ms in proc_list:
                translation_only.append(proc_ms)
            # STT도 수집
            stt_only.extend(stt_list)
            e2e_paired.extend(e2e_list[:len(stt_list)])
            continue

        # 기존 방식: E2E - STT 역산 (레거시 데이터)
        paired = min(len(e2e_list), len(stt_list))
        for i in range(paired):
            stt_ms = stt_list[i]
            e2e_ms = e2e_list[i]
            trans_ms = e2e_ms - stt_ms
            if trans_ms >= 0:
                stt_only.append(stt_ms)
                translation_only.append(trans_ms)
                e2e_paired.append(e2e_ms)
            else:
                skipped_inverted += 1

    result = {"paired_n": len(stt_only), "skipped_inverted": skipped_inverted}
    if mode_filter:
        result["mode_filter"] = mode_filter
    for name, vals in [("stt_upstream", stt_only), ("translation", translation_only)]:
        if not vals:
            result[name] = {"n": 0}
            continue
        result[name] = {
            "n": len(vals),
            "mean": sum(vals) / len(vals),
            "p50": percentile(vals, 50),
            "p95": percentile(vals, 95),
            "max": max(vals),
        }

    # 비율 계산
    if stt_only and translation_only:
        stt_mean = sum(stt_only) / len(stt_only)
        trans_mean = sum(translation_only) / len(translation_only)
        total = stt_mean + trans_mean
        result["stt_pct"] = (stt_mean / total * 100) if total > 0 else 0
        result["translation_pct"] = (trans_mean / total * 100) if total > 0 else 0

    return result


# ── 분석 3: 발화 길이 vs 레이턴시 상관관계 ───────────────────

def analyze_length_correlation(calls: list[dict]) -> dict:
    """recipient 발화 텍스트 길이 vs Session B E2E 레이턴시."""
    scatter: list[dict] = []

    for call in calls:
        metrics = (call.get("call_result_data") or {}).get("metrics")
        transcripts = call.get("transcript_bilingual") or []
        if not metrics:
            continue

        e2e_list = metrics.get("session_b_e2e_latencies_ms", [])
        # recipient 발화만 추출 (Session B 방향)
        recipient_entries = [t for t in transcripts if t.get("role") == "recipient"]

        # recipient 발화 수와 e2e 레이턴시 수가 대응한다고 가정
        paired = min(len(recipient_entries), len(e2e_list))
        for i in range(paired):
            text = recipient_entries[i].get("original_text", "")
            char_len = len(text)
            word_len = len(text.split())
            scatter.append({
                "call_id": call["id"],
                "text": text[:80],
                "char_len": char_len,
                "word_len": word_len,
                "latency_ms": e2e_list[i],
            })

    # Pearson 상관계수 계산
    corr_char = _pearson([s["char_len"] for s in scatter], [s["latency_ms"] for s in scatter])
    corr_word = _pearson([s["word_len"] for s in scatter], [s["latency_ms"] for s in scatter])

    return {
        "n": len(scatter),
        "correlation_char_len": corr_char,
        "correlation_word_len": corr_word,
        "scatter": scatter,
    }


def _pearson(x: list[float], y: list[float]) -> float:
    """Pearson 상관계수."""
    n = len(x)
    if n < 2:
        return 0.0
    mx, my = sum(x)/n, sum(y)/n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sx = sum((xi - mx)**2 for xi in x) ** 0.5
    sy = sum((yi - my)**2 for yi in y) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)


# ── 분석 4: P95 이상 이상치 샘플 ──────────────────────────────

def analyze_outliers(calls: list[dict], dist: dict) -> list[dict]:
    """P95 이상 레이턴시를 가진 케이스 샘플."""
    e2e_stats = dist.get("session_b_e2e", {})
    if e2e_stats.get("n", 0) == 0:
        return []

    p95_threshold = e2e_stats["p95"]
    outliers: list[dict] = []

    for call in calls:
        metrics = (call.get("call_result_data") or {}).get("metrics")
        transcripts = call.get("transcript_bilingual") or []
        if not metrics:
            continue

        e2e_list = metrics.get("session_b_e2e_latencies_ms", [])
        stt_list = metrics.get("session_b_stt_latencies_ms", [])
        recipient_entries = [t for t in transcripts if t.get("role") == "recipient"]

        for i, lat in enumerate(e2e_list):
            if lat >= p95_threshold:
                text = recipient_entries[i].get("original_text", "N/A") if i < len(recipient_entries) else "N/A"
                translated = recipient_entries[i].get("translated_text", "N/A") if i < len(recipient_entries) else "N/A"
                stt_ms = stt_list[i] if i < len(stt_list) else None
                outliers.append({
                    "call_id": call["id"],
                    "latency_ms": lat,
                    "stt_ms": stt_ms,
                    "translation_ms": (lat - stt_ms) if stt_ms is not None else None,
                    "text": text,
                    "translated": translated,
                    "char_len": len(text) if text != "N/A" else 0,
                    "status": call.get("status", "unknown"),
                    "comm_mode": call.get("communication_mode", "unknown"),
                })

    outliers.sort(key=lambda x: x["latency_ms"], reverse=True)
    return outliers[:5]


# ── 분석 5: 성공/실패별 레이턴시 비교 ──────────────────────────

def analyze_by_status(calls: list[dict]) -> dict:
    """통화 성공/실패 상태별 Session B 레이턴시 비교."""
    groups: dict[str, list[float]] = {}

    for call in calls:
        metrics = (call.get("call_result_data") or {}).get("metrics")
        if not metrics:
            continue
        status = call.get("status", "unknown")
        e2e_list = metrics.get("session_b_e2e_latencies_ms", [])
        groups.setdefault(status, []).extend(e2e_list)

    result = {}
    for status, vals in sorted(groups.items()):
        if not vals:
            continue
        result[status] = {
            "n": len(vals),
            "mean": sum(vals) / len(vals),
            "p50": percentile(vals, 50),
            "p95": percentile(vals, 95),
            "max": max(vals),
        }
    return result


# ── 출력 ────────────────────────────────────────────────────

def print_report(
    dist: dict,
    breakdown: dict,
    correlation: dict,
    outliers: list[dict],
    by_status: dict,
) -> None:
    print("\n" + "=" * 72)
    print("  WIGVO Session B Latency Deep Analysis")
    print("=" * 72)

    # ── 1. 분포 ──
    for label, key in [("Session B E2E (speech→translation)", "session_b_e2e"),
                       ("Session B STT (speech→transcription)", "session_b_stt")]:
        stats = dist.get(key, {})
        if stats.get("n", 0) == 0:
            print(f"\n[{label}] No data")
            continue
        print(f"\n[1] {label}  (N={stats['n']})")
        print(f"    Mean={stats['mean']:.0f}ms  Std={stats['std']:.0f}ms")
        print(f"    Min={stats['min']:.0f}  P50={stats['p50']:.0f}  P75={stats['p75']:.0f}  "
              f"P90={stats['p90']:.0f}  P95={stats['p95']:.0f}  P99={stats['p99']:.0f}  Max={stats['max']:.0f}")
        print(f"\n    Histogram:")
        for rng, cnt, bar in stats["histogram"]:
            print(f"    {rng} ms │{cnt:4d} │ {bar}")

    # ── 2. Breakdown ──
    print(f"\n[2] Latency Breakdown (paired N={breakdown['paired_n']})")
    if breakdown["paired_n"] > 0:
        for label, key in [("STT (upstream)", "stt_upstream"), ("Translation", "translation")]:
            s = breakdown.get(key, {})
            if s.get("n", 0) == 0:
                continue
            print(f"    {label}: Mean={s['mean']:.0f}ms  P50={s['p50']:.0f}ms  "
                  f"P95={s['p95']:.0f}ms  Max={s['max']:.0f}ms")
        pct_stt = breakdown.get("stt_pct", 0)
        pct_trans = breakdown.get("translation_pct", 0)
        print(f"    Ratio: STT {pct_stt:.1f}% / Translation {pct_trans:.1f}%")
        if breakdown.get("skipped_inverted", 0) > 0:
            print(f"    ⚠ Skipped {breakdown['skipped_inverted']} inverted samples (STT > E2E)")
        if breakdown.get("mode_filter"):
            print(f"    Mode: {breakdown['mode_filter']}")
    else:
        print("    (STT latency data not available — only E2E total)")

    # ── 3. Correlation ──
    print(f"\n[3] Utterance Length vs Latency (N={correlation['n']})")
    print(f"    Pearson r (char_len):  {correlation['correlation_char_len']:.3f}")
    print(f"    Pearson r (word_len):  {correlation['correlation_word_len']:.3f}")
    if correlation["scatter"]:
        print(f"\n    Scatter sample (first 15):")
        print(f"    {'chars':>5}  {'words':>5}  {'latency':>8}  text")
        print(f"    {'─'*5}  {'─'*5}  {'─'*8}  {'─'*40}")
        for s in sorted(correlation["scatter"], key=lambda x: x["latency_ms"], reverse=True)[:15]:
            print(f"    {s['char_len']:5d}  {s['word_len']:5d}  {s['latency_ms']:7.0f}ms  {s['text'][:40]}")

    # ── 4. Outliers ──
    print(f"\n[4] P95+ Outlier Cases (top 5)")
    if not outliers:
        print("    No outliers found")
    else:
        for i, o in enumerate(outliers, 1):
            print(f"\n    #{i} — {o['latency_ms']:.0f}ms  (call: {o['call_id'][:8]}...  "
                  f"status={o['status']}  mode={o['comm_mode']})")
            if o["stt_ms"] is not None:
                print(f"       STT={o['stt_ms']:.0f}ms  Translation={o['translation_ms']:.0f}ms")
            print(f"       원문 ({o['char_len']}자): {o['text'][:70]}")
            print(f"       번역:     {o['translated'][:70]}")

    # ── 5. By Status ──
    print(f"\n[5] Latency by Call Status")
    if not by_status:
        print("    No data")
    else:
        print(f"    {'status':<15} {'N':>5}  {'Mean':>7}  {'P50':>7}  {'P95':>7}  {'Max':>7}")
        print(f"    {'─'*15} {'─'*5}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}")
        for status, s in by_status.items():
            print(f"    {status:<15} {s['n']:5d}  {s['mean']:6.0f}ms  {s['p50']:6.0f}ms  "
                  f"{s['p95']:6.0f}ms  {s['max']:6.0f}ms")

    print("\n" + "=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="WIGVO Session B Latency Analysis")
    parser.add_argument("--limit", type=int, default=50, help="조회할 통화 수")
    parser.add_argument("--all", action="store_true", help="전체 통화 (limit=1000)")
    parser.add_argument("--output", default=None, help="JSON 결과 저장 경로")
    args = parser.parse_args()

    limit = 1000 if args.all else args.limit

    logger.info("Fetching calls from Supabase (limit=%d)...", limit)
    calls = fetch_calls(limit=limit)
    if not calls:
        logger.error("No calls found")
        sys.exit(1)

    calls_with_metrics = [c for c in calls if (c.get("call_result_data") or {}).get("metrics")]
    logger.info("Found %d calls total, %d with metrics", len(calls), len(calls_with_metrics))

    if not calls_with_metrics:
        # 디버그: call_result_data 구조 확인
        sample = calls[0] if calls else {}
        crd = sample.get("call_result_data")
        logger.error("No calls have metrics in call_result_data")
        logger.info("Sample call_result_data type=%s, value=%s",
                     type(crd).__name__, str(crd)[:200] if crd else "None")
        logger.info("Sample call keys: %s", list(sample.keys()) if sample else "empty")
        sys.exit(1)

    # 모드별 분리 분석
    mode_labels = {
        "voice_to_voice": "V2V (Voice-to-Voice)",
        "text_to_voice": "T2V (Text-to-Voice)",
    }
    for mode_key, mode_label in mode_labels.items():
        mode_calls = [c for c in calls if c.get("communication_mode") == mode_key]
        mode_with_metrics = [c for c in mode_calls if (c.get("call_result_data") or {}).get("metrics")]
        if not mode_with_metrics:
            continue
        print(f"\n{'#' * 72}")
        print(f"  {mode_label}  ({len(mode_with_metrics)} calls with metrics)")
        print(f"{'#' * 72}")

        dist = analyze_distribution(mode_calls)
        breakdown = analyze_latency_breakdown(mode_calls, mode_filter=mode_key)
        correlation = analyze_length_correlation(mode_calls)
        outliers = analyze_outliers(mode_calls, dist)
        by_status = analyze_by_status(mode_calls)
        print_report(dist, breakdown, correlation, outliers, by_status)

    # 전체 통합 분석
    print(f"\n{'#' * 72}")
    print(f"  All Modes  ({len(calls_with_metrics)} calls with metrics)")
    print(f"{'#' * 72}")

    dist = analyze_distribution(calls)
    breakdown = analyze_latency_breakdown(calls)
    correlation = analyze_length_correlation(calls)
    outliers = analyze_outliers(calls, dist)
    by_status = analyze_by_status(calls)
    print_report(dist, breakdown, correlation, outliers, by_status)

    # JSON 저장
    if args.output:
        # raw 리스트와 histogram 튜플은 JSON 직렬화를 위해 변환
        save_data = {
            "session_b_e2e": {k: v for k, v in dist.get("session_b_e2e", {}).items()
                              if k not in ("histogram", "raw")},
            "session_b_stt": {k: v for k, v in dist.get("session_b_stt", {}).items()
                              if k not in ("histogram", "raw")},
            "breakdown": {k: v for k, v in breakdown.items() if k != "raw"},
            "correlation": {
                "n": correlation["n"],
                "r_char": correlation["correlation_char_len"],
                "r_word": correlation["correlation_word_len"],
            },
            "outliers": outliers,
            "by_status": by_status,
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        logger.info("Saved to %s", args.output)


if __name__ == "__main__":
    main()
