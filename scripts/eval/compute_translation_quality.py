#!/usr/bin/env python3
"""WIGVO 번역 품질 오프라인 평가 스크립트.

Supabase에서 transcript_bilingual (원문, 번역) 쌍을 가져와
GPT-4o 또는 Gemini reference 번역 대비 BLEU/chrF2++ 점수를 계산한다.

논문 활용: 실시간 번역 품질을 오프라인(제약 없는) 번역 대비로 측정하여
latency-quality tradeoff 입증 (Huber et al. 2023 방법론).

Usage:
    pip install -r requirements-eval.txt
    python compute_translation_quality.py --call-id <call_id>
    python compute_translation_quality.py --all --limit 10
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass, field

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class TranslationPair:
    """원문-번역 쌍."""
    role: str           # "user" | "recipient"
    original: str       # STT 원문 (source language)
    hypothesis: str     # 실시간 번역 출력
    reference: str = "" # GPT-4o 오프라인 번역 (나중에 채움)
    direction: str = "" # e.g. "en→ko" or "ko→en"


@dataclass
class SystemMetrics:
    """통화별 시스템 메트릭 집계."""
    num_calls: int = 0
    total_turns: int = 0
    # Latency (ms)
    session_a_latencies: list[float] = field(default_factory=list)
    session_b_e2e_latencies: list[float] = field(default_factory=list)
    first_message_latencies: list[float] = field(default_factory=list)
    # Echo & VAD
    echo_suppressions: int = 0
    echo_gate_breakthroughs: int = 0
    echo_loops_detected: int = 0
    hallucinations_blocked: int = 0
    vad_false_triggers: int = 0
    # Interrupt & Guardrail
    interrupt_count: int = 0
    guardrail_level2_count: int = 0
    guardrail_level3_count: int = 0


@dataclass
class EvalResult:
    """평가 결과."""
    direction: str
    num_segments: int = 0
    bleu: float = 0.0
    chrf: float = 0.0
    comet: float = 0.0
    comet_qe: float = 0.0
    pairs: list[TranslationPair] = field(default_factory=list)


def is_noise_text(text: str) -> bool:
    """텍스트가 노이즈(무의미한 입력)인지 판별한다.

    필터 기준:
    1. 자음만으로 이루어진 텍스트 (ㅇㄹㅇㄹ 등 keyboard spam)
    2. 짧은 패턴의 3회 이상 반복 (뾰로롱뾰로롱뾰로롱)
    3. 단일 문자 (뿅 등 단일 의성어)
    4. 배경 소음 전사 (TV/라디오 — 뉴스 앵커 멘트, 시청 감사 등)
    """
    stripped = text.strip()
    if not stripped:
        return True

    # 1. 자음만 (Korean Jamo consonants U+3131~U+314E)
    non_space = stripped.replace(" ", "")
    if non_space and all("\u3131" <= c <= "\u314E" for c in non_space):
        return True

    # 2. 짧은 패턴 반복 (패턴 길이 1~6자, 3회 이상)
    for plen in range(1, min(7, len(non_space) // 2 + 1)):
        pat = non_space[:plen]
        reps = len(non_space) // plen
        if reps >= 3 and pat * reps == non_space[:plen * reps]:
            return True

    # 3. 단일 문자
    if len(non_space) <= 1:
        return True

    # 4. 배경 소음 패턴 (TV/라디오 전사)
    noise_patterns = [
        r"MBC|KBS|SBS|JTBC|YTN",          # 방송사 멘트
        r"시청해\s*주셔서\s*감사",           # "시청해주셔서 감사합니다"
        r"뉴스.{0,5}입니다",                # "뉴스 OOO입니다"
    ]
    for pat in noise_patterns:
        if re.search(pat, stripped):
            return True

    return False


def filter_noise_pairs(pairs: list[TranslationPair]) -> list[TranslationPair]:
    """노이즈 쌍을 제거하고 유효한 쌍만 반환한다."""
    clean = []
    removed = 0
    for p in pairs:
        if is_noise_text(p.original):
            logger.info("  [NOISE] removed: %s", p.original[:60])
            removed += 1
        else:
            clean.append(p)
    if removed:
        logger.info("Noise filter: removed %d/%d pairs", removed, len(pairs))
    return clean


def fetch_transcripts(
    call_id: str | None = None,
    limit: int = 10,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Supabase에서 transcript_bilingual을 조회한다.

    Args:
        since: ISO date string (inclusive), e.g. "2026-02-23"
        until: ISO date string (inclusive, end of day), e.g. "2026-02-27"
    """
    try:
        from supabase import create_client
    except ImportError:
        logger.error("supabase 패키지가 필요합니다: pip install supabase")
        sys.exit(1)

    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY 환경변수가 필요합니다")
        sys.exit(1)

    client = create_client(url, key)

    query = client.table("calls").select(
        "id, transcript_bilingual, source_language, target_language, call_result_data"
    )
    if call_id:
        query = query.eq("id", call_id)
    else:
        query = query.not_.is_("transcript_bilingual", "null")
        if since:
            query = query.gte("created_at", f"{since}T00:00:00+00:00")
        if until:
            query = query.lte("created_at", f"{until}T23:59:59+00:00")
        query = query.order("created_at", desc=True).limit(limit)

    result = query.execute()
    return result.data


