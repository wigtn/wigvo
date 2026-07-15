# WI-2 §8-#9 프로파일링 게이트 — 측정 결과

> **결론: VAD 오프로드 착수 정당 (GO).** VAD가 busy CPU의 **53.6%**로 단독 최대 항목이며,
> 그중 Silero ONNX 추론(51%)은 GIL을 해제하는 C 호출이라 스레드풀로 빼면 다른 코어에서
> 진짜 병렬 실행된다. 현재 10코어 중 이벤트루프 1코어만 사용 → 오프로드 이득이 크다.

- **일자:** 2026-07-16
- **관련:** PRD `PRD_poc-server-refactor.md` §8-#9(프로파일링 게이트), WI-2(VAD 성능)
- **선행:** WI-1 부하 하네스(`apps/relay-server/tests/load/`)

## 목적

PRD가 "측정 없이 VAD 오프로드 착수 금지" 게이트를 건 이유 = VAD가 포화 코어의 일부(예 40%)뿐이면
오프로드 이득이 인상보다 작기 때문. 실제로 VAD가 CPU를 지배하는지 실측해 오프로드 착수 여부를 결정.

## 방법

- 부하 하네스(`tests.load.harness`)로 **N=40 동시 통화**, `--speech-ratio 1.0`(매 프레임 Silero 추론 = 최악 CPU).
- 서버는 `LOAD_TEST_MODE=1`(OpenAI/Twilio 스텁 → 비용 0), `--workers 1`(프로덕션 동일 단일 이벤트루프).
- 프로파일러 = **pyinstrument**(in-process 통계 샘플러). py-spy는 이 환경(Apple Silicon/macOS)에서
  프로세스 attach 불가(os error 25/60)라 배제. pyinstrument는 cProfile과 달리 C 호출 시간을
  깎지 않아 ONNX 비중 측정에 정확.
- 측정 코드 = 베이스라인(`ulaw_rms` 벡터화 이전). 벡터화는 non-VAD 비중을 줄이므로 이 결과는
  VAD 비중에 **보수적**(실제로는 VAD가 더 지배적).

## 환경

- 머신: Apple Silicon(arm64), 10 logical core(성능 4). **주의:** 배포 VM은 x86(e2-standard-2).
- Wall 480s 중 idle(이벤트루프 대기) 436s 제외 → **busy CPU ≈ 28.4s** 기준 비율.

## 결과 (busy CPU self-time)

| 카테고리 | self(s) | busy% |
| --- | --- | --- |
| **VAD (Silero ONNX + local_vad)** | **15.18** | **53.6%** |
| 프레임워크/asyncio (websocket·protocol·framing·starlette) | 9.80 | 34.6% |
| ulaw_rms/audio_utils *(구버전, 벡터화 전)* | 2.81 | 9.9% |
| audio_router·json·base64·numpy·echo_gate | <0.5 | <2% |

- 핫패스: `AudioRouter.handle_twilio_audio`(22.4s) → `LocalVAD.process`(17.7s) → `SileroVAD.process`(13.7s, 순수 ONNX).
- 단일 최대 프레임 = `silero_vad.py` ONNX 추론 **14.5s (51.2%)**.

## 판정

**VAD 오프로드 착수 GO.**

1. VAD가 단독 최대(53.6%). 2위 프레임워크 오버헤드(34.6%)는 asyncio/websocket 기계장치라 오프로드 곤란.
2. Silero ONNX(51%)는 GIL 해제 C 추론 → 스레드풀 오프로드 시 다른 코어에서 병렬. 10코어 중 1코어만
   쓰는 현 구조의 "단일 이벤트루프 포화"(WI-1 병목)를 직접 해소.

## Caveat

- 53.6%는 40~55% 경계 구간이고 측정 머신은 ARM. x86에선 ONNX SIMD 특성 차로 정확한 %는 흔들릴 수 있음.
  단 "VAD가 오프로드 1순위"라는 질적 결론은 견고(2배 이상 차이 단독 1위 + GIL 해제). 절대 상한은 GCP(WI-1)에서.
- 부수 확인: `ulaw_rms`(FR-2.0) 벡터화가 busy의 9.9%(GIL 점유 파이썬 루프)를 제거 → 가치 입증.

## 재현

```bash
cd apps/relay-server
LOAD_TEST_MODE=1 MAX_CONCURRENT_CALLS=100 python -m uvicorn src.main:app --host 127.0.0.1 --port 8080
# 별도로:
python -m tests.load.harness --base-url http://127.0.0.1:8080 --sweep 40 --duration 60 --speech-ratio 1.0
# 프로파일: pyinstrument로 서버 래핑 후 SIGINT (py-spy attach는 macOS/ARM 불가)
```
