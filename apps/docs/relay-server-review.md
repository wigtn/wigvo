# Relay Server 소스코드 리뷰

> 대상: `apps/relay-server/` | 분석일: 2026-02-27
> 목적: 현황 파악 + 리팩토링 방향 수립

---

## 목차

1. [프로젝트 구조 Overview](#1-프로젝트-구조-overview)
2. [대형 파일 & 관심사 분리](#2-대형-파일--관심사-분리)
3. [코드 중복 분석](#3-코드-중복-분석)
4. [타입 안전성](#4-타입-안전성)
5. [에러 핸들링 & 로깅](#5-에러-핸들링--로깅)
6. [보안 & API 라우트](#6-보안--api-라우트)
7. [테스트 현황](#7-테스트-현황)
8. [리팩토링 우선순위 로드맵](#8-리팩토링-우선순위-로드맵)

---

## 1. 프로젝트 구조 Overview

### 요약 수치

| 항목 | 수량 |
|------|------|
| 소스 파일 (`src/**/*.py`, `__init__` 제외) | 38개 |
| 테스트 파일 (`tests/**/*.py`) | 36개 |
| 소스 코드 줄 수 | **8,166줄** |
| 테스트 코드 줄 수 | **7,316줄** |
| pytest 수집 테스트 | 265개 |
| 테스트 함수 (`def test_`) | 355개 |
| 테스트 클래스 (`class Test*`) | 79개 |

### 디렉토리 트리

```
apps/relay-server/
├── src/
│   ├── main.py                          # FastAPI 앱 엔트리 (71줄)
│   ├── config.py                        # pydantic-settings 환경변수 (147줄)
│   ├── types.py                         # 공유 타입 — Pydantic 모델 (308줄)
│   ├── call_manager.py                  # 통화 라이프사이클 싱글톤 (252줄)
│   ├── logging_config.py                # 구조화 로깅 (ContextVar, JSON) (163줄)
│   │
│   ├── routes/                          # FastAPI 라우터
│   │   ├── health.py                    # GET /health (20줄)
│   │   ├── calls.py                     # POST /relay/calls/start, /end (164줄)
│   │   ├── stream.py                    # WS /relay/calls/{id}/stream (102줄)
│   │   └── twilio_webhook.py            # POST/WS /twilio/* (150줄)
│   │
│   ├── realtime/                        # OpenAI Realtime API 세션
│   │   ├── sessions/
│   │   │   ├── session_manager.py       # DualSessionManager + RealtimeSession (413줄)
│   │   │   ├── session_a.py             # User→수신자 번역 세션 (494줄)
│   │   │   └── session_b.py             # 수신자→User 번역 세션 (1,089줄) ★
│   │   │
│   │   ├── pipeline/                    # Strategy 패턴 파이프라인
│   │   │   ├── base.py                  # BasePipeline ABC (134줄)
│   │   │   ├── voice_to_voice.py        # V2V 파이프라인 (596줄)
│   │   │   ├── text_to_voice.py         # T2V 파이프라인 (603줄)
│   │   │   ├── full_agent.py            # Agent 파이프라인 (74줄)
│   │   │   └── echo_gate.py             # EchoGateManager (251줄)
│   │   │
│   │   ├── audio_router.py              # 얇은 위임자 — Pipeline 선택 (153줄)
│   │   ├── chat_translator.py           # Chat API 번역기 (136줄)
│   │   ├── recovery.py                  # 연결 복구 + Degraded Mode (588줄)
│   │   ├── local_vad.py                 # Silero VAD + RMS Energy Gate (282줄)
│   │   ├── ring_buffer.py               # 오디오 링 버퍼 (147줄)
│   │   ├── interrupt_handler.py         # Interrupt 우선순위 제어 (113줄)
│   │   ├── context_manager.py           # 대화 컨텍스트 윈도우 (86줄)
│   │   ├── first_message.py             # 첫 메시지 타이밍 (95줄)
│   │   └── audio_utils.py               # PCM/μ-law 유틸 (57줄)
│   │
│   ├── prompt/                          # 시스템 프롬프트
│   │   ├── templates.py                 # 언어별 템플릿 (219줄)
│   │   └── generator_v3.py              # 프롬프트 생성기 (97줄)
│   │
│   ├── guardrail/                       # 발화 필터링 (PRD M-2)
│   │   ├── checker.py                   # Level 분류 + 파이프라인 (199줄)
│   │   ├── filter.py                    # 텍스트 필터 (163줄)
│   │   ├── dictionary.py                # 블록리스트 사전 (102줄)
│   │   └── fallback_llm.py              # LLM 폴백 교정 (124줄)
│   │
│   ├── tools/                           # Agent Mode Function Calling
│   │   ├── definitions.py               # 도구 스키마 정의 (150줄)
│   │   └── executor.py                  # 도구 실행기 (106줄)
│   │
│   ├── twilio/                          # Twilio 연동
│   │   ├── outbound.py                  # 아웃바운드 발신 (62줄)
│   │   └── media_stream.py              # Media Stream 핸들러 (89줄)
│   │
│   ├── db/
│   │   └── supabase_client.py           # DB 영속화 (113줄)
│   │
│   └── middleware/
│       └── rate_limit.py                # IP 기반 Rate Limiter (31줄)
│
├── tests/                               # 테스트 코드 (7,316줄)
│   ├── test_voice_to_voice_pipeline.py  # V2V 파이프라인 (908줄)
│   ├── test_chat_translator.py          # Chat 번역기 (877줄)
│   ├── test_echo_gate_manager.py        # Echo Gate (562줄)
│   ├── test_text_to_voice_pipeline.py   # T2V 파이프라인 (531줄)
│   ├── test_echo_gate.py                # Echo Gate 유닛 (458줄)
│   ├── test_session_b_metrics.py        # Session B 메트릭 (455줄)
│   ├── test_local_vad.py                # Local VAD (328줄)
│   ├── test_full_agent_pipeline.py      # Agent 파이프라인 (271줄)
│   ├── test_audio_router.py             # AudioRouter (231줄)
│   ├── test_call_manager.py             # CallManager (210줄)
│   ├── test_logging_config.py           # 로깅 (202줄)
│   ├── test_incremental_metrics.py      # 증분 메트릭 (211줄)
│   ├── test_context_manager.py          # 컨텍스트 관리 (106줄)
│   ├── test_types.py                    # 타입 (103줄)
│   ├── test_rate_limit.py               # Rate Limiter (96줄)
│   ├── test_guardrail.py                # 가드레일 (미확인)
│   ├── test_ring_buffer.py              # 링 버퍼 (미확인)
│   ├── test_function_calling.py         # Function Calling (미확인)
│   ├── test_cors_config.py              # CORS (미확인)
│   ├── test_stt_model_config.py         # STT 설정 (미확인)
│   ├── component/                       # 컴포넌트 데모/벤치마크
│   │   ├── test_loopback_call.py        # 루프백 통화 테스트 (270줄)
│   │   └── fake_realtime_session.py     # 페이크 세션 (237줄)
│   ├── integration/                     # 라이브 서버 테스트
│   ├── e2e/                             # E2E 양방향 통화 테스트
│   └── run.py                           # 테스트 러너 (148줄)
```

### 모듈별 역할 요약

| 모듈 | 줄 수 | 역할 |
|------|-------|------|
| `realtime/` | 4,459 | 코어 — 세션, 파이프라인, VAD, 에코 게이트, 복구 |
| `routes/` | 436 | API 엔드포인트 (REST + WebSocket) |
| `guardrail/` | 588 | 발화 품질 필터링 (Level 1/2/3) |
| `prompt/` | 316 | 시스템 프롬프트 생성 |
| `tools/` | 256 | Agent Mode Function Calling |
| 나머지 | 2,111 | config, types, logging, twilio, db, middleware |

---

## 2. 대형 파일 & 관심사 분리

### 2.1 대형 파일 목록 (250줄+)

| 파일 | 줄 수 | 관심사 수 | 핵심 문제 |
|------|-------|----------|-----------|
| `session_b.py` | **1,089** | 5 | STT 필터 + VAD + 출력 버퍼 + 번역 + 할루시네이션 방지 혼재 |
| `text_to_voice.py` | **603** | 3 | V2V와 ~250줄 중복 (가드레일 콜백, 타이머, VAD 핸들링) |
| `voice_to_voice.py` | **596** | 3 | T2V와 ~250줄 중복 |
| `recovery.py` | **588** | 1 | 단일 관심사 (연결 복구 + Degraded Mode) — **양호** |
| `session_a.py` | **494** | 2 | 이벤트 핸들러 + 가드레일 콜백, 중간 크기 — 허용 범위 |
| `session_manager.py` | **413** | 2 | Dual Session + 단일 Session 관리 — 허용 범위 |
| `types.py` | **308** | 1 | 타입 정의 전용 — **양호** |
| `local_vad.py` | **282** | 1 | VAD 전용 — **양호** |
| `call_manager.py` | **252** | 2 | 라이프사이클 + DB 영속화 — 허용 범위 |
| `echo_gate.py` | **251** | 1 | Echo Gate 전용 — **양호** |

### 2.2 파일별 상세 분석 & 분리 방향

#### session_b.py (1,089줄) — 최대 파일

**혼재된 5가지 관심사:**

1. **STT 필터링** (L24-180): 할루시네이션 블록리스트 (`_STT_HALLUCINATION_BLOCKLIST` 65항목), 정규화, 짧은 텍스트 필터
2. **VAD 이벤트 핸들링** (L800-990): speech_started, speech_stopped, silence timeout, debounced response
3. **출력 버퍼** (L496-560): `_pending_output` (오디오/캡션 큐) — Echo Gate 억제 중 버퍼링
4. **번역 분기** (L870-965): V2V는 Realtime 통합, T2V/Agent는 Chat API (`ChatTranslator`) 분기
5. **메트릭/타이밍** (산재): `_pending_stt_ms`, speech_duration, stt_after_stop 등 측정 로직

**분리 방향:**
```
realtime/sessions/
├── session_b.py                   # 오케스트레이터 (~400줄)
├── stt_filter.py                  # 블록리스트 + 정규화 + 짧은 텍스트 필터 (~180줄)
├── output_buffer.py               # pending_output 큐 관리 (~80줄)
└── session_b_metrics.py           # 메트릭 수집기 (~100줄)
```

#### text_to_voice.py (603줄) / voice_to_voice.py (596줄)

두 파이프라인은 **~250줄이 거의 동일한 코드**:
- `_call_duration_timer()` 22줄 × 2 (한국어 경고 메시지 포함)
- 가드레일 콜백 3개 메서드 × 2 (`_on_guardrail_filler`, `_on_guardrail_corrected_tts`, `_on_guardrail_event`)
- `handle_twilio_audio()` VAD 경로 ~40줄 × 2
- 에코 게이트 breakthrough 핸들러 ~20줄 × 2

V2V 고유 로직: Echo Gate + Silence Injection + drain queue, User audio → Session A 전달
T2V 고유 로직: per-response instruction override, `handle_user_text()`, Interrupt Guard (`is_generating`)

**분리 방향:**
```
realtime/pipeline/
├── base.py                        # BasePipeline (기존 134줄)
│   + _call_duration_timer()       # 공통 메서드로 승격
│   + _on_guardrail_*()            # 공통 가드레일 콜백
│   + _handle_twilio_audio_vad()   # 공통 VAD 경로
├── voice_to_voice.py              # V2V 고유 (~350줄)
└── text_to_voice.py               # T2V 고유 (~350줄)
```

---

## 3. 코드 중복 분석

### 3.1 파이프라인 중복 (V2V/T2V)

| 중복 코드 | 줄 수 × 횟수 | 위치 |
|-----------|-------------|------|
| `_call_duration_timer()` | 22줄 × 2 | V2V:575, T2V:582 |
| 가드레일 콜백 3개 | ~15줄 × 2 | V2V:540-565, T2V:547-572 |
| `handle_twilio_audio` VAD 경로 | ~40줄 × 2 | V2V/T2V 각각 |
| Echo Gate breakthrough 핸들러 | ~20줄 × 2 | V2V:238, T2V:256 |

**총 중복: ~200줄** — `BasePipeline`로 추출하면 각 파이프라인 약 200줄 감소.

### 3.2 세션 중복 (Session A/B)

| 중복 코드 | 줄 수 × 횟수 | 위치 |
|-----------|-------------|------|
| `_prune_conversation_items()` | ~25줄 × 2 | session_a:367, session_b:355 |
| `_handle_response_done()` 토큰 트래킹 | ~20줄 × 2 | session_a:302, session_b:702 |

세션 A/B는 역할이 다르므로 공통 베이스 추출보다 **유틸 함수**로 분리하는 것이 적합.

### 3.3 한국어 타이머 문자열

`"통화 종료까지 2분 남았습니다."` — **V2V:584 + T2V:591** 에 동일 문자열 하드코딩.
`"최대 통화 시간을 초과하여 자동 종료됩니다."` — **V2V:591 + T2V:598** 에 동일 문자열 하드코딩.

→ `BasePipeline._call_duration_timer()` 통합 시 자동 해소.

---

## 4. 타입 안전성

### 4.1 정량 데이터

| 지표 | 수치 | 평가 |
|------|------|------|
| `# type: ignore` | **2회** | **양호** — `logging_config.py` ContextVar 속성 확장용 |
| `Any` 사용 | **70회** (14개 파일) | 요주의 — 아래 상세 분석 |
| `dict[str, Any]` | **45회** (9개 파일) | `Any` 사용의 핵심 원인 |
| `cast()` | **0회** | **양호** — 무리한 타입 우회 없음 |

### 4.2 `Any` 사용 분석

**핵심 원인: OpenAI Realtime API 이벤트 (`dict[str, Any]`)**

| 파일 | Any 횟수 | 주요 사용처 |
|------|---------|------------|
| `session_b.py` | 12 | OpenAI 이벤트 핸들러 (`event: dict[str, Any]`) |
| `session_a.py` | 11 | OpenAI 이벤트 핸들러 |
| `types.py` | 9 | `collected_data: dict[str, Any]` — 프리폼 JSON (의도적) |
| `session_manager.py` | 7 | WebSocket 메시지 파싱 |
| `audio_router.py` | 5 | Pipeline 프록시 (`__getattr__`) |
| `tools/executor.py` | 5 | Function Calling 인자 |
| 기타 8개 파일 | 21 | 소수 사용 |

**평가:**
- 70회 중 ~45회가 `dict[str, Any]` — OpenAI Realtime API가 공식 Python SDK에 타입을 제공하지 않으므로 현실적 선택
- `collected_data: dict[str, Any]`는 시나리오별 프리폼 JSON이므로 의도적
- Pydantic 모델 사용은 전반적으로 양호 (`CallStartRequest`, `CallEndRequest`, `ActiveCall` 등)

### 4.3 불투명 유니온 타입

```python
# session_b.py:225
self._pending_output: list[tuple[str, Any]] = []
```

실제 사용: `("audio", bytes)`, `("caption", tuple[str, str])`, `("original_caption", tuple[str, str])`.
`Any` 대신 `tuple[Literal["audio"], bytes] | tuple[Literal["caption", "original_caption"], tuple[str, str]]` 유니온으로 타입을 좁힐 수 있음.

### 4.4 개선 제안

1. **OpenAI 이벤트 TypedDict 도입** — 자주 사용되는 이벤트 구조에 대해 `ResponseDoneEvent`, `TranscriptDoneEvent` 등 TypedDict 정의
2. **`_pending_output` 타입 좁히기** — 태그드 유니온으로 리팩토링
3. 현재 `cast()` 0회, `type: ignore` 2회는 매우 양호 — 유지

---

## 5. 에러 핸들링 & 로깅

### 5.1 예외 처리 개요

| 지표 | 수치 |
|------|------|
| `except Exception:` (로깅 포함) | **43회** (17개 파일) |
| `except: pass` (에러 삼킴) | **7회** (아래 목록) |
| `except asyncio.CancelledError: pass` | **14회** — 비동기 정리 패턴, **정상** |
| `logger.*` 호출 총 수 | **212회** (28개 파일) |

### 5.2 에러 삼킴 (`except: pass`) 상세

| 위치 | 코드 | 위험도 |
|------|------|--------|
| `media_stream.py:84` | `except Exception: pass` (WS close) | **낮음** — WS 종료 시 실패 무시 |
| `stream.py:59` | `except Exception: pass` (WS send) | **낮음** — 상태 알림 실패 무시 |
| `local_vad.py:167` | `except Exception: pass` (reset) | **중간** — VAD 리셋 실패 시 상태 불일치 가능 |
| `local_vad.py:256` | `except Exception: pass` (cleanup) | **낮음** — 종료 정리 |
| `local_vad.py:281` | `except Exception: pass` (cleanup) | **낮음** — 종료 정리 |
| `call_manager.py:168` | `except (CancelledError, Exception): pass` (task cancel) | **중간** — 태스크 정리 실패 숨김 |
| `call_manager.py:190` | `except Exception: pass` (persist) | **높음** — DB 영속화 실패 조용히 삼킴 |
| `session_manager.py:292` | `except Exception: pass` (WS close) | **낮음** — 이미 닫힌 연결 |

`call_manager.py:190`은 통화 데이터 DB 저장 실패를 삼키므로 **최소한 logger.warning 추가 필요**.

### 5.3 구조화 로깅

**양호한 점:**
- `ContextVar` 기반 `call_id`, `call_mode` 자동 주입 (`logging_config.py`)
- Cloud Run JSON 포맷터 + 컬러 콘솔 포맷터 분리
- 로그 레벨 일관: `logger.info` (정상), `logger.warning` (경고), `logger.error` (에러), `logger.exception` (트레이스백)
- 로그 커버리지: 28개 파일에서 212개 로그문 — **양호**

### 5.4 한국어 하드코딩 사용자 문자열

| 위치 | 문자열 | 타입 |
|------|--------|------|
| `stream.py:56` | `"전화 연결 중..."` | WS 메시지 (사용자 표시) |
| `voice_to_voice.py:584` | `"통화 종료까지 2분 남았습니다."` | WS 메시지 (사용자 표시) |
| `voice_to_voice.py:591` | `"최대 통화 시간을 초과하여 자동 종료됩니다."` | WS 메시지 (사용자 표시) |
| `text_to_voice.py:591` | `"통화 종료까지 2분 남았습니다."` | WS 메시지 (사용자 표시, 중복) |
| `text_to_voice.py:598` | `"최대 통화 시간을 초과하여 자동 종료됩니다."` | WS 메시지 (사용자 표시, 중복) |
| `templates.py:41` | `"잠시만 기다려주세요, 메시지를 작성 중입니다."` | AI 프롬프트 내 한국어 (의도적) |
| `templates.py:217` | `"잠시만 기다려 주세요."` | AI 프롬프트 내 한국어 (의도적) |
| `dictionary.py:80` | `"잠시만요."` | 가드레일 필러 (의도적) |

사용자에게 직접 표시되는 WS 메시지 5곳은 **소스 언어에 따라 영어/한국어 분기 필요**. AI 프롬프트 내 한국어 문자열은 타겟 언어가 한국어일 때만 사용되므로 의도적.

---

## 6. 보안 & API 라우트

### 6.1 라우트 인벤토리

| 라우트 | 줄 수 | 메서드 | 인증 |
|--------|-------|--------|------|
| `POST /relay/calls/start` | 164 | POST | **없음** |
| `POST /relay/calls/{id}/end` | 18 | POST | **없음** |
| `WS /relay/calls/{id}/stream` | 102 | WS | **없음** |
| `POST /twilio/webhook/{id}` | 21 | POST | **없음** |
| `POST /twilio/status-callback/{id}` | 28 | POST | **없음** |
| `WS /twilio/media-stream/{id}` | 78 | WS | **없음** |
| `GET /health` | 20 | GET | 불필요 |

### 6.2 Critical: 인증 부재

**`POST /relay/calls/start`** — 인증 없이 호출 가능:
- `CallStartRequest`에 `phone_number` 필드 포함
- 공격자가 임의 전화번호로 Twilio 발신 가능 → **과금 공격**
- 현재 방어: `RateLimitMiddleware` (60/분) — IP 기반이므로 스푸핑 가능

**제안:** Web App의 `/api/calls/[id]/start`에서만 호출되므로, 공유 API 키 또는 Supabase JWT 검증으로 보호.

### 6.3 Critical: Twilio Webhook 시그니처 미검증

**`POST /twilio/webhook/{call_id}`** — Twilio 시그니처 검증 없음:
- 공격자가 악의적 TwiML webhook 요청을 위조 가능
- Twilio는 `X-Twilio-Signature` 헤더로 HMAC 검증 제공
- `twilio.request_validator.RequestValidator`로 간단히 구현 가능

**`POST /twilio/status-callback/{call_id}`** — 동일 문제:
- 통화 상태를 위조하여 `cleanup_call()` 트리거 가능

### 6.4 Rate Limiter 한계

```python
# middleware/rate_limit.py
class RateLimitMiddleware(BaseHTTPMiddleware):
    self._requests: dict[str, list[float]] = defaultdict(list)
```

**문제점:**

1. **메모리 무한 증가**: `_requests` dict가 시간이 지나도 키를 삭제하지 않음. 빈 리스트인 경우 `pop` 하지만, 활성 IP는 계속 누적
2. **IP 스푸핑 미방지**: `request.client.host`는 프록시 뒤에서 항상 동일 IP. Cloud Run 환경에서 `X-Forwarded-For` 처리 없음
3. **프로세스 로컬**: uvicorn 워커 여러 개 시 각자 독립 카운터 → 분산 환경에서 무효

### 6.5 세션 누수 가능성

```python
# routes/calls.py:105
call_manager.register_session(req.call_id, dual_session)  # 먼저 등록

# routes/calls.py:117-120 — Twilio 실패 시
await call_manager.cleanup_call(req.call_id, reason="twilio_failed")
```

Twilio 발신 실패 시 `cleanup_call()`이 호출되지만, `register_call()`은 아직 실행 전이므로 `get_call()`이 `None`을 반환. `cleanup_call()` 내부에서 `call` 없이도 세션은 정리되는지 확인 필요.

### 6.6 제안

| 우선순위 | 항목 | 방법 |
|---------|------|------|
| **P0** | `/relay/calls/start` 인증 | API 키 또는 JWT 검증 미들웨어 |
| **P0** | Twilio webhook 시그니처 | `twilio.request_validator.RequestValidator` |
| **P1** | Rate limiter 개선 | Redis 기반 또는 `X-Forwarded-For` 처리 |
| **P2** | WebSocket 인증 | 연결 시 토큰 검증 |

---

## 7. 테스트 현황

### 7.1 테스트 개요

| 지표 | 수치 |
|------|------|
| pytest 수집 | **265개** |
| `def test_` 함수 | **355개** (pytest parametrize 포함) |
| `class Test*` | **79개** |
| 테스트 파일 | **36개** |
| 테스트 코드 줄 수 | **7,316줄** |
| 소스:테스트 비율 | **1:0.9** |

### 7.2 커버리지 현황 — 잘 테스트된 영역

| 테스트 파일 | 줄 수 | 대상 | 평가 |
|------------|-------|------|------|
| `test_voice_to_voice_pipeline.py` | 908 | V2V 파이프라인 (11개 클래스, 48개 함수) | **우수** |
| `test_chat_translator.py` | 877 | Chat API 번역 (10개 클래스, 40개 함수) | **우수** |
| `test_echo_gate_manager.py` | 562 | EchoGateManager (10개 클래스, 37개 함수) | **우수** |
| `test_text_to_voice_pipeline.py` | 531 | T2V 파이프라인 (9개 클래스, 29개 함수) | **우수** |
| `test_echo_gate.py` | 458 | Echo Gate 유닛 (4개 클래스, 28개 함수) | **우수** |
| `test_session_b_metrics.py` | 455 | Session B 메트릭 (11개 클래스, 30개 함수) | **우수** |
| `test_local_vad.py` | 328 | Local VAD (3개 클래스, 18개 함수) | **양호** |
| `test_full_agent_pipeline.py` | 271 | Agent 파이프라인 (4개 클래스, 15개 함수) | **양호** |
| `test_audio_router.py` | 231 | AudioRouter (2개 클래스, 12개 함수) | **양호** |
| `test_call_manager.py` | 210 | CallManager (4개 클래스, 15개 함수) | **양호** |

**테스트 패턴:** `unittest.mock` + `AsyncMock`, strict `asyncio` mode, 클래스 기반 그룹화.

### 7.3 미테스트 영역

| 모듈 | 줄 수 | 테스트 | 비고 |
|------|-------|--------|------|
| `routes/calls.py` | 164 | **없음** | API 엔드포인트 — 통합 테스트 필요 |
| `routes/stream.py` | 102 | **없음** | WebSocket 핸들러 — 통합 테스트 필요 |
| `routes/twilio_webhook.py` | 150 | **없음** | Twilio webhook — 통합 테스트 필요 |
| `recovery.py` | 588 | **없음** | 연결 복구 — 대형 파일, 테스트 우선순위 높음 |
| `interrupt_handler.py` | 113 | **없음** | Interrupt 로직 |
| `first_message.py` | 95 | **없음** | 첫 메시지 타이밍 |
| `session_a.py` | 494 | **부분** | 메트릭만 테스트, 이벤트 핸들러 미테스트 |
| `session_b.py` | 1,089 | **부분** | 메트릭만 테스트, 코어 로직 미테스트 |
| `db/supabase_client.py` | 113 | **없음** | DB 영속화 |
| `middleware/rate_limit.py` | 31 | **있음** (96줄) | ✅ |

**미테스트 줄 합계: ~1,706줄** (전체의 ~21%)

### 7.4 테스트 추가 우선순위

**P0 — 높은 위험, 테스트 부재:**

| 대상 | 이유 |
|------|------|
| `recovery.py` (588줄) | 연결 복구 + Degraded Mode — 실패 시 통화 중단 |
| `routes/calls.py` (164줄) | 통화 시작/종료 API — 비즈니스 크리티컬 |
| `routes/twilio_webhook.py` (150줄) | Twilio 연동 — 외부 의존성 경계 |

**P1 — 기존 테스트 확장:**

| 대상 | 이유 |
|------|------|
| `session_a.py` 이벤트 핸들러 | 현재 메트릭만 테스트 |
| `session_b.py` 코어 로직 | STT 필터 + VAD + 번역 분기 |
| `interrupt_handler.py` | Interrupt 우선순위 보장 |

---

## 8. 리팩토링 우선순위 로드맵

### P0 — 즉시 (보안 + 높은 영향)

| 항목 | 예상 효과 | 영향 범위 |
|------|----------|----------|
| **`/relay/calls/start` 인증 추가** | 무단 Twilio 발신 차단 | `routes/calls.py` + 미들웨어 |
| **Twilio webhook 시그니처 검증** | 스푸핑 방지 | `routes/twilio_webhook.py` |
| **파이프라인 중복 제거** | ~200줄 제거, `BasePipeline` 강화 | V2V, T2V, base.py |

### P1 — 단기 (구조 개선)

| 항목 | 예상 효과 | 영향 범위 |
|------|----------|----------|
| **`session_b.py` 분리** | 1,089줄 → 4개 파일 (~400줄 오케스트레이터) | `realtime/sessions/` |
| **OpenAI 이벤트 TypedDict 도입** | `dict[str, Any]` 45회 중 ~30회 타입화 | `realtime/sessions/`, `types.py` |
| **라우트 테스트 추가** | 미테스트 영역 21% → ~10% | `tests/` |
| **`recovery.py` 테스트 추가** | 588줄 대형 파일 테스트 부재 해소 | `tests/` |
| **에러 삼킴 수정** | `call_manager.py:190` 등 최소 로깅 추가 | 7개 위치 |

### P2 — 중기 (품질 + 유지보수)

| 항목 | 예상 효과 | 영향 범위 |
|------|----------|----------|
| **한국어 WS 메시지 i18n** | 5곳 사용자 표시 문자열 다국어 지원 | V2V, T2V, stream.py |
| **Rate Limiter 개선** | IP 스푸핑 방지 + Cloud Run 호환 | `middleware/rate_limit.py` |
| **`_pending_output` 타입 좁히기** | 불투명 `Any` → 태그드 유니온 | `session_b.py` |
| **`config.py` 정리** | `echo_post_settling_s` 등 미사용 설정 제거 | `config.py` |

---

## 부록: 정량 데이터 요약

| 지표 | 수치 |
|------|------|
| 소스 파일 | 38개 |
| 소스 줄 수 | 8,166줄 |
| 테스트 파일 | 36개 |
| 테스트 줄 수 | 7,316줄 |
| pytest 수집 | 265개 |
| `# type: ignore` | 2회 |
| `Any` 사용 | 70회 (14개 파일) |
| `dict[str, Any]` | 45회 (9개 파일) |
| `cast()` | 0회 |
| `except Exception:` | 43회 (17개 파일) |
| `except: pass` (에러 삼킴) | 7회 |
| `asyncio.CancelledError: pass` | 14회 (정상) |
| `logger.*` 호출 | 212회 (28개 파일) |
| API 라우트 인증 | 0/6개 |
| 미테스트 줄 수 | ~1,706줄 (21%) |
| 한국어 하드코딩 (사용자 표시) | 5곳 |
| 파이프라인 코드 중복 | ~200줄 |