def extract_pairs(calls: list[dict]) -> tuple[list[TranslationPair], list[TranslationPair]]:
    """통화 데이터에서 (원문 != 번역)인 쌍만 추출한다.

    Returns:
        (user_pairs, recipient_pairs) — 방향별 분리
    """
    user_pairs: list[TranslationPair] = []
    recipient_pairs: list[TranslationPair] = []

    for call in calls:
        src_lang = call.get("source_language", "en")
        tgt_lang = call.get("target_language", "ko")

        transcripts = call.get("transcript_bilingual", [])
        if not transcripts:
            continue

        for entry in transcripts:
            original = entry.get("original_text", "")
            translated = entry.get("translated_text", "")
            role = entry.get("role", "")

            # 원문과 번역이 동일하면 스킵 (기존 버그 데이터)
            if not original or not translated or original == translated:
                continue

            if role == "user":
                user_pairs.append(TranslationPair(
                    role=role,
                    original=original,
                    hypothesis=translated,
                    direction=f"{src_lang}→{tgt_lang}",
                ))
            elif role == "recipient":
                recipient_pairs.append(TranslationPair(
                    role=role,
                    original=original,
                    hypothesis=translated,
                    direction=f"{tgt_lang}→{src_lang}",
                ))

    return user_pairs, recipient_pairs


def extract_system_metrics(calls: list[dict]) -> SystemMetrics:
    """call_result_data.metrics에서 시스템 메트릭을 집계한다."""
    sm = SystemMetrics()
    for call in calls:
        result_data = call.get("call_result_data") or {}
        metrics = result_data.get("metrics")
        if not metrics:
            continue
        sm.num_calls += 1
        sm.total_turns += metrics.get("turn_count", 0)
        sm.session_a_latencies.extend(metrics.get("session_a_latencies_ms", []))
        sm.session_b_e2e_latencies.extend(metrics.get("session_b_e2e_latencies_ms", []))
        fm = metrics.get("first_message_latency_ms", 0)
        if fm > 0:
            sm.first_message_latencies.append(fm)
        sm.echo_suppressions += metrics.get("echo_suppressions", 0)
        sm.echo_gate_breakthroughs += metrics.get("echo_gate_breakthroughs", 0)
        sm.echo_loops_detected += metrics.get("echo_loops_detected", 0)
        sm.hallucinations_blocked += metrics.get("hallucinations_blocked", 0)
        sm.vad_false_triggers += metrics.get("vad_false_triggers", 0)
        sm.interrupt_count += metrics.get("interrupt_count", 0)
        sm.guardrail_level2_count += metrics.get("guardrail_level2_count", 0)
        sm.guardrail_level3_count += metrics.get("guardrail_level3_count", 0)
    return sm


