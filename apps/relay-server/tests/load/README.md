# 릴레이 동시 통화 부하테스트 (WI-1)

PRD `poc-refactor/PRD_poc-server-refactor.md`의 **WI-1**. "동시 10통"이 미검증 추측값(`config.py`
주석 명시)이므로, **실제로 몇 통까지 안정적으로 처리하는지**를 외부 비용 없이 실측한다.

## 무엇을 재는가

서버의 CPU 병목 후보는 **통화마다 도는 Local VAD(Silero) 추론 + 오디오 처리**다.
WI-2 이후 추론은 고정 thread pool로 분리됐지만 완료 callback과 오디오 파이프라인은 단일
asyncio 이벤트루프(`--workers 1`)로 돌아가므로, 동일 하니스로 offload 이후 상한을 재측정한다.
이 하니스는 N개의 동시 통화를 Twilio Media Stream 프로토콜로 재현하고, 발화 수준의 합성
µ-law 오디오를 20ms마다 주입해 **VAD·오디오 핫패스를 실제로 돌린다.**

핵심 지표 = **이벤트루프 lag**(`/loadtest/stats`). 통화가 늘어 단일 스레드가 밀리면 lag이
급증하고, 이 lag이 곧 통역 지연에 그대로 더해진다.

## 비용 0 원리 (load_test_mode)

`LOAD_TEST_MODE=1`이면 (`src/config.py`):
- OpenAI Realtime **실제 연결을 스텁**(가짜 세션) → 토큰 비용 0
- Twilio 아웃바운드 **발신/종료 REST 스킵** → 통화료 0

기본값은 **off** — 프로덕션 동작에 영향 없음(모든 변경이 `load_test_mode` 가드).
하네스는 Local VAD가 꺼졌거나 ONNX runtime/model 로드에 실패하면 즉시 중단한다.
Silero hot path 없이 나온 낮은 CPU/lag 수치를 WI-1 결과로 오인하지 않기 위해서다.

> **측정 범위:** 스텁 모드는 **수신자 오디오 → VAD → 전송**(inbound, 지배적 CPU 병목)을
> 충실히 재현한다. OpenAI 응답 오디오의 outbound 재생 경로는 재현하지 않는다(통역 지연
> 실측은 Tier B 소수 실통화로 별도 대조 — PRD WI-1 FR-1.1).

## 실행

**1) 서버를 부하모드로 기동** (가급적 실제 배포 VM에서 — 실 하드웨어 상한을 재려면 필수)

```bash
cd apps/relay-server
LOAD_TEST_MODE=1 MAX_CONCURRENT_CALLS=100 \
  uv run uvicorn src.main:app --host 0.0.0.0 --port 8080 --workers 1
```
- `MAX_CONCURRENT_CALLS`를 스윕 최대치 이상으로 올려야 상한 위까지 측정된다(안 올리면 503 거절).
- `--workers 1`은 프로덕션과 동일하게 유지(핵심 조건).

**2) 하니스 실행** (부하 클라이언트는 **서버와 다른 머신**에서 돌려야 클라 CPU가 측정을 왜곡하지 않음)

```bash
uv run python -m tests.load.harness \
  --base-url http://<서버IP>:8080 \
  --tenant-id <tenant-uuid> \
  --sweep 5,8,10,15,20,30 \
  --duration 20
```

`TENANT_AUTH_ENFORCE=true` 환경에서는 기관 키를 CLI에 남기지 말고
`RELAY_API_KEY` 환경변수로 전달한다. App WebSocket 부가 부하까지 인증하려면
`WIGVO_USER_JWT`도 설정한다. JWT가 없으면 Twilio/VAD 핵심 경로만 측정한다.

옵션:
- `--sweep` 측정할 동시성 레벨(쉼표구분)
- `--duration` 레벨당 유지 초(기본 20)
- `--speech-ratio` 발화 비중, 1.0=매 프레임 Silero 추론(최악 CPU). 기본 0.7
- `--tenant-id` 부하 통화 tenant UUID (`WIGVO_LOADTEST_TENANT_ID`로도 지정 가능)

## 결과 해석

```
목표  시작  거절  에러  lag_p50  lag_p95  lag_p99  lag_max   CPU%
  5     5     0     0      0.3      1.1      2.0      4.5     22.0
 10    10     0     0      0.8      3.2      8.1     18.0     48.0
 20    20     0     0      4.5     31.0     95.0    210.0     92.0   ← 저하 시작
 30    24     6     0     22.0    140.0    ...                       ← 거절(503)
```
- **안정 X** = lag_p95가 낮게 유지되는 최대 레벨
- **저하 Y** = lag_p95가 급증(예: 수십 ms↑)하기 시작하는 레벨 → 통역 지연 악화 지점
- **거절 Z** = `started < 목표`(503) 나오는 레벨 = 현재 하드캡 도달
- 이 X/Y/Z로 `max_concurrent_calls`를 **근거 있게** 재설정한다.

**CPU%는 서버에서 교차 확인:** `docker stats` 또는 `top`으로 relay 프로세스를 함께 관찰
(하니스가 계산하는 CPU%는 프로세스 CPU초 기반 근사).

## 외부 한도도 함께 확인 (PRD FR-1.3)

서버가 안 밀려도 **외부 한도가 먼저 걸릴 수 있다** — 스텁 모드는 이를 못 재므로 별도 확인:
- **OpenAI 동시 세션**: 통화 1건 = 세션 2개(`DualSessionManager`). 한도 100이면 서버 무관하게 동시 50통이 상한.
- **Twilio 동시 채널/CPS**: 계정 한도.
- **DB 커넥션 풀**: `db_pool_max_size=5`(`config.py`).

## VAD 오프로드(WI-2) 전/후 비교

WI-2 적용 후 동일 스윕을 재실행해 **안정 X 상승 + lag_p95 하락**을 증명한다(PRD 인수기준).
`--speech-ratio 1.0`로 돌리면 VAD 추론 부하가 최대라 오프로드 효과가 가장 뚜렷하다.
