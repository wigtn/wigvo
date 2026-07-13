#!/usr/bin/env python3
"""WIGVO 릴레이 동시 통화 부하 하니스 (WI-1).

서버를 load_test_mode(OpenAI/Twilio 스텁)로 띄운 뒤, N개의 동시 통화를 재현한다.
각 통화는 실제 Twilio Media Stream 프로토콜로 /twilio/media-stream WS에 붙어
합성 µ-law 오디오를 20ms 간격으로 주입한다 → 서버의 실제 VAD/오디오 핫패스가
단일 이벤트루프에서 돌며 포화 여부를 드러낸다.

지표는 서버의 /loadtest/stats(이벤트루프 lag p50/p95/p99/max + CPU%)로 수집한다.
--sweep로 여러 동시성 레벨을 순차 측정해 "안정 X / 저하 Y / 거절 Z" 곡선을 만든다.

사용:
  # 1) 서버를 부하모드로 (별도 터미널 또는 VM)
  RELAY_LOAD_TEST_MODE=1 MAX_CONCURRENT_CALLS=100 \
    uv run uvicorn src.main:app --host 0.0.0.0 --port 8080

  # 2) 하니스 실행
  uv run python -m tests.load.harness \
    --base-url http://localhost:8080 --sweep 5,8,10,15,20 --duration 20
"""

import argparse
import asyncio
import base64
import json
import time
import uuid

import httpx
import websockets

from tests.load.audio import make_frame_stream

STREAM_SID = "MZloadtest0000000000000000000000"


def _prebuild_media_msgs(duration_s: float, speech_ratio: float) -> list[str]:
    """20ms µ-law 프레임을 미리 media 이벤트 JSON 문자열로 직렬화(클라 CPU 최소화)."""
    frames = make_frame_stream(duration_s=duration_s, speech_ratio=speech_ratio)
    msgs = []
    for f in frames:
        payload = base64.b64encode(f).decode("ascii")
        msgs.append(json.dumps({"event": "media", "streamSid": STREAM_SID,
                                "media": {"payload": payload}}))
    return msgs


async def _start_call(client: httpx.AsyncClient, base_url: str, idx: int) -> str | None:
    """POST /relay/calls/start. 성공 시 call_id, 용량초과(503)면 None."""
    call_id = f"lt-{uuid.uuid4().hex[:12]}"
    body = {
        "call_id": call_id,
        "phone_number": "+821000000000",
        "source_language": "ko",
        "target_language": "en",
        "communication_mode": "voice_to_voice",
    }
    try:
        r = await client.post(f"{base_url}/relay/calls/start", json=body, timeout=30)
    except Exception:
        return None
    if r.status_code == 503:
        return "__REJECTED__"
    if r.status_code != 200:
        return None
    return call_id


async def _run_call(base_url: str, ws_url: str, media_msgs: list[str],
                    call_id: str, stop: asyncio.Event, result: dict) -> None:
    """한 통화: media WS + app WS 연결, 20ms 간격 오디오 주입, stop까지 유지."""
    twilio_uri = f"{ws_url}/twilio/media-stream/{call_id}"
    app_uri = f"{ws_url}/relay/calls/{call_id}/stream"
    app_ws = None
    try:
        # app(브라우저) WS는 부가 부하 — 실패해도 통화 자체는 진행
        try:
            app_ws = await websockets.connect(app_uri, open_timeout=10)
        except Exception:
            app_ws = None

        async with websockets.connect(twilio_uri, open_timeout=10) as tw:
            await tw.send(json.dumps({"event": "connected"}))
            await tw.send(json.dumps({"event": "start", "streamSid": STREAM_SID,
                                      "start": {"streamSid": STREAM_SID,
                                                "tracks": ["inbound"]}}))
            # app WS 수신 드레인(서버가 broadcast하는 자막/이벤트 카운트)
            async def _drain():
                if not app_ws:
                    return
                try:
                    async for _ in app_ws:
                        result["app_msgs"] += 1
                except Exception:
                    pass
            drain_task = asyncio.create_task(_drain())

            loop = asyncio.get_running_loop()
            next_t = loop.time()
            i = 0
            n = len(media_msgs)
            while not stop.is_set():
                await tw.send(media_msgs[i % n])
                i += 1
                next_t += 0.02
                dt = next_t - loop.time()
                if dt > 0:
                    await asyncio.sleep(dt)
            result["frames_sent"] += i
            drain_task.cancel()
    except Exception as e:
        result["errors"] += 1
        result.setdefault("error_samples", [])
        if len(result["error_samples"]) < 3:
            result["error_samples"].append(repr(e)[:120])
    finally:
        if app_ws:
            try:
                await app_ws.close()
            except Exception:
                pass


async def _get_stats(client: httpx.AsyncClient, base_url: str) -> dict:
    try:
        r = await client.get(f"{base_url}/loadtest/stats", timeout=10)
        return r.json()
    except Exception:
        return {}