def _percentile(values: list[float], p: float) -> float:
    """리스트에서 p-th 백분위수를 계산한다."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * (p / 100)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_v[int(k)]
    return sorted_v[f] * (c - k) + sorted_v[c] * (k - f)


def generate_references_gemini(pairs: list[TranslationPair], src_lang: str, tgt_lang: str) -> None:
    """Gemini 2.5 Flash로 reference 번역을 생성한다."""
    try:
        from google import genai
    except ImportError:
        logger.error("google-genai 패키지가 필요합니다: pip install google-genai")
        sys.exit(1)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY 환경변수가 필요합니다")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    for i, pair in enumerate(pairs):
        logger.info("Generating reference [%d/%d]: %s", i + 1, len(pairs), pair.original[:50])
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=pair.original,
            config=genai.types.GenerateContentConfig(
                system_instruction=(
                    f"You are a professional translator. Translate the following text from "
                    f"{src_lang} to {tgt_lang}. Output ONLY the translated text, nothing else."
                ),
                temperature=0.0,
                max_output_tokens=1024,
            ),
        )
        text = response.text if response.text else ""
        pair.reference = text.strip() if text else pair.original


def generate_references_gpt4o(pairs: list[TranslationPair], src_lang: str, tgt_lang: str) -> None:
    """GPT-4o text API로 reference 번역을 생성한다."""
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai 패키지가 필요합니다: pip install openai")
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY 환경변수가 필요합니다")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    for i, pair in enumerate(pairs):
        logger.info("Generating reference [%d/%d]: %s", i + 1, len(pairs), pair.original[:50])
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a professional translator. Translate the following text from "
                        f"{src_lang} to {tgt_lang}. Output ONLY the translated text, nothing else."
                    ),
                },
                {"role": "user", "content": pair.original},
            ],
            temperature=0.0,
            max_tokens=1024,
        )
        pair.reference = response.choices[0].message.content.strip()


def compute_scores(pairs: list[TranslationPair]) -> EvalResult:
    """SacreBLEU로 BLEU + chrF2++ 점수를 계산한다."""
    try:
        import sacrebleu
    except ImportError:
        logger.error("sacrebleu 패키지가 필요합니다: pip install sacrebleu")
        sys.exit(1)

    if not pairs:
        return EvalResult(direction="N/A", num_segments=0)

    hypotheses = [p.hypothesis for p in pairs]
    references = [[p.reference for p in pairs]]

    bleu = sacrebleu.corpus_bleu(hypotheses, references)
    chrf = sacrebleu.corpus_chrf(hypotheses, references, word_order=2)  # chrF2++

    return EvalResult(
        direction=pairs[0].direction,
        num_segments=len(pairs),
        bleu=bleu.score,
        chrf=chrf.score,
        pairs=pairs,
    )


def compute_comet_scores(results: list[EvalResult], skip_qe: bool = False) -> None:
    """COMET (ref-based) + COMET-QE (ref-free) 점수를 계산한다."""
    try:
        from comet import download_model, load_from_checkpoint
    except ImportError:
        logger.warning("unbabel-comet 패키지가 없습니다. COMET 점수를 건너뜁니다.")
        logger.warning("설치: pip install unbabel-comet")
        return

    active_results = [r for r in results if r.num_segments > 0]
    if not active_results:
        return

    # COMET (reference-based)
    try:
        logger.info("Loading COMET model (wmt22-comet-da)...")
        model_path = download_model("Unbabel/wmt22-comet-da")
        model = load_from_checkpoint(model_path)

        for r in active_results:
            data = [
                {"src": p.original, "mt": p.hypothesis, "ref": p.reference}
                for p in r.pairs
            ]
            output = model.predict(data, batch_size=8, gpus=0, num_workers=1)
            r.comet = output.system_score

        del model
    except Exception as e:
        logger.warning("COMET (ref-based) 실패: %s", e)

    # COMET-QE (reference-free)
    if skip_qe:
        logger.info("COMET-QE 스킵 (--skip-comet-qe)")
        return

    try:
        logger.info("Loading COMET-QE model (wmt21-comet-qe-da)...")
        qe_model_path = download_model("wmt21-comet-qe-da")
        qe_model = load_from_checkpoint(qe_model_path)

        for r in active_results:
            data = [
                {"src": p.original, "mt": p.hypothesis}
                for p in r.pairs
            ]
            output = qe_model.predict(data, batch_size=8, gpus=0, num_workers=1)
            r.comet_qe = output.system_score

        del qe_model
    except Exception as e:
        logger.warning("COMET-QE 실패 (모델 다운로드 필요할 수 있음): %s", e)


def print_results(results: list[EvalResult], sys_metrics: SystemMetrics | None = None) -> None:
    """결과를 테이블 형식으로 출력한다."""
    print("\n" + "=" * 70)
    print("WIGVO Evaluation Results")
    print("=" * 70)

    # --- System Metrics ---
    if sys_metrics and sys_metrics.num_calls > 0:
        sm = sys_metrics
        print(f"\n[System Metrics] {sm.num_calls} calls, {sm.total_turns} turns")

        if sm.session_a_latencies:
            print(f"  Session A Latency (User→Recipient):")
            print(f"    P50={_percentile(sm.session_a_latencies, 50):.0f}ms  "
                  f"P95={_percentile(sm.session_a_latencies, 95):.0f}ms  "
                  f"Mean={sum(sm.session_a_latencies)/len(sm.session_a_latencies):.0f}ms  "
                  f"N={len(sm.session_a_latencies)}")
        if sm.session_b_e2e_latencies:
            print(f"  Session B Latency (Recipient→User):")
            print(f"    P50={_percentile(sm.session_b_e2e_latencies, 50):.0f}ms  "
                  f"P95={_percentile(sm.session_b_e2e_latencies, 95):.0f}ms  "
                  f"Mean={sum(sm.session_b_e2e_latencies)/len(sm.session_b_e2e_latencies):.0f}ms  "
                  f"N={len(sm.session_b_e2e_latencies)}")
        if sm.first_message_latencies:
            print(f"  First Message Latency:")
            print(f"    P50={_percentile(sm.first_message_latencies, 50):.0f}ms  "
                  f"P95={_percentile(sm.first_message_latencies, 95):.0f}ms  "
                  f"N={len(sm.first_message_latencies)}")

        print(f"  Echo: suppressions={sm.echo_suppressions}  breakthroughs={sm.echo_gate_breakthroughs}  "
              f"loops={sm.echo_loops_detected}")
        print(f"  VAD: false_triggers={sm.vad_false_triggers}  hallucinations_blocked={sm.hallucinations_blocked}")
        print(f"  Interrupt: {sm.interrupt_count}  Guardrail: L2={sm.guardrail_level2_count} L3={sm.guardrail_level3_count}")

    # --- Translation Quality ---
    for r in results:
        if r.num_segments == 0:
            continue
        print(f"\n[Translation Quality] {r.direction} ({r.num_segments} segments)")
        print(f"  BLEU:     {r.bleu:.1f}")
        print(f"  chrF2++:  {r.chrf:.1f}")
        if r.comet > 0:
            print(f"  COMET:    {r.comet:.4f}")
        if r.comet_qe > 0:
            print(f"  COMET-QE: {r.comet_qe:.4f}")

    # --- LaTeX: System Metrics ---
    if sys_metrics and sys_metrics.num_calls > 0:
        sm = sys_metrics
        print("\n--- LaTeX: System Performance (Table) ---")
        print(r"\begin{tabular}{lrrrr}")
        print(r"\hline")
        print(r"Metric & P50 & P95 & Mean & N \\")
        print(r"\hline")
        if sm.session_a_latencies:
            n = len(sm.session_a_latencies)
            print(f"Session A Latency (ms) & {_percentile(sm.session_a_latencies, 50):.0f} & "
                  f"{_percentile(sm.session_a_latencies, 95):.0f} & "
                  f"{sum(sm.session_a_latencies)/n:.0f} & {n} \\\\")
        if sm.session_b_e2e_latencies:
            n = len(sm.session_b_e2e_latencies)
            print(f"Session B Latency (ms) & {_percentile(sm.session_b_e2e_latencies, 50):.0f} & "
                  f"{_percentile(sm.session_b_e2e_latencies, 95):.0f} & "
                  f"{sum(sm.session_b_e2e_latencies)/n:.0f} & {n} \\\\")
        print(r"\hline")
        print(r"\end{tabular}")

    # --- LaTeX: Translation Quality ---
    has_comet = any(r.comet > 0 for r in results)
    print("\n--- LaTeX: Translation Quality (Table) ---")
    if has_comet:
        print(r"\begin{tabular}{lrrrrr}")
        print(r"\hline")
        print(r"Direction & N & BLEU & chrF2++ & COMET & COMET-QE \\")
        print(r"\hline")
        for r in results:
            if r.num_segments == 0:
                continue
            print(f"{r.direction} & {r.num_segments} & {r.bleu:.1f} & {r.chrf:.1f} & "
                  f"{r.comet:.4f} & {r.comet_qe:.4f} \\\\")
    else:
        print(r"\begin{tabular}{lrrr}")
        print(r"\hline")
        print(r"Direction & N & BLEU & chrF2++ \\")
        print(r"\hline")
        for r in results:
            if r.num_segments == 0:
                continue
            print(f"{r.direction} & {r.num_segments} & {r.bleu:.1f} & {r.chrf:.1f} \\\\")
    print(r"\hline")
    print(r"\end{tabular}")

    # 샘플 출력
    for r in results:
        if r.num_segments == 0:
            continue
        print(f"\n--- Sample pairs ({r.direction}) ---")
        for p in r.pairs[:3]:
            print(f"  Original:   {p.original[:80]}")
            print(f"  Hypothesis: {p.hypothesis[:80]}")
            print(f"  Reference:  {p.reference[:80]}")
            print()


def save_results(
    results: list[EvalResult],
    output_path: str,
    sys_metrics: SystemMetrics | None = None,
    ref_model: str = "gpt-4o",
) -> None:
    """결과를 JSON으로 저장한다."""
    output: dict = {"meta": {"ref_model": ref_model}}

    # System metrics
    if sys_metrics and sys_metrics.num_calls > 0:
        sm = sys_metrics
        output["system_metrics"] = {
            "num_calls": sm.num_calls,
            "total_turns": sm.total_turns,
            "session_a_latency_ms": {
                "p50": round(_percentile(sm.session_a_latencies, 50)),
                "p95": round(_percentile(sm.session_a_latencies, 95)),
                "mean": round(sum(sm.session_a_latencies) / len(sm.session_a_latencies)) if sm.session_a_latencies else 0,
                "n": len(sm.session_a_latencies),
            },
            "session_b_latency_ms": {
                "p50": round(_percentile(sm.session_b_e2e_latencies, 50)),
                "p95": round(_percentile(sm.session_b_e2e_latencies, 95)),
                "mean": round(sum(sm.session_b_e2e_latencies) / len(sm.session_b_e2e_latencies)) if sm.session_b_e2e_latencies else 0,
                "n": len(sm.session_b_e2e_latencies),
            },
            "echo_suppressions": sm.echo_suppressions,
            "echo_gate_breakthroughs": sm.echo_gate_breakthroughs,
            "interrupt_count": sm.interrupt_count,
            "guardrail_level2": sm.guardrail_level2_count,
            "guardrail_level3": sm.guardrail_level3_count,
        }

    # Translation quality
    output["translation_quality"] = []
    for r in results:
        entry: dict = {
            "direction": r.direction,
            "num_segments": r.num_segments,
            "bleu": round(r.bleu, 2),
            "chrf": round(r.chrf, 2),
        }
        if r.comet > 0:
            entry["comet"] = round(r.comet, 4)
        if r.comet_qe > 0:
            entry["comet_qe"] = round(r.comet_qe, 4)
        entry["pairs"] = [
                {
                    "original": p.original,
                    "hypothesis": p.hypothesis,
                    "reference": p.reference,
                }
                for p in r.pairs
            ]
        output["translation_quality"].append(entry)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info("Results saved to %s", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="WIGVO 번역 품질 평가")
    parser.add_argument("--call-id", help="특정 통화 ID로 평가 (Supabase calls.id)")
    parser.add_argument("--all", action="store_true", help="최근 N개 통화 전체 평가")
    parser.add_argument("--limit", type=int, default=10, help="--all 시 최대 통화 수")
    parser.add_argument("--output", default="eval_results.json", help="결과 저장 경로")
    parser.add_argument("--ref-model", default="gemini",
                        choices=["gemini", "gpt4o"],
                        help="Reference 번역 생성 모델 (기본: gemini)")
    parser.add_argument("--skip-reference", action="store_true",
                        help="Reference 생성 스킵 (기존 reference가 있는 경우)")
    parser.add_argument("--skip-comet", action="store_true",
                        help="COMET/COMET-QE 점수 계산 전체 스킵")
    parser.add_argument("--skip-comet-qe", action="store_true",
                        help="COMET-QE만 스킵 (QE 모델 1.72GB 다운로드 필요)")
    parser.add_argument("--filter-noise", action="store_true",
                        help="노이즈 데이터 필터링 (자음 스팸, 반복 패턴, 배경 소음 등)")
    parser.add_argument("--since", help="시작 날짜 (inclusive, e.g. 2026-02-23)")
    parser.add_argument("--until", help="종료 날짜 (inclusive, e.g. 2026-02-27)")
    args = parser.parse_args()

    if not args.call_id and not args.all:
        parser.print_help()
        print("\n--call-id 또는 --all 중 하나를 지정하세요.")
        sys.exit(1)

    # ref_model 선택
    if args.ref_model == "gemini":
        generate_fn = generate_references_gemini
    else:
        generate_fn = generate_references_gpt4o
    ref_model_name = "gemini-2.5-flash" if args.ref_model == "gemini" else "gpt-4o"

    # 1. Supabase에서 transcript 조회
    logger.info("Fetching transcripts from Supabase...")
    calls = fetch_transcripts(
        call_id=args.call_id, limit=args.limit,
        since=args.since, until=args.until,
    )
    if not calls:
        logger.error("No calls found with transcript_bilingual data")
        sys.exit(1)
    logger.info("Found %d calls with transcript data", len(calls))

    # 2. 시스템 메트릭 추출 (call_result_data.metrics)
    sys_metrics = extract_system_metrics(calls)
    logger.info("System metrics: %d calls, %d turns", sys_metrics.num_calls, sys_metrics.total_turns)

    # 3. (원문 != 번역)인 쌍 추출
    user_pairs, recipient_pairs = extract_pairs(calls)
    logger.info("Extracted %d user pairs, %d recipient pairs", len(user_pairs), len(recipient_pairs))

    # 3.5 노이즈 필터링
    if args.filter_noise:
        logger.info("Applying noise filter...")
        user_pairs = filter_noise_pairs(user_pairs)
        recipient_pairs = filter_noise_pairs(recipient_pairs)
        logger.info("After filter: %d user pairs, %d recipient pairs", len(user_pairs), len(recipient_pairs))

    if not user_pairs and not recipient_pairs:
        if sys_metrics.num_calls > 0:
            # 시스템 메트릭만이라도 출력
            logger.warning("No valid translation pairs (original_text == translated_text?)")
            logger.warning("Showing system metrics only. Run new calls to accumulate translation data.")
            print_results([], sys_metrics)
            save_results([], args.output, sys_metrics, ref_model=ref_model_name)
            return
        logger.error("No valid translation pairs found (original_text == translated_text?)")
        logger.info("Ensure transcript_bilingual has separate original/translated values.")
        sys.exit(1)

    # 4. Reference 번역 생성
    if not args.skip_reference:
        logger.info("Using %s for reference generation", ref_model_name)
        if user_pairs:
            src = calls[0].get("source_language", "en")
            tgt = calls[0].get("target_language", "ko")
            logger.info("Generating references for user pairs (%s→%s)...", src, tgt)
            generate_fn(user_pairs, src, tgt)

        if recipient_pairs:
            src = calls[0].get("source_language", "en")
            tgt = calls[0].get("target_language", "ko")
            logger.info("Generating references for recipient pairs (%s→%s)...", tgt, src)
            generate_fn(recipient_pairs, tgt, src)

    # 5. BLEU/chrF2++ 계산
    results = []
    if user_pairs:
        results.append(compute_scores(user_pairs))
    if recipient_pairs:
        results.append(compute_scores(recipient_pairs))

    # 5.5 COMET/COMET-QE 계산
    if not args.skip_comet:
        compute_comet_scores(results, skip_qe=args.skip_comet_qe)

    # 6. 결과 출력 + 저장
    print_results(results, sys_metrics)
    save_results(results, args.output, sys_metrics, ref_model=ref_model_name)


if __name__ == "__main__":
    main()
