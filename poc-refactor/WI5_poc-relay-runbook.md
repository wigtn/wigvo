# PoC Relay 운영 런북

## 책임과 알림 채널

- 1차 대응 담당: WIGVO PoC on-call (`contact@wigtn.com`).
- 단일 알림 채널: Google Cloud Logging의 `wigvo.operations.alert` ERROR 로그를
  Cloud Monitoring 로그 기반 알림 정책으로 연결한 on-call 이메일.
- 알림 필터: `severity=ERROR AND jsonPayload.logger="wigvo.operations.alert" AND jsonPayload.alert_type:*`.
- 알림 종류: `high_cpu`, `openai_errors`, `capacity_reached`, `manual_test`.

배포마다 아래 명령을 relay 컨테이너와 같은 환경에서 한 번 실행하고, 5분 안에
on-call 이메일이 도착하는지 확인한다. 이 확인 전에는 운영 준비 완료로 표시하지 않는다.

```bash
cd apps/relay-server
uv run python -c 'from src.observability.operations import operations; operations.emit_test_alert()'
```

## 정상 기준

`GET /health`에서 다음을 확인한다.

- `status=ok`
- `active_sessions == active_call_count`
- `capacity.occupied <= capacity.maximum`
- `reserved_call_count=0` (새 통화 시작 중이 아닐 때)
- `operations.process_cpu_percent < CPU_ALERT_THRESHOLD_PERCENT`
- `operations.openai_errors_window < OPENAI_ERROR_ALERT_THRESHOLD`

WI-1 실측 기준 2 vCPU VM의 안전 상한은 20통이며, 지연 민감 운영값은 18통이다.
`MAX_CONCURRENT_CALLS`를 20보다 높일 때는 같은 VM에서 부하 sweep을 다시 통과해야 한다.

## 경보별 대응

### capacity_reached

1. `/health`의 active/reserved/max를 기록한다.
2. 새 요청은 503과 `active/max` 안내로 거절되며 기존 통화는 종료하지 않는다.
3. 사용자는 즉시 재시도하도록 안내한다. 반복되면 cap을 임의 상향하지 말고 통화
   도착 패턴과 WI-1 부하 결과를 확인한다.
4. 종료된 통화가 남아 있거나 `reserved`가 계속 0이 아니면 프로세스를 재시작하기 전
   해당 call_id의 cleanup 로그를 확인한다.

### high_cpu

1. `/health`와 최근 10분의 active/reserved, OpenAI 오류, event-loop lag를 기록한다.
2. 신규 통화 유입을 줄이고 진행 통화는 유지한다.
3. active가 0이 된 뒤에만 relay를 재시작한다. 단일 stateful relay 재시작은 진행 통화를 끊는다.
4. 같은 동시성에서 재발하면 `MAX_CONCURRENT_CALLS`를 18 이하로 낮추고 WI-1 sweep을 재실행한다.

### openai_errors

1. OpenAI 상태와 relay의 `realtime_connect`, `realtime_event`, Chat/Whisper 오류 로그를 확인한다.
2. 진행 통화의 recovery/degraded-mode 진입 여부를 확인한다.
3. 장애가 지속되면 사용자에게 즉시 재시도를 안내하고 신규 통화를 제한한다.
4. 키/모델/env 변경은 `active_sessions=0`에서만 재배포한다.

## 배포·마이그레이션 게이트

상태를 보유한 단일 relay이므로 재시작, 인증 강제 전환, DB 마이그레이션은 반드시
`active_sessions=0`, `reserved_call_count=0`, 인바운드 대기열 0에서 수행한다.
변경 후 `/health`를 재확인하고 `manual_test` 알림을 실발화한다.

## 과부하 회귀 시험

```bash
cd apps/relay-server
LOAD_TEST_MODE=1 MAX_CONCURRENT_CALLS=20 uv run uvicorn src.main:app --port 8080
uv run python -m tests.load.harness --base-url http://localhost:8080 --sweep 18,20,21 --duration 20
```

기대 결과는 20개까지 시작, 21번째 503, 진행 중 20개 무중단, 테스트 종료 후
`active_call_count=0`과 `reserved_call_count=0`이다. `LOAD_TEST_MODE`는 운영에서 항상 false다.