async def _run_level(base_url: str, ws_url: str, media_msgs: list[str],
                     n: int, duration_s: float) -> dict:
    """동시성 레벨 n을 duration_s 동안 유지하며 서버 지표를 계측."""
    async with httpx.AsyncClient() as client:
        await client.post(f"{base_url}/loadtest/reset", timeout=10)
        stop = asyncio.Event()
        result = {"target": n, "started": 0, "rejected": 0, "errors": 0,
                  "frames_sent": 0, "app_msgs": 0}

        # 통화 시작(램프업)
        call_ids: list[str] = []
        for i in range(n):
            cid = await _start_call(client, base_url, i)
            if cid == "__REJECTED__":
                result["rejected"] += 1
            elif cid:
                call_ids.append(cid)
                result["started"] += 1
            await asyncio.sleep(0.05)  # 완만한 램프업

        # 오디오 주입 태스크 기동
        tasks = [asyncio.create_task(_run_call(base_url, ws_url, media_msgs, cid, stop, result))
                 for cid in call_ids]

        stats_before = await _get_stats(client, base_url)
        t0 = time.monotonic()
        await asyncio.sleep(duration_s)  # 부하 유지 구간
        stats_after = await _get_stats(client, base_url)
        t1 = time.monotonic()

        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for cid in call_ids:  # 정리
            try:
                await client.post(f"{base_url}/relay/calls/{cid}/end", timeout=10)
            except Exception:
                pass

        # CPU% = 구간 CPU초 / 구간 벽시계
        cpu_pct = None
        if stats_before.get("cpu_seconds_total") is not None and \
           stats_after.get("cpu_seconds_total") is not None:
            dc = stats_after["cpu_seconds_total"] - stats_before["cpu_seconds_total"]
            cpu_pct = round(dc / max(t1 - t0, 0.001) * 100, 1)

        result["loop_lag_ms"] = stats_after.get("loop_lag_ms", {})
        result["cpu_pct"] = cpu_pct
        result["active_at_end"] = stats_after.get("active_calls")
        return result


def _print_report(rows: list[dict]) -> None:
    print("\n=== WIGVO 부하테스트 결과 ===")
    hdr = f"{'목표':>4} {'시작':>4} {'거절':>4} {'에러':>4} " \
          f"{'lag_p50':>8} {'lag_p95':>8} {'lag_p99':>8} {'lag_max':>8} {'CPU%':>6}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        lag = r.get("loop_lag_ms", {}) or {}
        print(f"{r['target']:>4} {r['started']:>4} {r['rejected']:>4} {r['errors']:>4} "
              f"{lag.get('p50', 0):>8} {lag.get('p95', 0):>8} {lag.get('p99', 0):>8} "
              f"{lag.get('max', 0):>8} {str(r.get('cpu_pct')):>6}")
    print("\n해석: 이벤트루프 lag p95가 급증하기 시작하는 지점이 '저하 Y', "
          "거절(503)이 나오는 지점이 '거절 Z'. p95 lag이 통역 지연에 직접 더해진다.")
    for r in rows:
        if r.get("error_samples"):
            print(f"  [N={r['target']}] 에러 예시: {r['error_samples']}")


async def _main() -> None:
    ap = argparse.ArgumentParser(description="WIGVO 릴레이 동시 통화 부하 하니스")
    ap.add_argument("--base-url", default="http://localhost:8080")
    ap.add_argument("--ws-url", default=None, help="기본: base-url의 http→ws 치환")
    ap.add_argument("--sweep", default="5,8,10,15,20", help="쉼표구분 동시성 레벨")
    ap.add_argument("--duration", type=float, default=20.0, help="레벨당 유지 초")
    ap.add_argument("--speech-ratio", type=float, default=0.7, help="발화 비중(1.0=최악)")
    args = ap.parse_args()

    ws_url = args.ws_url or args.base_url.replace("http://", "ws://").replace("https://", "wss://")
    levels = [int(x) for x in args.sweep.split(",") if x.strip()]

    # 사전 점검: 서버가 load_test_mode인지 확인
    async with httpx.AsyncClient() as client:
        st = await _get_stats(client, args.base_url)
    if not st.get("load_test_mode"):
        print("⚠️  경고: 서버가 load_test_mode가 아닙니다. RELAY_LOAD_TEST_MODE=1로 재기동하세요.")
        print(f"    (/loadtest/stats 응답: {st})")
        return
    print(f"서버 확인 OK · max_concurrent_calls={st.get('max_concurrent_calls')} · "
          f"레벨={levels} · 레벨당 {args.duration}s")

    media_msgs = _prebuild_media_msgs(duration_s=max(args.duration, 5) + 2,
                                      speech_ratio=args.speech_ratio)

    rows = []
    for n in levels:
        print(f"\n▶ 레벨 N={n} 측정 중 ({args.duration}s)...")
        row = await _run_level(args.base_url, ws_url, media_msgs, n, args.duration)
        rows.append(row)
        lag = row.get("loop_lag_ms", {}) or {}
        print(f"  시작 {row['started']}/{n} · 거절 {row['rejected']} · 에러 {row['errors']} · "
              f"lag_p95={lag.get('p95')}ms · CPU%={row.get('cpu_pct')}")
        await asyncio.sleep(2)  # 레벨 간 안정화

    _print_report(rows)


if __name__ == "__main__":
    asyncio.run(_main())
