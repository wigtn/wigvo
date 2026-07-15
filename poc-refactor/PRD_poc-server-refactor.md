# WIGVO PoC 단계 서버 리팩토링 PRD

> **Version**: 1.17
> **Created**: 2026-07-13
> **Status**: Draft — 하이브리드 · 인바운드 보강 · **WI-2 전제 검증(#1)·개인정보 문구(#2)·스레드모델(#6) 반영** · **WI-6 웹 부스 응대 진입면 프론트 예외 반영(Non-Goal 부분 해제)** · **VAD v6 업그레이드 팩트체크 반영(§8-#10)** · **코덱스 리뷰 1~4차 반영: FR-3.6 번호 풀 후속 PRD 이월 · WI-6 pickup 콜디스패치 재스코프(디스패치 DB 스키마+종료상태·DualSession 지연 생성·대기 미디어 handoff(FR-6.1a)·용량 예약(SESSION_STARTING)·SESSION_STARTING TTL/통합 cleanup) · 공용 CapacityManager(FR-5.5, 인·아웃바운드 cap 원자화) · 브라우저 인증 재설계(§8-#1) · 개인정보 릴리스 게이트(§7)** · **WI-6 PoC 최소 구현선 ✅ 확정(§8-#11, 공수 4~7일→3~5일)** · **prd-reviewer 반영: 일정 병렬 전제(F-1)·PendingMediaHandler 명세(F-2)·재시작 CONNECTED 복구(CP-1)·pickup 토큰 폐기(S-1)·인바운드 레이트(S-2)·status-callback fail-safe(CP-3) 등 13건** / WI-2 착수 대기 · **VAD 프로파일링 미실시 → 게이트 유지(§8-#9)**
> **Type**: Backend refactor — 플랫폼 안정화 (통역 파이프라인 로직 변경 없음) · **예외: WI-6 인바운드 웹 부스 "응대 진입면" 최소 프론트 포함**(§1.3·FR-6.3a)
> **Owner**: contact@wigtn.com
> **Scope 경계**: 이 PRD는 **현행 단일 GCP VM 위에서** 가능한 PoC 안정화 작업만 다룬다. 수평확장(상태 외부화·서버 복수화)·AWS 마이그레이션·Twilio 회선/번호 결정·자체 SIP는 **별도 PRD로 이월**한다.

> ### ✅ WI-1 부하테스트 실측 완료 (2026-07-13 · 프로덕션 VM e2-standard-2, 2 vCPU)
>
> - **안정 ≈20통**(CPU 68%) / **저하 ≈25통**(79%) / **N=30 포화**(91%) / 거절(503) 미발생 — 앱이 cap 전까진 조용히 지연만 악화.
> - **p99 꼬리는 ~N=10부터** 상승(N=8 p99=3ms → N=10 p99=112ms) → 지연 민감 시 cap **18** 권장(처리량 기준 20).
> - **🔴 프로덕션 조치 필요:** VM `deploy/.env`의 `MAX_CONCURRENT_CALLS`가 `50`(안전선의 2.5배)으로 설정돼 있었다 — _출처: WI-1 측정 세션 리포트. 실제 `.env`는 repo 미포함이라 코드로 재확인 불가이고 `.env.example`엔 이 키가 없다(→ 추가 필요)._ → **`.env` 값만 18~20으로 낮추고 relay 재시작**(env override라 재빌드 불필요).
> - **WI-2 근거 실증:** VM은 N=30에서 **CPU ~1코어 포화, 2번째 코어 유휴** → 단일 이벤트루프가 벽. **GIL 해제 검증됨**(마이크로벤치: 2코어 VAD 병렬 **~1.8x**, 4코어 3.0x → onnxruntime이 GIL을 놓음). 오프로드 전제 성립. 실제 동시 통화 상한 증가폭은 WI-2 후 동일 스윕 재측정. 이 데이터가 before 베이스라인.
> - 하니스 seam 버그 2건 수정(`bf78da2`, `test/relay-load-harness`). env 변수명은 `LOAD_TEST_MODE`(핸드오프의 `RELAY_` 접두사는 오기).

---

## 1. Overview

### 1.1 Problem Statement

WIGVO 릴레이 서버는 통역 품질과 무관하게 **"데모 1대 구조"**에 최적화돼 있어, 기관 PoC 배포 전에 세 가지 결함을 해소해야 한다. 모두 코드로 확인된 사실이다.

- **수용량이 미검증이었다 → WI-1로 실측 완료.** 코드 기본값은 `max_concurrent_calls = 10`(주석에 _"부하테스트로 확정"_ 명시)이나, **프로덕션 VM엔 `.env`로 `50`이 박혀 있었다(위험)**. WI-1 실측 결과 2 vCPU 안전선은 **≈20통**(저하 25·포화 30) → cap을 18~20으로 조정해야 한다(상단 블록).
- **CPU 병목이 단일 이벤트루프에 묶여 있다 (실측 확증).** 통화마다 생성되는 음성감지(Silero VAD) 추론이 `to_thread` 없이 **asyncio 이벤트루프에서 동기 실행**된다(`local_vad.py:189` `self._model.process(...)`, 호출부 `voice_to_voice.py:392`·`text_to_voice.py:409`). 통화 1건은 WebSocket 4개(브라우저·Twilio·OpenAI×2) + VAD 모델 1개를 점유하고, 이 CPU 작업이 통화 수에 비례해 한 스레드(GIL)에 쌓인다. **부하테스트 실증:** VM은 N=30에서 CPU가 ~1코어에 포화하고 2번째 코어는 유휴 → 단일 스레드가 벽. 논문 실패 3건(webhook timeout·WebSocket 조기종료)과 정합.
- **멀티테넌트·인증이 없다.** 코드에 `tenant_id`가 전무해 기관별 데이터/설정/번호 격리가 불가능하다. 발신 엔드포인트 `/calls/start`는 인증이 없어(`calls.py:38`) 서버 주소만 알면 누구나 통화를 걸 수 있고, 발신번호는 `outbound.py`의 `from_=settings.twilio_phone_number`(env 단일값)로 다뤄진다. 모니터/조회 경로도 무방비다.
- **인바운드 통화 경로가 없다 (신규 요구).** 사용자 오디오 소스가 브라우저(app WS 라우트 `stream.py:21`)에 하드 바인딩돼, 사용자가 **전화로 걸어 들어오는(inbound)** 진입점이 없다. PoC는 웹 발신뿐 아니라 **전화 착신**도 지원해야 한다 → WI-6.

> **비즈니스 문서(「기관 서비스 전환 계획」)와 정합:** 서버 과제를 "① 기관별 칸막이+인증(멀티테넌트) · ② 수용량 실측 · ③ 배정 개편+복수화"로 규정하고, ①②를 *"번호·클라우드 결정과 무관하게 지금 가능 · PoC에 직접 기여"*로, ③(복수화)은 다기관 전환 전까지 이월로 둔다. 이 PRD는 ①②(+VAD 개선·운영 안전장치)를 담고 ③을 제외한다.

### 1.1-b 통화 방향 모델 — 하이브리드 (인바운드 우선 + 아웃바운드 레이어)

PoC는 **두 방향**을 지원한다. 토폴로지·미디어 파이프라인은 사실상 동일하고 **세션 생성 시점과 레그 역할만 다르다** — 코드 확인: DualSession 생성은 `calls.py:107`(오직 `/calls/start`)뿐이고, 미디어 경로(Media Streams → AudioRouter → 듀얼세션 → VAD/echo)는 방향 불문 재사용된다.

|                    | **아웃바운드 (현행)**                       | **인바운드 (신규 · PoC 우선)**                         |
| ------------------ | ------------------------------------------- | ------------------------------------------------------ |
| 흐름               | 웹앱 → WIGVO 발신 → 상대(PSTN)              | 외국인(PSTN) → WIGVO → 기관(웹 부스)                   |
| PSTN 레그          | 1                                           | **1**(기관 웹 부스) / 2(기관 데스크 전화 브리지, 옵션) |
| 발신번호(CLI) 문제 | **있음** — 번호 풀·BYOC·피어링(어려운 트랙) | **없음** — 외국인이 거는 쪽이라 우리 번호 표시 무관    |
| 필요 번호          | 발신용 국내번호(취득 어려움)                | **인바운드 DID(취득 최易)**                            |
| 신규 코드          | (현행)                                      | **세션 생성 진입점 하나**(생성 시점 역전)              |

- **인바운드 = PoC 신뢰 경로.** 번호/BYOC 불확실성을 **우회**하므로 통역 품질·지연·과업완수를 지금 측정 가능. 기관이 **웹 부스(양방향 오디오 경로 `/stream` 재사용)로 받으면 PSTN 1레그 = 현행과 동일 부하**(VAD 2배 아님). 데스크 전화 브리지(2레그)는 옵션.
- **아웃바운드 = 선택 레이어.** WIGVO가 먼저 걸어야 할 때(기관→외국인 능동 발신 등)만. 이때만 번호 풀·BYOC(FR-3.6·Track A)가 걸린다. 발신번호 **변작(스푸핑)은 불법이라 스코프 아웃**, 외국인 개인 테넌트도 불필요(외국인 = 익명 인바운드 발신자, 테넌트 = 기관).

### 1.2 Goals

- **G1. 진짜 수용량을 실측으로 확정한다. ✅ 완료(WI-1).** 안정≈20/저하25/포화30 확정. 외부 한도(OpenAI 세션·Twilio 채널)는 스텁 모드라 미측정 → 별도 확인 항목.
- **G2. 동시 통화 상한을 올린다.** VAD 추론을 이벤트루프에서 분리(thread offload)해 멀티코어를 실제로 사용. **전제 검증 완료:** GIL 해제 실측(2코어 VAD 병렬 ~1.8x). 실제 상한 증가폭은 WI-2 후 재측정. **(코덱스 F-3) 조건부 성공 기준:** WI-2 프로파일링에서 **VAD 비지배로 판명되면**(축소 분기), G2의 성공 기준은 "상한 상승"이 아니라 **"이벤트루프 GIL 점유 감소(`ulaw_rms` 벡터화로 파이썬 루프 제거) + 지연 p95 유지"**로 조정한다(상한 상승은 근거 없이 목표화 금지).
- **G3. 기관별 칸막이를 만든다.** `tenant_id`를 요청→통화→DB→로그까지 관통시키고, 기관별 설정 테이블로 발신번호·프롬프트·이력·권한을 격리한다. 회선을 나중에 **값만 바꿔 꽂는 그릇**을 완성한다.
- **G4. 모든 접근 경로를 잠근다.** 발신 API뿐 아니라 통화 조회·모니터 경로까지 인증을 건다 (일부만 잠그면 잠그지 않은 것과 같다).
- **G5. 단일서버 PoC 운영 안전장치를 세운다.** 장애·과부하를 감지·알림하고, 용량 초과 시 우아하게 거절하며(기존 503 UX 검증), 장애 시 대응 절차를 문서화한다.
- **G6. 통역 파이프라인·기능·통화 흐름은 무손상으로 유지한다.** 기존 테스트 전부 통과 — **게이트 숫자는 `pytest --co -q` 실측 기준**(#3: README '184'는 stale, `def test_` 실측 ~469).

### 1.3 Non-Goals (Out of Scope) — 다음 PRD로 이월

- **상태 외부화 & 세션 어피니티 & 서버 복수화 (B④):** call_id→task 라우팅, Redis, graceful drain, 무중단 배포. → _「PRD: 수평확장 & AWS 마이그레이션」_
- **AWS(ECS Fargate/ALB/RDS) 이전** 일체.
- **회선(egress) 결정 — Twilio BYOC 피어링·국내 SIP 트렁크·ClawOps·자체 SIP·발신번호 표시 테스트** = 통신 트랙(Track A, 별도). _단 팀원 분석(`poc-number-architecture.md`)에 따라 이 결정은 "확산"이 아니라 **PoC 크리티컬 패스**로 승격 — Track A에서 지금 병행 착수(RFP 질의). BYOC는 Media Streams(AI 파이프라인)를 안 건드리므로 **서버 오디오 작업(WI-1/2/4/5)은 무영향**이고, **번호 풀 할당(리싱형 할당기)은 이 PRD에서 제외 → 후속 「아웃바운드 번호 풀」 PRD로 이월**(코덱스 리뷰). WI-3엔 `resolve_outbound_number` seam만 남긴다(FR-3.6)._
- **Twilio 하위계정(subaccount) 기반 기관 분리** — 번호 확정 후 얹는 선택 레이어. 이 PRD는 DB 레벨 테넌트 격리까지만.
- **프론트엔드/웹앱 변경** — 원칙적으로 제외(503 대기 UX의 클라이언트 처리는 협의 항목으로만). **⚠ 단 예외(부분 해제): WI-6 인바운드 "웹 부스 응대(pickup) 진입면"은 이 PRD 스코프에 포함**한다. 근거 = 웹 부스로 **받으려면** 최소 프론트가 불가피(monitor는 읽기전용, 양방향 클라이언트는 아웃바운드 발신 진입에만 묶여 인바운드 통화로 직원을 데려다줄 진입면이 없음 — 코드 확인). **재사용: 오디오 엔진(`RealtimeCallView`+마이크/재생) 그대로.** **신규 한정: ① 인바운드 call_id 수신·응대 진입 화면 · ② 사용자 JWT 로그인 + 서버 발급 단기 pickup 토큰(기관 API Key 부착 아님 · §8-#1) · ③ 응대측 역할·언어방향 설정**(FR-6.3a). 그 밖의 웹앱 개편(디자인·기존 화면 리팩터)은 계속 제외.
- 통역 품질·프롬프트·VAD 튜닝 파라미터 값 변경 (VAD는 **실행 위치만** 옮기고 로직·임계값 불변).

### 1.4 Scope

| 포함                                                                                                                                                                      | 제외                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| 부하테스트 하니스 (동시 N통) · 수용량 실측 리포트                                                                                                                         | 상태 외부화·call_id 라우팅·Redis                                       |
| `local_vad.py` 추론의 스레드 오프로드                                                                                                                                     | AWS 리소스·ECS/ALB/RDS                                                 |
| `tenant_id` 관통 (요청→`ActiveCall`→DB→로그)                                                                                                                              | Twilio 회선/번호/표시 테스트                                           |
| `tenants`·`tenant_call_config` 테이블 + 발신번호 resolve                                                                                                                  | Twilio 하위계정 분리                                                   |
| `/calls/start` + 조회/모니터 경로 인증                                                                                                                                    | 서버 복수화·무중단 배포                                                |
| 단일서버 모니터링·알림·503 검증                                                                                                                                           | 프론트엔드·웹앱 (**단 WI-6 인바운드 응대 진입면은 예외로 포함**)       |
| **하이브리드: 인바운드 착신(웹 부스, 1레그) + 아웃바운드(웹 발신) 공존 (WI-6)** · **웹 부스 응대 진입면(최소 프론트: 수신·응대 진입 + JWT·단기 pickup 토큰 + 역할/언어)** | 발신번호 변작(스푸핑)·외국인 개인 테넌트·IVR 고도화·**웹앱 전면 개편** |

---

## 2. User Stories

### 2.1 Primary Users

- **운영자(WIGTN 내부):** PoC를 돌리며 동시 통화·장애를 감시하고, 기관을 온보딩한다. 수용량 수치·알림·인증의 직접 수혜자.
- **기관 담당자(테넌트):** 자기 기관 발신번호·프롬프트로 통화가 걸리고, 자기 기관 통화 기록만 조회한다.
- **개발자(내부):** 이후 회선을 코드 수정 없이 교체하고, 검증된 수용량 위에서 확장을 설계한다.

> As a **운영자**, I want 서버가 실제로 몇 통까지 버티는지 수치로 알기를 원한다, so that "10통"을 추측이 아니라 근거로 말하고 온보딩 속도를 정할 수 있다.
> As a **기관 담당자**, I want 우리 기관 발신번호·데이터가 다른 기관과 섞이지 않기를 원한다, so that 규정과 신뢰를 지킨 채 서비스를 승인할 수 있다.
> As a **개발자**, I want 회선/번호가 코드가 아니라 설정으로 다뤄지기를 원한다, so that 회선 결정을 기다리지 않고 서버를 완성할 수 있다.

### 2.2 Acceptance Criteria (Gherkin)

```gherkin
Scenario: 수용량 실측 확정
  Given 부하테스트 하니스로 동시 5/8/10/15/20통을 시뮬레이션하면
  When 통역 지연(p50/p95)·CPU%·에러율·외부 한도 도달을 계측하면
  Then "안정 X통 / 저하 시작 Y통 / 거절 시작 Z통"이 리포트로 확정되고
  And max_concurrent_calls가 추측값(10)이 아니라 그 수치로 갱신된다

Scenario: VAD 오프로드로 상한 상승 · 지연 유지
  Given VAD 추론을 이벤트루프 밖 워커로 옮긴 빌드에서
  When 동일 하드웨어로 부하테스트를 재실행하면
  Then 안정 동시 통화 수가 베이스라인 대비 증가하고
  And 통역 지연 p95가 베이스라인 이하이며
  And 기존 VAD 정확도 테스트(test_local_vad.py 등)가 전부 통과한다

Scenario: 기관별 격리
  Given 두 기관(A, B)을 서로 다른 발신번호·프롬프트로 등록하고
  When A의 인증키로 통화를 시작하고 기록을 조회하면
  Then A의 발신번호·프롬프트로 발신되고, A는 A의 통화만 조회하며
  And B의 데이터는 어떤 경로로도 A에게 노출되지 않는다

Scenario: 교차 테넌트 접근 차단 (음성 경로 · C-1/M-6)
  Given A가 인증된 상태에서 B 소유의 call_id를 알고 있을 때
  When A의 키로 그 call_id의 통화 조회·모니터에 접근하면
  Then tenant 불일치로 명시적 거부(403)되고 (빈 결과가 아니라 거부)
  And tenant_id가 해석되지 않는 어떤 요청도 fail-closed로 거부된다

Scenario: 접근 경로 잠금
  Given 인증키 없이 발신·조회·모니터 엔드포인트를 호출하면
  When 각 요청을 보내면
  Then 모두 401/403으로 거부되고, 인증 없이 접근 가능한 테넌트 데이터 경로가 0건이다

Scenario: 파이프라인 무손상
  Given 리팩토링 브랜치에서
  When 통화 시작→릴레이→통역→종료 플로우와 전체 테스트 스위트를 실행하면
  Then 통역 동작이 동일하고 기존 테스트가 전부 통과한다

Scenario: 과부하 우아한 거절
  Given 활성 통화가 상한에 도달한 상태에서
  When 새 통화 start 요청이 오면
  Then 503과 active/max 정보로 거절되고 진행 중 통화는 영향받지 않는다
```

---

## 3. Functional Requirements

작업 항목(WI)별 상세. 각 항목은 독립 배포 가능하며, 권장 순서는 §5.

### WI-1. 부하테스트 & 수용량 실측 (G1) — ✅ 완료 (2026-07-13)

> **결과:** 안정 ≈20통 / 저하 25 / 포화 30 (프로덕션 VM e2-standard-2, 2 vCPU). VM cap **50→18~20 조정 필요**. 하니스 = `test/relay-load-harness`(seam 버그 2건 수정 `bf78da2`). env 플래그 = `LOAD_TEST_MODE`. 이 데이터가 WI-2 before 베이스라인.

- **FR-1.1** 기존 `tests/e2e/call_client.py`·`tests/component/test_loopback_call.py`를 확장해 **동시 N통을 생성하는 하니스**를 만든다. 실제 OpenAI/Twilio 비용을 피하기 위해 가능한 한 loopback/mock 경로를 우선 사용하고, 실측 검증용 소수 실통화는 별도로 분리한다.
- **FR-1.2** 계측 지표: 통역 지연 `session_a`/`session_b`(p50/p95, 기존 `call_metrics` 재사용), 이벤트루프 지연, CPU%·메모리, 에러율, `active_call_count`.
- **FR-1.3** 외부·내부 한도 도달 여부를 함께 계측: **OpenAI 동시 세션**(통화당 2개 = `DualSessionManager`), **Twilio 한도 — 두 축을 구분**(코덱스 팩트체크): ① **동시 채널 수**(동시 활성 통화 상한) ② **CPS = 통화 생성 속도**(기본 1, Console에서 상향 · "동시 통화 수"가 아님), **DB 커넥션 풀**(`db_pool_max_size=5`, `config.py:33`).
- **FR-1.4** 산출물 = "안정 X / 저하 Y / 거절 Z" 리포트. 이 수치로 `max_concurrent_calls`를 갱신한다.
- **완료기준:** §2.2 "수용량 실측 확정".

### WI-2. VAD 스레드 오프로드 (G2) — _2~3일_

> **⚠️ 착수 전 프로파일링 게이트 (#1):** **(용어 정리 — 내부 모순 아님):** §1.1의 "실측 확증"은 **"단일 이벤트루프 스레드가 벽"**이라는 뜻(확정)이고, 아래 "미증명"은 **"그 포화 스레드에서 VAD의 비중이 지배적인가"**라는 별개의 더 세밀한 질문이다. WI-2의 대전제는 후자 — "VAD 추론이 포화 코어의 주 소비자"인데, **부하테스트(코어 포화)·GIL 벤치(1.8x 병렬)만으로는 이건 미증명**이다. 이벤트루프엔 VAD 외에도 `ulaw_rms`(파이썬 루프)·base64 인/디코딩·OpenAI 이벤트 JSON 파싱·echo_gate가 남는다. VAD가 포화 코어의 일부(예: 40%)뿐이면 오프로드 이득이 인상보다 훨씬 작다. → **N=20~30 부하 하 py-spy/cProfile 플레임그래프로 VAD가 실제 상위 항목인지 확인**한 뒤 착수(수 시간·저비용). **분기: VAD가 비지배로 나오면 Silero 고정풀 오프로드는 축소하고 `ulaw_rms` 벡터화(FR-2.0)만 반영**(공수 절감 — 2~3일 예산 방어).

- **FR-2.0 (#1) `ulaw_rms` 벡터화 선행:** `src/realtime/audio_utils.py`의 `ulaw_rms`(def `:38`, 순수 루프 `:46`)는 **순수 파이썬 루프**(`sum(...for b in audio)`)로, 프레임당 여러 곳(`local_vad.py:132`·`voice_to_voice.py:381·420`·`text_to_voice.py`·echo_gate 등 4모듈 6호출)에서 GIL 점유 실행된다(대조: `ulaw_to_float32`는 이미 numpy 벡터화). `_ULAW_TO_LINEAR`를 numpy 배열로 만들어 인덱싱+제곱합 벡터화 → 이벤트루프 파이썬 루프 제거. **WI-2보다 싸고 프로파일 결과와 무관하게 이득.**
- **FR-2.1 (#6) Silero 오프로드 = 고정 스레드 풀:** Silero 추론(`local_vad.py:189`)을 이벤트루프 밖으로. **권장: vCPU 배수 고정 스레드 풀 + 통화별 큐**(통화당 전용 스레드는 cap 20이면 20스레드/2코어 경합 → 1.8x 잠식 우려). VAD 결과(speech start/end)만 콜백으로 이벤트루프 반환. Silero 모델은 워커 측 고정.
- **FR-2.2 (#6) 순서 보장:** 통화별 **큐 키**로 프레임 순차성 확보(고정 풀에서도). 통화당-스레드 vs 고정-풀 트레이드오프는 §8 결정 + WI-2 재측정 시 두 방식 비교.
- **FR-2.3** VAD 로직·임계값·정확도는 **불변** — 실행 위치만 변경. 호출부(`voice_to_voice.py:392`·`text_to_voice.py:409`)의 인터페이스 유지. _(모델 자체 교체 = **VAD v6 업그레이드는 별건** — WI-2와 분리, §8-#10.)_
- **FR-2.4** 폴백: 워커 초기화 실패 시 기존 동기 경로로 안전하게 폴백(모델 로드 실패 처리와 동일 패턴).
- **FR-2.5 (M-2·#6) 워커/큐 정리:** 통화 종료 시 해당 통화의 **큐·워커 상태를 확정 정리** → **누수 0**. 고정 풀 모델에선 스레드가 풀에 상주(vCPU 배수 상한, 통화 수에 비례 안 함)하므로 검증 대상은 "스레드 수 원복"이 아니라 **통화별 큐/키의 완전 해제**. (통화당-스레드 방식 채택 시엔 스레드 join·수 원복 확인.)
- **FR-2.6 (M-2) 백프레셔:** 워커 큐 최대 길이와 초과 시 정책(오래된 프레임 드롭 or 경고)을 정의 — 큐 적체로 VAD 판정이 무한 지연되지 않게.
- **완료기준:** **(#1) 프로파일링으로 VAD 지배성 확인** + `ulaw_rms` 벡터화 반영 + §2.2 "VAD 오프로드로 상한 상승 · 지연 유지" + **누수 0**(고정 풀: 통화별 큐/키 완전 해제 확인 · 통화당-스레드 채택 시: 100회 반복 후 스레드 수 원복) + 재측정 시 **VAD 콜백 지연 p95** 별도 계측 + **고정 풀 vs 통화당 스레드 실측 비교**.

### WI-3. 멀티테넌트 기반 (G3) — _2~3일_

- **FR-3.1** `tenant_id` 관통: `CallStartRequest`→`ActiveCall`(`types.py`)→calls 영속화(`db/pg_client.py`의 `persist_call`/`update_call`)→로깅 컨텍스트(`logging_config.py`에 `call_id_var` 옆 `tenant_id_var` 추가).
- **FR-3.2** 신규 테이블(마이그레이션 `migrations/003_*.sql`):
  ```sql
  tenants(id, name, created_at)
  tenant_call_config(tenant_id, outbound_number, provider, prompt_overrides, languages)
  ```
  초기값: 전부 `provider='twilio'`, `outbound_number`=현행 단일 번호.
  **(M-3) 마이그레이션 안전:** up/down 스크립트 쌍 필수. 기존 통화 테이블의 `tenant_id`는 **nullable 추가 → 기본 테넌트 backfill → NOT NULL 승격** 3단계(대형 테이블 락 회피). 스키마 변경은 **활성 통화 0 상태**에서만 실행(WI-5 런북 연동). `provider` 컬럼은 "그릇"일 뿐 분기 로직은 회선 이월분(스코프 외) — 초기엔 항상 `'twilio'` 경로.
- **FR-3.3** 발신번호 resolve: `outbound.py`의 `from_=settings.twilio_phone_number` 하드코딩 → `resolve_outbound_number(tenant_id)`. **회선 결정이 나면 이 테이블 값만 교체 → 코드 무변경 전환.**
- **FR-3.4 (C-1) 격리 강제·fail-closed:** "애플리케이션 스코핑"만으론 쿼리 1건 누락 시 교차 유출 → **구조적 강제** 필수. 택1(§8 확정): (a) Postgres **RLS 정책**(`tenant_id` 기준), 또는 (b) **모든 통화 데이터 접근을 단일 tenant-scoped repository/DAO로 강제**(원시 쿼리 금지). `tenant_id`가 해석되지 않는 요청은 **빈 결과가 아니라 명시적 거부(fail-closed)**.
- **FR-3.5 (M-7·#2) 음성 녹음 미저장 (사실 확인용 — 프라이버시 '완료' 아님):** 오디오 프레임이 DB/로그/디스크/임시파일에 영속화되지 않음을 검사로 확인. **단 이건 원래부터 그랬고(ring buffer 인메모리·Twilio 녹음 미사용) 새 이득이 아니다.** 실제 PII 노출 — 양방향 전사(`pg_client.py:46` `transcript_bilingual` jsonb, DB 무기한)·전화번호·이름, 서버 로그 발화 평문(`guardrail/checker.py`·`recovery.py`), Langfuse(3자 SaaS) — 의 **보존기간·파기·로그 마스킹·Langfuse 차단은 이 PRD 스코프 밖 별도 트랙**(`docs/pilot-readiness.md` §2.3). 이 FR을 '프라이버시 완료'로 읽지 말 것.
- **FR-3.6 (번호 풀 · 이 PRD에서 제외 → 후속 이월) — 코덱스 리뷰 반영:** 발신번호 풀(통화별 고유 국내번호 배정 + 콜백 hold/release)은 **멀티테넌트 기반과 별개의 상태형 리싱(leasing) 서브시스템**이다 — 최소 필요분: `pool` 테이블 · 상태(`available/held/in_use/quarantined`) · **원자적 claim** · **lease expiry** · **통화 시작 실패 시 release** · 콜백 유지기간 · **동일 번호 동시할당 방지** · **공급사 provisioning reconciliation**. PoC가 **인바운드 우선**이라 번호 풀은 크리티컬 패스가 아니므로, WI-3에서 **분리해 후속 「아웃바운드 번호 풀」 PRD(통신/회선 트랙)로 이월**한다. **WI-3에 남기는 것 = seam뿐:** `resolve_outbound_number(tenant_id)`가 `tenant_call_config.outbound_number`의 **현행 단일번호를 반환**(그릇). 회선/번호 풀 결정이 나면 이 resolve 지점 뒤에 할당기를 꽂는다.
- **완료기준:** §2.2 "기관별 격리" + "교차 테넌트 접근 차단(음성 경로)" + 오디오 미저장 검사 통과 + `resolve_outbound_number`가 tenant별 단일번호 반환(번호 풀 할당/회수는 후속 PRD).

### WI-4a. 기관 API 인증 (G4) — _1~2일_ · ⚠ 착수 전 §8-#1(인증 방식) 확정 필수 · WI-3 선행

- **FR-4a.1** `/calls/start`(`calls.py:38`) + 통화 조회 + **모니터 WebSocket**(`stream.py:105`) + **웹부스 응대 WS `/calls/{call_id}/stream`(`stream.py:21` — WI-6 기관 응대 경로, 현재 무방비, W-5)**에 인증(방식은 §8-#1). 실패 시 401/403. **인증 축 구분(코덱스):** 서버 간·온보딩 = **기관 API Key**, 직원 웹 부스 = **사용자 로그인 JWT + 단기 pickup 토큰**(브라우저에 기관 API Key 부착 금지 — §8-#1·FR-6.3b).
- **FR-4a.2 (C-2) 2단계 롤아웃:** 무인증 클라이언트(웹·모니터)가 살아 있어 한 번에 잠그면 전 통화 중단. → `enforce=false`(검증하되 통과+로그) → **웹/모니터에 키 반영 확인** → `enforce=true`. 완료기준: **롤아웃 중 진행 통화 0 드롭**.
  - **운영 게이트:** 1차 배포 후 `/health`의 `tenant_auth_enforced=false`·`tenant_api_key_tenants>0` 확인 → 웹 발신/stream/monitor JWT 연결 확인 → **`active_sessions=0`일 때만** `TENANT_AUTH_ENFORCE=true`로 재배포 → `/health`에서 true 재확인. stateful 단일 relay 재시작은 진행 통화를 끊으므로 이 zero-active 게이트를 생략하지 않는다.
- **FR-4a.3 (C-2) 경로 화이트리스트:** "기관 인증 대상 경로"와 "인증 제외(=Twilio 서명검증 경유, WI-4b) 경로"를 **명시적 목록**으로 분리 — 회선 경로(webhook/media-stream)가 실수로 기관 인증 게이트에 걸려 통화가 끊기지 않게.
- **FR-4a.4 (M-6) IDOR 차단:** 조회·모니터는 인증 + `tenant_id` 스코핑(WI-3 의존). 인증된 A가 B의 call_id를 직접 지정해도 거부(§2.2 음성 경로).
- **FR-4a.5** `max_concurrent_calls`를 tenant별 상한으로 확장 검토(전역 상한과 병행). **검토 결과:** WI-4a에서는 전역 `CapacityManager` 하드캡을 유지하고, tenant별 상한은 WI-5의 공용 예약 계약을 훼손하지 않는 후속 운영 정책으로 둔다.
- **완료기준:** §2.2 "접근 경로 잠금" + "교차 테넌트 접근 차단".

### WI-4b. Twilio 콜백 서명검증 (G4) — _1~2일_ · **owner A(텔레포니)** · ⚠ 착수 전 §8 callback URL 확정

- **FR-4b.1** `webhook`·`status-callback`·**신규 `/twilio/incoming`(WI-6 인바운드 진입점, C-3 확장)**(HTTP): Twilio 공식 `RequestValidator`로 `X-Twilio-Signature` 검증(현재 서명검증 코드 전무 — 인바운드 진입점이 무방비면 아무나 가짜 착신으로 세션·비용 소진 가능). **서명 계산에 쓰는 public callback URL을 env로 고정**(프록시/HTTPS 뒤 URL 불일치 시 정상 요청이 전부 403 — 흔한 함정).
- **FR-4b.2 (C-3) media-stream WebSocket:** 표준 HTTP 서명검증과 **동작이 다름**(핸드셰이크 시점·URL 구성). **(코덱스) `X-Twilio-Signature` 핸드셰이크 검증을 필수**로 두고(Twilio Media Stream 공식 검증 대상), **서버 발급 signed stream token(call_id 바인딩)은 추가 방어층으로 선택 적용** — "둘 중 하나"가 아니라 "서명검증 필수 + 토큰 옵션".
- **FR-4b.3** 검증 실패 = **403 + 통화 미생성/정리**(상태 갱신 누락 방지) + 로깅.
- **FR-4b.4 (코덱스 CP-3) 종료 감지 fail-safe:** status-callback 서명검증 실패(특히 callback URL 오설정, FR-4b.1의 그 함정)로 정당한 `completed` 콜백이 403 처리되면, dispatch row가 `CONNECTED`에 영구히 걸려 **CapacityManager 예약이 반환 안 될** 위험. → **통화 종료 감지는 status-callback 단일 트리거에 의존 금지** — WS/Stream 끊김·claim TTL·세션 종료 등 **다중 트리거로 FR-6.3b 단일 cleanup 경로에 수렴**(어느 하나 실패해도 다른 경로가 종료·예약 반환 보장).
- **완료기준:** 무효 서명 요청 거부 + 정상 Twilio 콜백·미디어스트림 통과 e2e + **status-callback 누락/403 시에도 통화 종료·예약 반환이 다른 트리거로 성립**.

### WI-5. 단일서버 PoC 운영 안전장치 (G5) — _1~2일 (+CapacityManager 0.5~1일)_

- **FR-5.1** 관측: `active_call_count`·CPU·OpenAI 에러·용량 도달을 로그/헬스(`/health`, `health.py`)로 노출하고, 임계 초과 시 **알림**(채널 1개, 담당 지정).
- **FR-5.2** 과부하 거절 UX 검증: 상한 초과 시 503 + `active/max`(`calls.py:49`)가 진행 통화에 무영향임을 부하테스트로 확인. (클라이언트 대기 UX는 웹 협의 항목으로만 표기.)
- **FR-5.3** 장애 대응 절차 문서화: PoC 기간 단일서버 리스크는 "동시 통화를 소수로 유지 + 장애 시 즉시 재시도"로 감수하되, 감지·대응 담당과 절차를 런북으로 남긴다.
- **FR-5.4 (재발 방지 · 코덱스 CP-2로 일반화)** `.env.example`에 **이 PRD가 도입하는 모든 운영 임계값 env 키**를 명시 — `MAX_CONCURRENT_CALLS`(cap 단일 소스, 기본 18~20) · **public callback base URL(§8-#3)** · **pickup 토큰 TTL(§8-#1)** · **`max_waiting_calls`·claim TTL·SESSION_STARTING 초기화 timeout(FR-6.3b)**. cap=50 오설정과 동일한 "env 누락 → prod 위험" 클래스를 신규 상수 전체로 차단.
- **FR-5.5 (신규 · 공용 CapacityManager — 코덱스 4차, High · FR-6.3b가 소비):** 전역 cap 불변식 `active + reserved ≤ max_concurrent_calls`의 **단일 소유자**를 둔다. **문제:** 지금 cap 상태가 흩어져 있다 — `active_call_count`(call_manager 인메모리) · dispatch 상태(Postgres) · 아웃바운드 `/calls/start`는 **비원자적 soft cap**(`calls.py:47-49` 주석이 스스로 "동시 start 겹치면 1~2개 초과 가능" 인정, 검사~`register_call` 사이 경쟁). **인바운드 예약만 잠가도 아웃바운드와 동시 시작 시 cap 초과.** → **인바운드·아웃바운드가 같은 `CapacityManager`를 쓴다.** 단일 프로세스 PoC에선 `asyncio.Lock`/bounded semaphore 아래에서 **`active + reserved` 확인과 reservation 생성을 원자적으로**. **OpenAI 세션 생성 전에 reserve → 성공 시 reserved→active 전환 → 모든 실패·취소 경로에서 release.** **기존 `/calls/start`의 soft-cap 검사도 이 경로로 교체**(기존 아웃바운드 경쟁도 함께 해소). **테스트:** 인바운드 claim N 동시 · 아웃바운드 start N 동시 · 혼합 · 세션 생성 실패/취소 → **모든 경우 `active + reserved ≤ cap`** · 테스트 종료 후 **`reserved == 0`**.
- **완료기준:** 알림 1건 이상 실발화 테스트 + 런북 존재 + `.env.example`에 cap 키 존재 + **CapacityManager 동시성 테스트 통과(위 5케이스, 초과 0 · 종료 후 reserved 0)**.

### WI-6. 인바운드 통화 (외국인 착신) + 콜 디스패치 — _3~5일 (PoC 최소 확정 · §8-#11)_ · **선행: WI-3(tenant·DID 매핑) + WI-4a(직원 JWT·pickup 토큰) + WI-4b(`/twilio/incoming`·미디어스트림 서명검증) + WI-5(FR-5.5 CapacityManager)**

> **WI-6 내부 분할(확정):** WI-6은 선행작업 전체가 끝난 뒤 한 사람이 통짜로 시작하지 않는다. **A(미디어·용량·텔레포니)**는 FR-6.1/6.1a와 세션 부트스트랩을, **B(테넌트·인증·디스패치)**는 FR-6.2/6.3/6.3a/6.3b를 각자 선행작업 위에서 병렬 구현한다. 전체 e2e 완료만 두 트랙의 합류를 요구한다.
>
> - **A 소유:** `/twilio/incoming`, WI-4b 서명검증, `PendingMediaHandler`, CapacityManager 연동, DualSession 지연 생성, 동일 Stream handoff, 미디어 자원 cleanup.
> - **B 소유:** DID→tenant 라우팅, `inbound_call_dispatch`, 상태머신·원자적 claim·TTL, pickup 토큰 발급/재검증, tenant FIFO, 응대 진입 UI, dispatch 상태 cleanup.
>   - 응대 진입 UI는 B 기능 범위지만 현재 Frontend 소유자의 UI 작업과 겹치므로 `apps/web/**` 변경 전 조율하고 기존 오디오 엔진은 재사용만 한다.
> - **교차 seam:** B가 tenant·권한·claim 검증 후 `SESSION_STARTING`으로 전이하고 `await bootstrap_inbound_session(call_id: str, tenant_id: str) -> BootstrapResult`를 호출한다. A는 용량 예약→세션 생성→handoff만 수행하고 tenant/claim DB를 직접 갱신하지 않는다. B는 A의 미디어 내부 객체를 직접 조작하지 않는다.
> - **실패 소유권:** 부트스트랩 실패·취소·timeout은 단일 cleanup 진입점으로 수렴한다. B는 최종 dispatch 상태/`end_reason`, A는 세션·CapacityManager 예약·PendingMediaHandler/Stream 자원 정리를 책임진다.
> - **인증 경계:** pickup 토큰 최소 클레임은 `call_id + tenant_id + user_id + role + exp`; A 진입점은 B 인증 계층이 검증한 컨텍스트만 소비한다.

> **재추정 이유:** 코드 확인 결과 WI-6은 "UI만 추가"가 아니라 **작은 call-dispatch 기능**이다. ① `register_app_ws`는 `self._app_ws[call_id] = ws`로 **통화당 단일 WS를 말없이 덮어씀**(`call_manager.py:55` — 원자적 선점·중복부착 방지 없음 → 두 직원이 같은 통화를 누르면 뒤가 앞을 밀어냄). ② App WS 끊김이 `cleanup_call(reason="app_disconnected")`로 **통화 전체를 종료**(`stream.py:102` — 새로고침·네트워크 블립에 통화 소실). 인바운드는 "누가 받을지"를 서버가 중재해야 하므로 **API·상태·원자적 claim**이 필요. 프론트는 여전히 얇지만 백엔드가 커진다.

> **배경:** 미디어 파이프라인은 방향 불문 재사용 가능하나, **DualSession은 `/calls/start`가 먼저 만들어놓는다고 가정**한다(`twilio_webhook.py:87·100` — 없으면 즉시 close). 인바운드는 아무도 `/calls/start`를 안 부르므로 지금 구조에선 실패한다. 필요한 건 **재작성이 아니라 "생성 시점의 역전"** — Twilio 착신 webhook에서 call_id·DualSession을 즉석 생성하는 진입점.
>
> **토폴로지 = 현행과 동일(레그 역할만 스왑).** 외국인(PSTN 1레그) ↔ Twilio ↔ WIGVO ↔ 기관(웹 부스). 지금 "웹(사용자) ↔ Twilio(기관)"에서 웹/전화 자리만 바뀐다 — AudioRouter의 Session A(웹쪽)/Session B(Twilio쪽) 역할이 스왑되고 배관은 그대로. **PSTN 레그 1개 = 현행과 동일 부하(VAD 2배 아님).**

- **FR-6.1 인바운드 진입점 + DualSession 지연 생성 (코덱스 리뷰):** Twilio 착신 DID의 Voice URL → 신규 `POST /twilio/incoming`. 착신 시 **경량 call/dispatch 상태만 생성**(call_id 발급, RINGING) + `<Connect><Stream>`으로 외국인 레그 tap. **OpenAI DualSession은 직원 claim 시점까지 지연 생성.** **왜:** 착신 즉시 DualSession을 만들면 **아무도 안 받는 대기 통화가 OpenAI 세션 2개·동시통화 cap·AI 비용·VAD/메모리를 점유** → 비용·수용량 보호 실패. **흐름:** 착신 → dispatch(RINGING) → Stream 연결 → 대기 안내 → 직원 claim → **용량 예약(FR-6.3b)** → DualSession 생성 → pickup 토큰 → 직원 WS + Stream handoff → CONNECTED. (인바운드 진입점은 FR-4b.1 서명검증 대상.)
- **FR-6.1a (신규 · 대기 단계 미디어 — 코덱스 리뷰) PendingMediaHandler:** **문제:** 현 `/twilio/media-stream/{call_id}`는 **DualSession이 없으면 즉시 WS를 닫는다**(`twilio_webhook.py:97-101` — "start_call에서 이미 생성" 가정). 또 `<Connect><Stream>` 뒤의 `<Say>`는 **Stream이 끝나야 실행**되므로 대기 안내를 뒤 verb로 붙일 수 없다. → **대기 전용 경로:** `WAITING_FOR_AGENT` 동안 **DualSession/AudioRouter 없이** Twilio Stream을 유지하고 **대기 음성을 Stream으로 직접 송신**하는 `PendingMediaHandler`. **명세(코덱스 F-2 · 재사용 자산 없음 = 신규 코드):** (a) **대기 오디오 소스** = 정적 보류음 파일 재생(PoC 기본) or TTS 안내 — 택1 · (b) **Twilio media 메시지 송신** = outbound μ-law base64 프레임을 Twilio media JSON으로 인코딩·송신(기존 `TwilioMediaStreamHandler.send_audio`에서 추출 가능한지 우선 확인, 없으면 신규 헬퍼) · (c) **handoff = Stream 재연결이 아니라 동일 WS의 소비자 교체**(PendingMediaHandler → AudioRouter), claim 시점 **in-flight 프레임 경계에서 무손실 전환**(글리치·중복 프레임 방지). **직원 claim + DualSession 초기화 완료 후 handoff.** 대기 중 **caller hangup · stream disconnect · claim timeout** 시 dispatch 상태 종료 + 자원 정리. _(이 경로가 없으면 구현자가 결국 착신 즉시 DualSession을 다시 만들게 됨 — FR-6.1 지연 생성 무력화. 공수 재추정에 독립 라인으로.)_
- **FR-6.2 타깃 라우팅 + tenant 주입:** 착신 DID → 대상 기관·**언어쌍(`tenant_call_config.languages`)** 매핑을 WI-3에서 조회, **resolve된 `tenant_id`를 세션 생성 시 주입**(인바운드는 FR-3.1 tenant 관통 시작점이 '요청'이 아니라 'DID→tenant', M-8). **매핑 미존재(no-match) = 명시적 거절 TwiML**(default 라우팅 금지 · C-1 fail-closed 일관, W-2). PoC 기본 = 번호별 고정 매핑(IVR은 §8, 후속).
- **FR-6.3 기관 응대 = 웹 부스(PoC 기본):** 기관 직원은 **기존 양방향 오디오 경로(`/stream`) 재사용**한 웹 창구에서 응대(PSTN 1레그). 세션 역할 스왑(외국인→Twilio쪽/Session B, 기관→웹쪽/Session A) + 언어 방향 설정. **웹부스 WS 접속 인증 = 단기 pickup 토큰**(장기 기관 API Key 부착 금지 — §8-#1·C-1/C-2, W-5).
- **FR-6.3a (프론트 스코프 — §1.3 Non-Goal 부분 해제) 웹 부스 응대 진입면:** 현재 양방향 클라이언트(`RealtimeCallView`+`useRelayCall`+`web-recorder`/`web-player`)는 **아웃바운드 발신 진입에만 묶여 있다** — 브라우저가 `/calls/start`로 call_id를 먼저 만든 뒤에야 `/call/[callId]`에 도달한다. 인바운드는 call_id를 Twilio 착신이 만들므로 **직원을 그 통화로 데려다줄 진입면이 없다**(monitor는 읽기전용). → **신규 프론트(오디오 엔진은 재사용, UI만):** ① 해당 기관의 **인바운드 대기 통화 목록/알림 → 응대(pickup) 액션** · ② 사용자 로그인(JWT) + **단기 pickup 토큰**으로 WS 접속(FR-6.3b·§8-#1) · ③ **응대측 역할·언어방향**(Session A=기관/웹, Session B=외국인/Twilio) 설정. **한정: 진입·인증·방향 UI뿐 — 웹앱 전면 개편·디자인 변경 제외.** _(단, "받을 수 있는 부스"의 실제 무게는 아래 FR-6.3b 백엔드 디스패치에 있다 — 프론트가 얇다고 WI-6이 가벼운 게 아니다.)_
- **FR-6.3b (신규 · 백엔드 콜 디스패치 상태머신 — 코덱스 리뷰) 인바운드 pickup:** 착신~응대 사이를 서버가 중재한다. **상태 전이:** `RINGING →(Twilio Stream 연결 완료 시) WAITING_FOR_AGENT → CLAIMED → SESSION_STARTING → CONNECTED`. **종료 상태(코덱스 4차):** `CONNECTED → ENDED`(정상 종료) · `RINGING/WAITING_FOR_AGENT/CLAIMED/SESSION_STARTING → CANCELLED`(caller hangup·직원 이탈·초기화 실패) · `WAITING_FOR_AGENT → TIMEOUT`(무응답) · `* → REJECTED`(권한·no-match). 허용 전이만 명시, 그 외 거부. `SESSION_STARTING` = **용량 예약~DualSession 생성 구간**(cap 경쟁 방지, 아래).
  - **데이터 모델 (코덱스 · 마이그레이션 `004_*.sql`, WI-3 연동):** 인메모리가 아니라 **DB를 authoritative source**로. **신규 테이블 `inbound_call_dispatch`로 확정**(계산·조회가 잦은 디스패치 상태를 `calls`와 분리): `inbound_call_dispatch(call_id, tenant_id, state, claimed_by, claim_expires_at, connected_at, ended_at, end_reason, version, created_at, updated_at)` — `ended_at`/`end_reason`으로 운영 추적·stale row 정리(코덱스 4차). **원자적 claim = 조건부 UPDATE:** `UPDATE ... SET state='CLAIMED', claimed_by=:uid, claim_expires_at=now()+ttl, version=version+1 WHERE call_id=:id AND state='WAITING_FOR_AGENT' AND claimed_by IS NULL` — 영향 행 1이면 성공. `version`으로 optimistic lock. **claim TTL** 만료 시 자동 회수. **정리 규칙:** 종료 통화 stale claim 정리 · **서버 재시작 후 복구(코덱스 CP-1):** 단일 프로세스 재시작 = 모든 인메모리 세션(DualSession·AudioRouter·WS) 소멸이므로, DB에 남은 **`CONNECTED`/`SESSION_STARTING` 잔존 row는 유령** → **`ENDED`/`CANCELLED`(`end_reason='server_restart'`)로 마감**, `WAITING/CLAIMED`는 orphan claim·예약 해제. (CapacityManager는 인메모리라 자동 리셋되지만 DB는 아님 — 반드시 마감.) · `TIMEOUT/REJECTED` 보존기간 명시.
  - **원자적 claim + 응답 코드 구분(코덱스):** 동시 클릭 시 **정확히 1명만 선점**. 실패 응답을 구분 — **이미 다른 직원이 claim = 409** · **다른 tenant거나 권한 없음 = 403**.
  - **용량 예약 = cap 경쟁(TOCTOU) 방지(코덱스 2·3차):** cap을 CONNECTED에서만 점유하면 **동시 claim들이 각자 cap 검사를 통과한 뒤 세션을 만들어 cap 초과**. → **claim 성공과 용량 예약을 원자적으로**, **예약 성공 후에만 DualSession 생성**(SESSION_STARTING). **예약 주체 = 공용 `CapacityManager`(FR-5.5)** — 인바운드·아웃바운드 공유, 인바운드만 잠그면 아웃바운드 `/calls/start`와 동시 시작 시 여전히 초과하므로 반드시 같은 매니저. **불변식 `active + reserved ≤ max_concurrent_calls`.** **대기 통화 수는 별도 `max_waiting_calls` 상한**(대기열 폭주 방지).
  - **SESSION_STARTING TTL 경쟁 + 통합 cleanup(코덱스 4차 · PoC 확정 §8-#11):** `claim_expires_at`이 DualSession 생성 도중 만료되면 sweeper가 claim·예약을 반환하는 동시에 초기화가 성공하는 **split-brain** 가능. **PoC(단일 프로세스) 처리 = 인메모리 생성 가드**(생성 코루틴이 도는 통화는 sweeper 회수 대상에서 제외) **+ 고정 초기화 timeout 상수**. _(DB `session_start_deadline` 컬럼·분산 sweeper는 멀티서버 이월.)_ **토큰 발급·DualSession 생성·Stream handoff 중 어느 단계든 실패하면 → 단일 cleanup 경로**(상태 종료(CANCELLED) + 세션 정리 + `CapacityManager` 예약 반환 + PendingMediaHandler/Stream 정리)로 수렴 — **이 단일 cleanup은 필수(유지)**.
  - **중복 송신 WS 차단:** claim 후 다른 브라우저의 송신 WS 접속 거부(`register_app_ws`의 무조건 덮어쓰기 제거 — claim한 user_id만 허용).
  - **claim 회수:** 선점 직원이 CONNECTED 전에 이탈/토큰 만료 시 claim 해제 → 재대기(WAITING_FOR_AGENT 복귀).
  - **App WS 끊김 ≠ 즉시 통화종료:** 새로고침·네트워크 재시도에 **재연결 유예**(현행 `stream.py:102` 즉시 `cleanup_call` 커플링 완화). idempotent pickup(중복 클릭·재시도 안전).
  - **무응답 처리:** timeout 내 아무도 claim 안 하면 외국인에게 **대기/종료 안내 TwiML** + fallback(담당 없음 시 안내 후 종료).
  - **연결 전 대기 안내:** claim~CONNECTED 사이 외국인 레그에 대기 안내(보류음/멘트).
  - **tenant별 대기열 (PoC 확정 = 단순 FIFO):** 대기 통화가 여럿일 때 tenant 스코프 **FIFO**(대기 1건이면 큐 없음). **정교한 fair-queueing·공정성 알고리즘은 후속 이월**(멀티서버 공유 큐에서 어차피 재작성 — §8-#11).
- **FR-6.4 (옵션) 데스크 전화 브리지:** 기관이 기존 전화기로 받아야 할 때만 **아웃바운드 레그 추가(2-레그)** → 이 경우에만 **통화당 VAD 2배 + 데스크 표시번호 처리** → 인바운드 수용량 별도 산정. PoC 기본(웹 부스)에선 해당 없음.
- **FR-6.5 생명주기:** 인바운드 세션도 `call_manager` idempotent cleanup 재사용, 레그 종료 시 정리. **단 FR-6.3b대로 App WS 끊김과 통화 종료의 커플링을 분리**(재연결 유예 후에만 정리).
- **완료기준:** 실전화 인바운드 착신 → **디스패치 상태머신(FR-6.3b)으로 직원이 pickup** → 양방향 통역 e2e + 아웃바운드(현행)와 공존 + (웹 부스 기준) 수용량 현행과 동일 + **동시 pickup 시 정확히 1명 선점(나머지 409/403)** + **직원 새로고침/재연결에 통화 유지** + **무응답 timeout·안내 동작** + **(코덱스 2차) DualSession 지연 생성: `WAITING_FOR_AGENT` 동안 OpenAI 세션 0 · claim 실패/timeout 시 OpenAI 비용 0 · 세션 생성 실패 시 claim 해제/REJECTED · 세션 생성 중 이탈 시 세션 정리** + **(코덱스 3차) 대기 미디어·용량: DualSession 없이 Twilio Stream이 대기 동안 유지 · 대기 안내가 실전화에서 들림 · claim 후 동일 통화가 안 끊기고 AudioRouter로 handoff · 대기 중 caller hangup 시 dispatch row 종료 · 동시 claim 다수에도 `active + reserved ≤ cap` 불변식 유지(cap 초과 0)**. _(응대 진입면 프론트 포함 — §1.3 Non-Goal 예외.)_

> **⚖️ PoC 최소 구현선 — ✅ 확정 (§8-#11, 2026-07-15):** WI-6이 4라운드 리뷰로 커진 만큼 전체 무게를 의식적으로 결정했다. **아래 컷으로 확정 → 공수 4~7일 → 3~5일.**
>
> - **유지(필수 · 검증된 실제 버그 방어):** 원자적 claim(더블 pickup 방지, 409/403) · 공용 CapacityManager 용량 예약(cap 초과 방지) · DualSession 지연 생성 + PendingMediaHandler 대기 미디어 · claim 후 Stream handoff · App WS 재연결 유예 · 종료 상태 + 단일 cleanup 경로 · 무응답 timeout·안내.
> - **PoC 단순화(확정):** **tenant 대기열 = 단순 FIFO(대기 1건이면 큐 없음)**, 정교한 fair queueing 후속 · **fallback = 안내 후 종료**(복잡 분기 후속) · **SESSION_STARTING split-brain = 단일 프로세스용 인메모리 생성 가드 + 고정 초기화 timeout**(DB `session_start_deadline` 컬럼·분산 sweeper는 멀티서버 이월 — "seam은 지금, 분산 구현은 나중" 원칙).
> - **후속 이월:** IVR/입력 선택(§8-#7) · 데스크폰 브리지(FR-6.4) · 번호 풀(FR-3.6, `quarantine`·provisioning reconciliation 포함 — WI-6 무게 아님).

---

## 4. Non-Functional Requirements & Success Metrics

| 지표                         | 현재                                                         | 목표                                                                                                                                            |
| ---------------------------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| 검증된 동시 통화 상한        | ✅ 실측: 안정~20/저하25/포화30 (2vCPU)                       | cap을 18~20으로 설정                                                                                                                            |
| 안정 동시 통화 수            | ✅ 실측 **≈20통** (구 '추정 5~8'은 폐기)                     | VAD 오프로드 후 **재측정으로 증가 확인** (GIL ~1.8x 전제 검증됨)                                                                                |
| 통역 지연 p95 (부하 시)      | 논문 p95 ~1023ms                                             | **베이스라인 이하 유지**                                                                                                                        |
| 인증 없는 테넌트 데이터 경로 | 다수(발신·조회·모니터)                                       | **0건**                                                                                                                                         |
| 기관 격리                    | 불가(tenant 없음)                                            | **2개 기관 격리 검증**                                                                                                                          |
| 기존 테스트                  | `pytest --co` 실측 (README '184'는 stale · `def test_` ~469) | **전량 통과 유지**                                                                                                                              |
| 회선 전환 방식               | 코드 상수(=env 단일 전역값)                                  | **DB 값 교체(코드 무변경)**                                                                                                                     |
| 테넌트 격리 강제             | 없음                                                         | **RLS/스코프 계층 + fail-closed** (C-1)                                                                                                         |
| 원본오디오 미저장            | 미검증                                                       | **검사로 확인** (M-7)                                                                                                                           |
| 통화 방향 · 인바운드 수용량  | 아웃바운드(웹 발신) 전용                                     | **하이브리드: 인바운드(웹부스)+아웃바운드 공존 e2e** (WI-6) · 인바운드 수용량 **현행과 동일**(PSTN 1레그, 데스크폰 브리지 옵션만 2배·별도 산정) |

---

## 5. Phasing & Sequencing

```
1주차:  WI-1 부하테스트(✅완료)  →  WI-2 VAD 오프로드(상한↑) → WI-2 재측정
2주차 A:  WI-5 운영·CapacityManager  →  WI-4b Twilio 서명검증  →  WI-6 A(인바운드 진입·대기 미디어 스켈레톤)
2주차 B:  WI-3 멀티테넌트·격리  →  WI-4a 인증  →  WI-6 B(디스패치 스키마·claim 스켈레톤)
3주차:    WI-6 A/B 병렬 완성 → bootstrap seam 통합 → 하이브리드 e2e·실전화 검증 (PoC 최소 3~5일)
```

- **⚠️ 일정 전제 (F-1 갱신):** 이 3주 표는 **2인 병렬 기준**이다. 분담은 **A = WI-2→WI-5→WI-4b→WI-6 A**, **B = WI-3→WI-4a→WI-6 B**로 확정한다. WI-4b를 텔레포니 소유 A로 옮겨 B의 크리티컬 사슬을 줄이고, WI-6도 같은 도메인 seam으로 분할해 합류 후 생기던 통짜 꼬리를 제거한다. 단, 실제 통합·실전화 e2e에는 양쪽 선행작업이 모두 필요하다. 슬립 시 §8-#11 필수 항목(원자적 claim·CapacityManager·단일 cleanup)은 압축하지 않고 "이월" 버킷에서만 범위를 줄인다.
- **WI-1 먼저인 이유:** 이후 모든 판단의 수치 근거. VAD 개선 효과도 이 베이스라인으로만 증명(✅ 완료).
- **의존성 (M-5 갱신):** WI-4a의 조회·모니터 tenant 스코핑은 WI-3에 강결합하므로 B는 순차 진행한다. WI-4b는 WI-3과 무관한 텔레포니 관심사이며 A가 소유한다. WI-6의 각 반쪽은 자기 선행작업과 계약 stub이 준비되는 대로 착수할 수 있지만, **전체 완료 = WI-3 + WI-4a + WI-4b + WI-5 + A/B 통합**이다. 교차 계약은 `bootstrap_inbound_session`, pickup 토큰 클레임, CapacityManager reserve/commit/release, 단일 cleanup으로 고정한다.
- **착수 게이트:** WI-3 전 **격리 방식(§8-#2)**, WI-4a 전 **인증 방식(§8-#1)**, WI-4b 전 **callback URL(§8-#3)** 확정.
- **공수 (M-4):** WI-4는 인증(4a)+Twilio 서명(4b)로 분할, 합계 **2~4일**(WebSocket 인증·서명검증이 무겁다).
- **WI-6 (인바운드 + 콜 디스패치):** 세션 생성 시점 역전 + 역할 스왑, 미디어 파이프라인 재사용. 타깃 라우팅이 WI-3 의존 → WI-3 후. **웹 부스(1레그)면 수용량 현행과 동일**(데스크폰 브리지 옵션만 재측정). **⚠ 재추정 2~4일 → 4~7일(코덱스):** "UI만 추가"가 아니라 **pickup 상태머신·원자적 claim·재연결 유예**가 있는 작은 call-dispatch 기능(FR-6.3b). 프론트는 얇지만 백엔드가 커짐. 응대 진입면 프론트(FR-6.3a)는 §1.3 Non-Goal 예외.

---

## 6. Risks & Mitigations

| 리스크                                                                          | 영향              | 완화                                                                                                                                        |
| ------------------------------------------------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| VAD 스레드 경계에서 상태머신 순서 꼬임                                          | 오탐/미탐         | **통화별 큐 키로 순차성 보장(고정 풀 · FR-2.1/2.2)** + `test_local_vad.py` 회귀. (통화당-스레드 채택 시엔 통화당 단일 워커로 순차성 보장.)  |
| 부하테스트 실통화 비용(OpenAI/Twilio)                                           | 비용              | loopback/mock 우선, 실통화는 소수·분리                                                                                                      |
| 기존 데이터에 `tenant_id` 소급                                                  | 마이그레이션 오류 | 기본 테넌트로 backfill + 마이그레이션 리허설                                                                                                |
| 인증 추가로 웹 클라이언트 파손                                                  | 통화 시작 실패    | 웹과 키 발급/전달 계약 사전 합의, 스테이징 검증                                                                                             |
| 외부 동시성 한도가 서버보다 먼저 걸림                                           | 상한 오판         | WI-1에서 OpenAI/Twilio 한도 포함 계측, 필요 시 사전 상향 신청                                                                               |
| **테넌트 스코핑 쿼리 1건 누락 → 교차 유출**                                     | 치명              | RLS 또는 단일 scoped 계층으로 구조적 강제 + fail-closed (C-1)                                                                               |
| **인증 잘못된 순서 배포 → 전 통화 중단**                                        | 높음              | enforce 2단계 롤아웃 + Twilio 경로 화이트리스트 (C-2)                                                                                       |
| **Twilio callback URL 불일치 → 콜백 전부 403**                                  | 높음              | public callback URL을 env로 고정 (C-3)                                                                                                      |
| VAD 워커 큐/키 누수 → 장시간 후 리소스 고갈                                     | 중                | **고정 풀: 통화 종료 시 통화별 큐/키 완전 해제 + 100회 반복 누수 0 회귀** (FR-2.5·M-2). (통화당-스레드 채택 시엔 스레드 join·수 원복 확인.) |
| **VAD v6 업그레이드 시 PyTorch 패키지 오채택 → GIL 점유로 오프로드 무력화**     | 높음              | **ONNX-Runtime(C++) 경로 한정** · PyTorch `silero-vad` pip 패키지 금지 · 모델 교체는 WI-2와 분리 (§8-#10)                                   |
| 데스크폰 브리지(옵션) = 통화당 VAD 2배                                          | 중                | PoC 기본은 웹부스(1레그·해당 없음). 데스크폰 필요 시만 인바운드 cap 별도 산정 (WI-6 FR-6.4)                                                 |
| **브라우저에 기관 API Key 부착 → 직원 1명 유출=기관 전체 권한 유출**            | 치명              | 서비스 인증(기관 Key, S2S)과 사용자 인증(직원 JWT+단기 pickup 토큰) 분리 (§8-#1·FR-6.3b · 코덱스)                                           |
| **인바운드 동시 pickup → 두 직원이 한 통화 선점 / 이중 송신 WS**                | 높음              | 원자적 claim(정확히 1명) + 중복부착 409/403 + `register_app_ws` 무조건 덮어쓰기 제거 (FR-6.3b · 코덱스)                                     |
| **직원 새로고침·네트워크 블립 → App WS 끊김이 통화 전체 종료**                  | 높음              | App WS 끊김↔`cleanup_call` 커플링 분리 + 재연결 유예 (FR-6.3b/6.5 · 코덱스)                                                                 |
| **인바운드 예약만 잠그고 아웃바운드 soft-cap 방치 → 혼합 동시성에서 cap 초과**  | 높음              | 인·아웃바운드 공용 `CapacityManager`로 reserve/commit/release 원자화 + 기존 `/calls/start` soft-cap 교체 (FR-5.5 · 코덱스 4차)              |
| **SESSION_STARTING 중 claim TTL 만료 → sweeper 회수 ↔ 초기화 성공 split-brain** | 중                | SESSION_STARTING은 claim-TTL 회수 제외·`session_start_deadline`로만 정리 + 실패 시 단일 cleanup 경로 (FR-6.3b · 코덱스 4차)                 |

---

## 7. Verification & Rollout

- **회귀 게이트:** 기존 `tests/`(e2e·component·unit) 전부 통과 — 기준 숫자는 `pytest --co` 실측(README '184'는 stale). **(코덱스 C-3) WI 착수 시점에 `pytest --co -q | wc -l` 실측값을 baseline으로 1회 고정 기록**(approximate '~469' 대신 확정값 → 테스트 삭제성 회귀 탐지) + 통화 시작→통역→종료 수동 스모크.
- **수용량 검증:** WI-1 하니스로 before/after 곡선 비교 리포트를 PR에 첨부.
- **격리 검증:** 두 테넌트 픽스처로 발신번호·프롬프트·이력·권한 분리 e2e.
- **배포:** WI별 순차 머지. PoC 기간에는 **데모/온보딩 중 배포 동결**(재시작 = 진행 통화 드롭, 단일서버 한계) — 이 제약 자체가 WI-5 런북에 포함되며, 근본 해소는 다음 PRD(무중단 배포)로 이월.
- **개인정보 릴리스 게이트 (코덱스 · FR-3.5 연동):** 개인정보 보존·파기·로그 마스킹·Langfuse 정책은 이 PRD 스코프 밖(`pilot-readiness.md` 별도 트랙)이나, **실제 기관·민원인 데이터를 쓰는 PoC 배포 전 `pilot-readiness.md`의 보존기간·파기·로그 마스킹·Langfuse 차단 완료를 별도 릴리스 게이트로 확인**한다. **(코덱스 CP-4) 인바운드 = 외국인(민원인) 발신이라 PII 민감도가 더 높다** — 인바운드 caller의 전사·전화번호도 명시적으로 FR-3.5 파기/마스킹 대상에 포함. **미완료 시 합성 데이터/내부 테스트만 허용**.

---

## 8. Open Decisions (진행 전 확정)

1. **인증 방식 — 서비스 인증 ↔ 사용자 인증 분리 (✅ 확정, 2026-07-16 · C-2 · 코덱스 리뷰로 재프레이밍)** — 기존 "기관 API key vs JWT" 이분법은 **틀린 축**이다. **브라우저(웹 부스)에 장기 기관 API Key를 실으면** XSS·확장·로그·devtools로 유출되고, **직원 1명 유출 = 기관 전체 권한 유출** → C-1 멀티테넌트 격리와 정면충돌. **확정 = 두 인증을 분리하는 설계:**
   - **기관 API Key = 서버 간(S2S)·온보딩 자동화 전용** (브라우저 노출 금지). HTTP 헤더 `X-Wigvo-API-Key`; relay에는 raw key가 아닌 tenant별 SHA-256 digest만 설정한다.
   - **직원 웹 부스 = WIGTN-SSO Supabase JWT**로 신원 확립. relay가 공개 JWKS(ES256)로 서명을 검증한 뒤 WIGVO `users`의 활성 tenant membership을 해석한다.
   - **WS 접속 직전 단기 pickup 토큰 발급:** `call_id + tenant_id + user_id + role + exp`(1–5분·1회용 또는 짧은 TTL), 서버가 **call 소유권(tenant 일치)**을 검증. 재사용·만료 시 거부.
   - **폐기·재생 정책 (코덱스 S-1 · 미정의였음):** **"1회용" 강제 = WS 인증 스텝에서 dispatch row 재확인** — 토큰만 보지 말고 `claimed_by == token.user_id AND state IN (CLAIMED, SESSION_STARTING, CONNECTED)`를 DB로 검증(stateless JWT로도 폐기 성립). **claim 회수 시(FR-6.3b: 직원 이탈 → WAITING 복귀) 이미 발급된 토큰은 이 재확인으로 자동 무효화**(state가 WAITING이거나 `claimed_by`가 바뀌면 접속 거부) → 교차 직원 세션 하이재킹 창 차단. (단일 프로세스면 사용-소진 인메모리 set도 대안.)
   - **토큰 전송 방식 ✅ `Sec-WebSocket-Protocol` 확정:** URL query 금지. 사용자 WS는 `wigvo.jwt` marker + JWT, WI-6 pickup WS는 `wigvo.pickup` marker + 단기 token의 두 subprotocol 값으로 전달한다. 서버는 marker만 선택 응답해 token을 응답 헤더에 재노출하지 않는다. (FR-4a.1·FR-6.3a 반영.)
2. **테넌트 격리 방식 (⚠ WI-3 착수 전 게이트, C-1)** — Postgres RLS vs 단일 tenant-scoped 계층. fail-closed 전제. **(#5) RLS 채택 시 주의: `calls`는 웹앱이 Drizzle로 읽는 공유 테이블 — 웹은 'RLS 생략+앱 스코핑' 설계라, RLS를 켜면 tenant GUC 미설정 웹 쿼리가 깨질 수 있음(웹 영향 검토 필요). 결합 부담이면 (b) DAO가 웹 무영향으로 더 안전.**
3. **Twilio callback base URL (⚠ WI-4b 착수 전 게이트, C-3)** — 서명검증 URL 불일치 방지 위해 env로 고정할 정확한 public URL.
4. **데모 cap 값** — 18(지연 안전) vs 20(처리량). _(VM 스펙은 `e2-standard-2` 실측 확정, 실통화 예산은 스텁 모드로 불필요.)_
5. **테넌트 상한·레이트 정책** — 동시 통화 상한(전역 `max_concurrent_calls` vs tenant별) + **아웃바운드 `/calls/start` 호출 레이트** + **(코덱스 S-2) 인바운드 착신 레이트(tenant/DID별)**. 서명검증(WI-4b)은 _위조_ 착신만 막지 *정당 서명된 대량 착신*은 못 막음 → DID 폭주 시 dispatch row·PendingMediaHandler Stream·예약 시도 반복으로 자원 소진(DoS 유사). `max_waiting_calls`(동시 대기 상한, FR-6.3b)는 시간당 착신율 제한이 아니므로 별도 필요. 최소한 **`max_waiting_calls` 초과 시 즉시 거절 TwiML**을 FR-6.3b 완료기준에 편입.
6. **외부 동시성 한도** — 스텁 모드라 미측정. OpenAI 세션(통화당 2)·Twilio 채널·DB풀(5)은 목표 규모 확정 시 확인 + **상향 신청 리드타임 주의(MIN-6)**.
7. **인바운드 타깃 라우팅 (WI-6)** — 착신 DID별 고정 매핑(PoC 기본) vs IVR/입력 선택.
8. **기관 응대 방식 (WI-6)** — 웹 부스(1레그, PoC 권장) vs 데스크 전화 브리지(2레그·데스크번호). 후자만 인바운드 cap 별도 산정. **✅ 확정: 웹 부스 채택 → 응대 진입면 최소 프론트가 스코프에 편입(FR-6.3a, §1.3 Non-Goal 부분 해제).** 데스크 브리지는 옵션 유지.
9. **VAD 워커 모델 (WI-2, #6)** — 통화당 전용 스레드 vs vCPU 배수 고정 풀. 프로파일링·재측정으로 확정.
10. **VAD v6 모델 업그레이드 (신규 · WI-2와 분리)** — 팀원 제안(v6 채택). **팩트체크(직접 검증 2026-07-15):** 공식 Silero VAD **v6 ONNX는 현행 v5(`silero-vad-lite 0.2.1` 번들)와 API 완전 동일** — 입력 `input`·`state[2,1,128]`·`sr`, 출력 `output`·`stateN`, **opset 16 동일**. onnxruntime에서 **v5 방식 호출(512샘플 @16k)로 v6 모델 정상 추론 확인**(master `silero_vad.onnx`·`op18_ifless` 모두). → "v6가 파이썬 의존이 생겼다"·"`[1,576]`+64샘플 컨텍스트 버퍼 필요"는 **공식 ONNX 경로엔 해당 없음**(그건 Apple MLX 재export 얘기). **결론:** v6 채택 = **모델 파일 스왑 수준**, GIL 해제(ONNX-C++/ctypes 경로) 유지, `local_vad.py`의 `.process(512)`/`.reset()` 인터페이스 무변경. **잔여 작업 2가지:** (a) `silero-vad-lite 0.2.1`은 **v5 번들·2024-10 이후 미유지보수** → v6 번들 버전 확보 또는 번들 `silero_vad.onnx`(opset 16)를 v6로 교체, (b) v6 **재학습으로 확률분포 이동** → `local_vad.py` 임계값(`_speech_threshold`·`_silence_threshold`·hysteresis) **재튜닝 필요**. **⛔ 하드 룰:** PyTorch `silero-vad` pip 패키지 채택 금지(프레임당 파이썬 추론 → GIL 점유 → WI-2 오프로드 무력화). **WI-2(오프로드)와 분리 진행** — 모델 교체를 before/after 벤치마크에 섞으면 측정 오염(FR-2.3 "임계값 불변" 유지). **참고(별도 이월 · 코덱스):** v6 전환 시 **8kHz 네이티브 입력**(Silero 8k 네이티브 = 256샘플 프레임)으로 가면 현재 `local_vad.py`의 `np.repeat` **zero-order-hold 8k→16k 업샘플링 왜곡**(`:13-15,123`)까지 제거 가능하다(참조: `docs/infra/wigvo_vad_explainer.pdf` 리샘플링 이슈). **이 §8-#10 '최소 스왑'(16k/512 유지) 범위 밖 → 별도 이월/추적 항목**으로 남긴다(유실 방지 · 리샘플링 개선은 모델 스왑과 독립).
11. **WI-6 PoC 최소 구현선 — ✅ 확정 (2026-07-15)** — **WI-6이 PoC 착수의 가장 긴 단일 구간(long pole)**이라 전체 무게를 의식적으로 결정. **결정: PoC 최소 컷 채택 → 공수 4~7일 → 3~5일.** 유지=필수(원자적 claim·CapacityManager·지연 생성·대기 미디어·재연결 유예·종료 상태+단일 cleanup·timeout) · 단순화=tenant 대기열 **FIFO**·fallback **안내 후 종료**·split-brain **인메모리 가드+고정 timeout** · 이월=fair-queueing·IVR·데스크폰·번호 풀(quarantine/reconciliation 포함, WI-6 무게 아님). 상세 = WI-6 완료기준 아래 "PoC 최소 구현선 ✅ 확정" 박스.

---

## Appendix — 근거 문서 / 코드 참조

- 전략: 「WIGVO 고도화 전략」(`docs/infra/wigvo_advancement_strategy.pdf`) · 「기관 서비스 전환 계획」(`docs/infra/wigvo_platform_plan_biz.pdf`) · 통신·회선: **`docs/poc-number-architecture.md`** · 개인정보·파일럿: **`docs/pilot-readiness.md`**
- 실행 상세(이월분): 「인프라 마이그레이션 보고서 v2」(`docs/infra/wigvo_infra_migration_report.pdf`) · `docs/infra/aws-migration-plan.md`
- _⚠️ `docs/`는 gitignore(로컬 전용) — 팀 공유는 수동/PDF. 경로는 작업 머신 기준(팀원 체크아웃엔 없을 수 있음)._
- 코드: `config.py:69`(상한) · `local_vad.py:189`·`voice_to_voice.py:392`·`text_to_voice.py:409`(VAD) · `calls.py:38`(무인증 발신) · `outbound.py`(from\_ 하드코딩) · `logging_config.py`(로깅 컨텍스트) · `db/pg_client.py`(영속화) · `apps/relay-server/migrations/`(마이그레이션)
