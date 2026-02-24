<div align="center">

<img src="docs/assets/wigvo_logo.png" alt="WIGVO" width="480" />

<br />
<br />

**AI 실시간 전화 통역 & 중계 플랫폼**

실제 전화 통화에서 양방향 실시간 음성 번역.
상대방은 앱 설치 없이 그냥 전화를 받으면 됩니다.

<br />

[![Live Demo](https://img.shields.io/badge/Live_Demo-wigvo.run-0F172A?style=for-the-badge&logo=google-cloud&logoColor=white)](https://wigvo.run)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](#기술-스택)
[![Next.js](https://img.shields.io/badge/Next.js-16-000000?style=for-the-badge&logo=nextdotjs&logoColor=white)](#기술-스택)
[![Tests](https://img.shields.io/badge/Tests-150+_passing-22C55E?style=for-the-badge&logo=pytest&logoColor=white)](#테스트)

<br />

[English](README.md)

</div>

---

## WIGVO란?

WIGVO는 **실제 전화 통화**에서 언어 장벽을 없애는 플랫폼입니다. 채팅도, 텍스트 번역도 아닌 — 양쪽이 각자 모국어로 자연스럽게 대화하는 **실시간 음성 통화**입니다.

```
사용자:        "I'd like to make a reservation for tonight"
                    ↓ OpenAI Realtime API (< 500ms)
수신자가 듣는 말:  "오늘 저녁 예약하고 싶은데요"  ← Twilio 전화로 전달
                    ↓
수신자:           "네, 몇 시에 오실 건가요?"
                    ↓ OpenAI Realtime API (< 500ms)
사용자가 듣는 말:  "Yes, what time would you like to come?"
```

수신자는 그냥 평범한 전화를 받는 것뿐입니다. 앱도 없고, 설정도 없고, AI가 관여하는지조차 모릅니다.

---

## 해결하려는 문제

매년 **한국 체류 외국인 200만 명 이상**이 단순한 일에서 막힙니다: 전화 한 통.

병원 예약. 배달 주문. 식당 예약. 관공서 문의. 이 모든 게 한국어 전화 통화를 요구합니다 — 그런데 기존 번역 앱은 **일방향 텍스트**만 처리하지, **실제 전화선 위의 실시간 양방향 음성**은 처리하지 못합니다.

| 대상 | 문제 | 규모 |
|------|------|------|
| 재한 외국인 | 한국어 전화 불가 | 220만 명 (2024) |
| 재외 한국인 | 현지어 전화 어려움 | 280만 명 |
| 언어/청각 장애인 | 음성 통화 접근성 부재 | 등록 39만 명 |
| 콜포비아 (Gen-Z) | 전화 자체를 회피 | 추정 ~400만 명 |

**양방향 실시간 음성 번역 + 전화 연결을 하나의 플랫폼에서 해결하는 제품은 아직 없습니다.**

---

## 사용 흐름

### 사용자 (Web App)

```
┌─────────────────────────────────────────────────────────┐
│  1. AI와 채팅          "식당 예약하고 싶어요"               │
│     ↓                                                    │
│  2. 정보 수집          날짜, 시간, 인원, 요청사항           │
│     ↓                                                    │
│  3. 장소 검색          네이버 장소 검색 → 전화번호 확인      │
│     ↓                                                    │
│  4. 원클릭 통화        Relay Server → Twilio 발신          │
│     ↓                                                    │
│  5. 실시간 모니터링     자막 + 통화 상태 표시                │
└─────────────────────────────────────────────────────────┘
```

### 지원 모드 & 파이프라인 아키텍처

각 통신 모드는 독립적인 **파이프라인** (Strategy 패턴)으로 처리되어, 모드별 독립 개발과 테스트가 가능합니다:

| 모드 | 파이프라인 | 입력 | 출력 | 대상 |
|------|----------|------|------|------|
| **Voice → Voice** | `VoiceToVoicePipeline` | 모국어 음성 | 번역된 음성 + 자막 | 일반 사용자 |
| **Voice → Text** | `VoiceToVoicePipeline` (audio 억제) | 음성 | 실시간 자막 | 청각 장애 |
| **Text → Voice** | `TextToVoicePipeline` | 텍스트 입력 | AI가 대신 말해줌 | 언어 장애, 콜포비아 |
| **Agent Mode** | `FullAgentPipeline` | 정보만 제공 | AI가 전화 전체를 자율 진행 | 모든 사용자 |

```
AudioRouter (얇은 위임자)
    │
    ├── VoiceToVoicePipeline  ← EchoDetector + 전체 오디오 경로
    ├── TextToVoicePipeline   ← Per-response instruction + 텍스트 전용 Session B
    └── FullAgentPipeline     ← Function Calling + 자율 AI 대화
```

---

## 아키텍처

```
┌──────────────────┐         ┌───────────────────────────────┐         ┌──────────────────┐
│                  │         │                               │         │                  │
│   Next.js Web    │◄──WS──►│       Relay Server            │◄──WS──►│  OpenAI Realtime  │
│   (Chat + Call   │         │       (FastAPI)               │         │  API (GPT-4o)    │
│    Monitor)      │         │                               │         │                  │
│                  │         │  ┌───────────┐ ┌───────────┐  │         └──────────────────┘
└──────────────────┘         │  │ Session A │ │ Session B │  │
                             │  │ User→수신자│ │ 수신자→User│  │         ┌──────────────────┐
┌──────────────────┐         │  └───────────┘ └───────────┘  │◄──WS──►│  Twilio Media    │
│                  │         │                               │         │  Streams         │
│  React Native    │◄──WS──►│  ┌───────────┐ ┌───────────┐  │         │  (전화 브릿지)    │
│  Mobile App      │         │  │ Echo Gate │ │ Guardrail │  │         │                  │
│  (VAD + Audio)   │         │  └───────────┘ └───────────┘  │         └──────────────────┘
│                  │         │                               │
└──────────────────┘         └───────────────┬───────────────┘
                                             │
                                    ┌────────▼────────┐
                                    │    Supabase     │
                                    │  PostgreSQL +   │
                                    │  Auth + RLS     │
                                    └─────────────────┘
```

### 왜 이중 세션인가?

단일 번역 세션은 양방향 대화를 처리할 수 없습니다 — 번역 방향이 혼동됩니다. WIGVO는 **두 개의 OpenAI Realtime 세션을 동시에** 운영합니다:

- **Session A** (User → 수신자): 사용자 발화를 수신자 언어로 번역, Twilio로 출력
- **Session B** (수신자 → User): Twilio에서 수신자 발화를 캡처, 사용자 언어로 번역

이것이 실시간 양방향 전화 번역을 가능하게 하는 핵심 아키텍처 결정입니다.

---

## 핵심 기술

### 에코 방지 — 다중 레이어 시스템

Session A가 번역된 음성을 Twilio로 보내면, 그 음성이 Session B 마이크로 에코됩니다. 대응 없이는 무한 번역 루프가 발생합니다.

**Layer 1: 무음 주입 + 동적 에너지 임계값 (기본)**

TTS 재생 중 ("에코 윈도우")에는 수신되는 Twilio 오디오를 무음 프레임으로 대체하여, 서버 VAD가 자연스럽게 발화 종료를 감지합니다. 동적 에너지 임계값으로 에코와 실제 발화를 구분합니다:

```
에코 윈도우 활성 (TTS 재생 중)
       │
       ▼
  수신 오디오 RMS
       │
       ├── < 400 RMS  → 에코 (~100-400 RMS) → 무음으로 대체
       └── > 400 RMS  → 실제 발화 (~500-2000+ RMS) → 통과
```

- PSTN 에코는 일반적으로 100-400 RMS, 실제 발화는 500-2000+ RMS
- 무음 프레임으로 서버 VAD가 강제 절단 없이 자연스럽게 종료
- 에코 윈도우 밖에서는 낮은 에너지 게이트 (150 RMS)로 PSTN 배경 잡음 필터링

**Layer 2: Echo Gate v2 (출력측 게이팅)**

```
                      ┌─────── TTS 재생 중 ────────┐
                      │                            │
입력 (수신자 음성):    │  항상 활성                  │  ← 실제 발화를 놓치지 않음
출력 (사용자에게):     │  억제 → 큐에 저장           │  ← 에코 전달 차단
                      │                            │
                      └───── 쿨다운 (300ms) ────────┘
                                    │
                              큐에 쌓인 출력 배출
```

- 입력은 **차단하지 않음** — 에코 억제 중에도 수신자 발화 감지는 항상 활성
- 출력은 TTS 재생 중 **큐에 저장**, 쿨다운 후 배출
- 수신자 발화 시 게이트를 **즉시 해제** (우선순위 인터럽트)

**레거시: 오디오 핑거프린트 에코 감지기 (실험적)**

Pearson 상관계수 기반 청크별 에코 감지기가 코드베이스에 존재하지만 (`echo_detector.py`), 현재 비활성 상태입니다 (`ECHO_DETECTOR_ENABLED=False`). PSTN 오디오에서는 무음 주입 방식이 더 안정적임이 확인되었습니다.

### 가드레일 시스템 — 번역 품질 검증

실시간 번역은 속도와 품질의 균형이 필요합니다. 3단계 시스템으로 95%+ 케이스에서 **지연 시간 제로**:

| 레벨 | 트리거 | 동작 | 추가 지연 |
|------|--------|------|----------|
| **L1** | 정상 번역 | 통과 | **0ms** |
| **L2** | 반말 감지 | TTS 먼저, 백그라운드 교정 | **0ms** |
| **L3** | 욕설/유해 콘텐츠 | 차단 + 필러 음성 + GPT-4o-mini 교정 | **~800ms** |

### 세션 복구 — 무중단 통화

OpenAI Realtime 세션이 끊어질 수 있습니다. 통화 중 복구가 핵심입니다.

```
Normal ──> Heartbeat miss ──> Reconnect (지수 백오프)
                                    │
                              ┌─────┴─────┐
                              │           │
                         성공        실패 (10s)
                              │           │
                    링 버퍼        Degraded 모드
                    catch-up      (Whisper 일괄
                    (미전송 오디오)  번역 전환)
```

- **링 버퍼**: 30초 순환 버퍼가 미전송 오디오 보존
- **Catch-up**: 재연결 시 미전송 오디오를 Whisper로 일괄 전사 후 재주입
- **Degraded 모드**: 10초 복구 실패 시 Whisper STT + GPT-4o-mini 번역으로 전환

### 음성 활동 감지 — 다단계 VAD

**클라이언트 VAD (모바일 앱)**

모바일 앱에서 **음성 활동 감지를 로컬로 수행**하여 음성 프레임만 서버로 전송:

- RMS 에너지 기반 감지 + 구성 가능한 임계값
- 상태 머신: `SILENT -> SPEAKING -> COMMITTED`
- 발화 시작 손실 방지용 300ms 프리스피치 링 버퍼
- OpenAI로 전송되는 오디오 데이터를 ~40% 감소 → 비용 직감

**서버 로컬 VAD (Silero + RMS)**

Session B는 수신 Twilio 오디오에 대해 로컬 VAD를 실행하여, 서버 VAD 단독 사용보다 정확하고 저지연의 발화 감지를 수행합니다:

- Silero 신경망 VAD 모델로 음성 확률 점수 산출
- RMS 에너지 게이트로 VAD 처리 전 PSTN 배경 잡음 필터링
- OpenAI 서버 VAD에만 의존할 때보다 빠른 발화 종료 감지

**오디오 에너지 게이트 (PSTN 잡음 필터)**

PSTN 전화선은 VAD를 혼란시키는 지속적 배경 잡음 (50-200 RMS)을 수반합니다. 에너지 게이트가 임계값 미만의 오디오를 무음으로 대체합니다:

- 구성 가능한 임계값 (`AUDIO_ENERGY_MIN_RMS=150`)으로 회선 잡음 필터링
- 에코 윈도우 중에는 더 높은 임계값 (`ECHO_ENERGY_THRESHOLD_RMS=400`)
- 실제 발화 (500-2000+ RMS)는 항상 통과

### 인터럽트 우선순위 — 자연스러운 대화 흐름

전화 통화에는 자연스러운 순서 교대가 있습니다. 우선순위 시스템으로 수신자를 기다리게 하지 않습니다:

```
Priority 1 (최고):  수신자 발화  -> AI 출력 즉시 취소
Priority 2:        사용자 발화  -> AI 취소, 번역 대기열에 추가
Priority 3 (최저):  AI 생성     -> 누구든 인터럽트 가능
```

최대 발화 시간 안전장치 (8초)로 VAD가 발화 종료를 감지하지 못할 경우 오디오 버퍼를 강제 커밋하여 무한 녹음을 방지합니다.

---

## 기술 스택

| 레이어 | 기술 | 선택 이유 |
|--------|------|----------|
| **Relay Server** | Python 3.12+, FastAPI, uvicorn | 비동기 WebSocket 처리, 저지연 |
| **Web App** | Next.js 16, React 19, shadcn/ui, Zustand | SSR, 실시간 UI 업데이트 |
| **Mobile App** | React Native, Expo SDK 54 | 크로스 플랫폼, expo-av 오디오 |
| **실시간 AI** | OpenAI Realtime API (GPT-4o) | 1초 이내 STT + 번역 + TTS |
| **채팅 AI** | GPT-4o-mini | 비용 효율적 데이터 수집 |
| **전화** | Twilio (REST + Media Streams) | 안정적 전화 인프라 |
| **데이터베이스** | Supabase (PostgreSQL + Auth + RLS) | 실시간 구독, 행 수준 보안 |
| **장소 검색** | 네이버 장소 검색 API | 한국 업종 디렉토리 |
| **배포** | Docker, Google Cloud Run | 자동 스케일링, 제로 콜드 스타트 |
| **패키지 관리** | uv (Python), npm (Web/Mobile) | 빠르고 안정적 의존성 관리 |

---

## 프로젝트 구조

```
apps/
├── relay-server/                    # Python FastAPI — 실시간 번역 엔진
│   ├── src/
│   │   ├── main.py                  # FastAPI 진입점 + lifespan
│   │   ├── call_manager.py          # 통화 라이프사이클 싱글톤 (등록/정리/종료)
│   │   ├── config.py                # pydantic-settings 환경변수 설정
│   │   ├── types.py                 # ActiveCall, CostTokens, WsMessage 등
│   │   ├── logging_config.py        # 구조화된 로깅 설정
│   │   ├── middleware/              # HTTP 미들웨어
│   │   │   └── rate_limit.py        # 속도 제한
│   │   ├── routes/                  # HTTP + WebSocket 엔드포인트
│   │   │   ├── calls.py             # POST /calls/start, /calls/{id}/end
│   │   │   ├── stream.py            # WS /calls/{id}/stream (앱 ↔ 릴레이)
│   │   │   └── twilio_webhook.py    # Twilio 상태 콜백
│   │   ├── realtime/                # OpenAI Realtime 세션 관리
│   │   │   ├── pipeline/            # Strategy 패턴 — 모드별 파이프라인
│   │   │   │   ├── base.py          # BasePipeline ABC
│   │   │   │   ├── voice_to_voice.py # V2V (Echo Gate + Silence Injection)
│   │   │   │   ├── text_to_voice.py  # T2V (per-response instruction, 텍스트 전용 B)
│   │   │   │   └── full_agent.py     # Agent (function calling, 자율)
│   │   │   ├── audio_router.py      # 얇은 위임자 → 파이프라인 선택
│   │   │   ├── echo_detector.py     # Pearson 상관계수 에코 감지
│   │   │   ├── audio_utils.py       # 공유 mu-law 오디오 유틸리티
│   │   │   ├── session_manager.py   # 이중 세션 오케스트레이터
│   │   │   ├── session_a.py         # User → 수신자 번역
│   │   │   ├── session_b.py         # 수신자 → User 번역
│   │   │   ├── context_manager.py   # 6턴 슬라이딩 컨텍스트 윈도우
│   │   │   ├── recovery.py          # 세션 장애 복구 + degraded 모드
│   │   │   └── ring_buffer.py       # 30초 순환 오디오 버퍼
│   │   ├── guardrail/               # 3단계 번역 품질 시스템
│   │   ├── tools/                   # Agent Mode function calling
│   │   ├── prompt/                  # 시스템 프롬프트 템플릿 + 생성기
│   │   └── db/                      # Supabase 클라이언트
│   ├── tests/                       # 150+ pytest 단위 테스트
│   │   ├── component/              # 모듈 벤치마크 (비용 추적, 링 버퍼 성능)
│   │   ├── integration/            # 서버 필요 테스트 (API, WebSocket)
│   │   ├── e2e/                    # 양방향 통화 E2E 테스트 (Twilio + OpenAI 필요)
│   │   └── run.py                  # 테스트 러너 (--suite, --test 옵션)
│
├── web/                             # Next.js 16 — Chat Agent + 통화 모니터
│   ├── app/
│   │   ├── page.tsx                 # 대시보드 (채팅 + 통화 인터페이스)
│   │   ├── api/                     # 7개 API 라우트 (chat, calls, conversations)
│   │   ├── calling/[id]/            # 실시간 통화 모니터링
│   │   └── result/[id]/             # 통화 결과 표시
│   ├── lib/
│   │   ├── services/                # 채팅 파이프라인 (chat-service, place-matcher, data-extractor)
│   │   ├── supabase/                # SSR 클라이언트 + 헬퍼
│   │   └── scenarios/               # 시나리오 프롬프트 (식당, 병원, 미용실 등)
│   ├── hooks/                       # useChat, useCallPolling, useRelayWebSocket, useDashboard
│   ├── components/                  # chat/, call/, dashboard/, ui/ (shadcn)
│   └── shared/types.ts              # 정규 타입 정의 (Call, Conversation, CallRow)
│
└── mobile/                          # React Native (Expo) — VAD + 오디오 클라이언트
    ├── app/                         # Expo Router (인증 + 메인 화면)
    ├── hooks/                       # useRealtimeCall, useClientVad, useAudioRecorder
    ├── components/call/             # RealtimeCallView, LiveCaptionPanel, VadIndicator
    └── lib/vad/                     # VAD 코어 (프로세서, 링 버퍼, 설정)
```

---

## API 레퍼런스

### Relay Server

| 엔드포인트 | 타입 | 설명 |
|----------|------|------|
| `POST /relay/calls/start` | HTTP | Twilio로 발신 통화 시작 |
| `POST /relay/calls/{id}/end` | HTTP | 활성 통화 종료 |
| `WS /relay/calls/{id}/stream` | WebSocket | 양방향 오디오/텍스트 스트림 |
| `POST /twilio/incoming` | HTTP | Twilio 통화 상태 웹훅 |
| `WS /twilio/media-stream` | WebSocket | Twilio Media Stream 오디오 브릿지 |
| `GET /health` | HTTP | 상태 확인 |

### Web App

| 엔드포인트 | 메서드 | 설명 |
|----------|--------|------|
| `/api/chat` | POST | AI 대화 (GPT-4o-mini, 시나리오 기반) |
| `/api/conversations` | GET/POST | 대화 세션 목록 또는 생성 |
| `/api/conversations/[id]` | GET | 대화 상세 (메시지 포함) |
| `/api/calls` | GET/POST | 통화 기록 목록 또는 생성 |
| `/api/calls/[id]` | GET | 통화 상세 (상태, 결과, 요약) |
| `/api/calls/[id]/start` | POST | Relay Server를 통한 통화 시작 |

---

## 데이터베이스 스키마

| 테이블 | 주요 컬럼 | 용도 |
|-------|----------|------|
| `conversations` | scenario, status, collected_data (JSONB) | 수집된 정보가 포함된 채팅 세션 |
| `messages` | role, content, metadata (JSONB) | 사용자 + AI 메시지 |
| `calls` | status, result, call_sid, duration_s, total_tokens | 통화 라이프사이클 추적 |
| `conversation_entities` | entity_type, value, confidence | 구조화된 데이터 추출 |
| `place_search_cache` | query, results (JSONB), expires_at | 네이버 API 응답 캐시 |

모든 테이블에 **Row Level Security** 적용 — 사용자는 자신의 데이터만 접근 가능합니다.

---

## 시작하기

### 사전 요구사항

- Python 3.12+ / Node.js 18+
- [uv](https://docs.astral.sh/uv/) (Python 패키지 매니저)
- [ngrok](https://ngrok.com/) (개발환경 Twilio 웹훅용)
- API 키: OpenAI, Twilio, Supabase, 네이버 (선택)

### 빠른 시작

```bash
# 1. Clone
git clone https://github.com/wigtn/wigvo-v2.git
cd wigvo-v2

# 2. Relay Server
cd apps/relay-server
cp .env.example .env          # API 키 입력
uv sync
uv run uvicorn src.main:app --reload --port 8000

# 3. Web App (새 터미널)
cd apps/web
cp .env.example .env.local    # API 키 입력
npm install
npm run dev

# 4. ngrok (새 터미널)
ngrok http 8000               # URL을 .env의 RELAY_SERVER_URL에 입력
```

### 테스트

```bash
# 단위 테스트 (150+개, 서버 불필요)
cd apps/relay-server
uv run pytest -v

# 컴포넌트 테스트 (링 버퍼 성능, 비용 추적)
uv run python -m tests.run --suite component

# 통합 테스트 (서버 실행 필요)
uv run python -m tests.run --suite integration

# 개별 테스트
uv run python -m tests.run --test cost

# E2E 통화 테스트 (Twilio + OpenAI 키 필요)
uv run python -m tests.run --test call --phone +82... --scenario restaurant --auto
```

### 배포

두 서비스 모두 컨테이너화되어 **Google Cloud Run**에 배포됩니다:

```bash
# Cloud Build로 빌드 & 배포
gcloud builds submit --config=cloudbuild.yaml
```

| 서비스 | Dockerfile | Cloud Run |
|-------|-----------|-----------|
| Relay Server | `apps/relay-server/Dockerfile` | 자동 스케일링, WebSocket 지원 |
| Web App | `apps/web/Dockerfile` | Next.js standalone 출력 |

---

## 시장 기회

| 세그먼트 | TAM (한국) | 지불 의향 |
|---------|-----------|----------|
| 재한 외국인 | 220만 (연 8% 성장) | 높음 — 일상 필수 |
| 재외 한국인 | 280만 | 중간 — 간헐적 사용 |
| 장애인 서비스 | 정부 지원 프로그램 | 기관 계약 |
| 콜포비아 (Gen-Z) | 추정 ~400만 | 구독 모델 |

**경쟁 현황**: Google 번역은 텍스트. 파파고도 텍스트 + 제한적 음성. **실제 전화선 위의 실시간 양방향 음성 번역은 아무도 하지 않습니다.** 가장 가까운 대안도 양쪽 모두 앱 설치를 요구 — WIGVO는 한쪽만 있으면 됩니다.

---

## 라이선스

All rights reserved.

---

<div align="center">

OpenAI Realtime API, Twilio, Supabase, 그리고 수많은 WebSocket 디버깅으로 만들었습니다.

</div>
