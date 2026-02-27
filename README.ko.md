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
[![Tests](https://img.shields.io/badge/Tests-430+_passing-22C55E?style=for-the-badge&logo=pytest&logoColor=white)](#테스트)

<br />

[English](README.md)

</div>

---

## 1. Introduction

WIGVO는 **일반 전화 통화에서 양방향 LLM 기반 음성 번역을 수행하는 서버 사이드 릴레이 시스템**입니다. 발신자는 브라우저에서 말하거나 타이핑하고, 수신자는 일반 전화를 받기만 하면 됩니다. 앱 설치, 통신사 연동, 특수 하드웨어 모두 불필요합니다.

```
사용자:        "I'd like to make a reservation for tonight"
                    ↓ OpenAI Realtime API (< 500ms)
수신자가 듣는 말:  "오늘 저녁 예약하고 싶은데요"  ← Twilio 전화로 전달
                    ↓
수신자:           "네, 몇 시에 오실 건가요?"
                    ↓ OpenAI Realtime API (< 500ms)
사용자가 듣는 말:  "Yes, what time would you like to come?"
```

### 문제 정의

스트리밍 음성 번역 기술은 빠르게 발전하고 있지만, 기존 시스템들은 광대역 오디오와 클라이언트측 음향 에코 제거(AEC)를 전제합니다. **공중전화망(PSTN)**은 여전히 병원, 식당, 관공서의 주요 인바운드 인터페이스이지만, G.711 μ-law 8kHz 코덱, 80–600ms 지연, AEC 부재라는 제약이 있습니다.

| 대상 | 문제 | 규모 |
|------|------|------|
| 재한 외국인 | 한국어 전화 불가 | 220만 명 (2024) |
| 재외 한국인 | 현지어 전화 어려움 | 280만 명 |
| 언어/청각 장애인 | 음성 통화 접근성 부재 | 등록 39만 명 |
| 콜포비아 (Gen-Z) | 전화 자체를 회피 | 추정 ~400만 명 |

### 기여점

1. 전화망 스트리밍 음성 번역에서 **에코 유발 자기 강화 번역 루프** 문제를 형식화
2. 방향 분리, 무음 주입, 에너지 기반 VAD 게이팅을 결합한 **이중 세션 게이팅 아키텍처** 제안
3. 3가지 통신 모드로 릴레이 서버를 배포·평가 — 147건 완료 통화에서 **에코 루프 0건**, **557ms 중앙값 레이턴시**, **분당 $0.28**

---

## 2. Related Work

동시 음성 번역 시스템(SeamlessM4T, Seamless Streaming)은 광대역 오디오를 전제합니다. 풀 듀플렉스 모델(Moshi, Hibiki)은 깨끗한 24kHz 오디오가 필요하며 G.711 코덱이나 전화 에코를 처리하지 못합니다. Google Duplex는 단일 언어, Vapi/Bland.ai는 다국어 번역 미지원. Samsung Galaxy AI와 SKT A.dot은 하드웨어/통신사 인프라 기반. WIGVO는 **소프트웨어만으로** 동일 문제를 해결합니다.

| 시스템 | PSTN | 양방향 | 음성-음성 | 에코 처리 | 접근성 |
|--------|:----:|:----:|:----:|:----:|:----:|
| SeamlessM4T | | ✓ | ✓ | N/A | |
| Moshi / Hibiki | | | ✓ | N/A | |
| Google Duplex | ✓ | | | N/D | |
| Samsung Galaxy AI | ✓ | ✓ | ✓ | 하드웨어 AEC | |
| SKT A.dot | ✓ | ✓ | ✓ | 통신사 제어 | |
| Vapi / Bland.ai | ✓ | | | N/D | |
| FCC TRS | ✓ | ✓ | | 사람 | ✓ |
| **WIGVO** | **✓** | **✓** | **✓** | **✓** | **✓** |

---

## 3. 시스템 아키텍처 및 에코 게이팅

<div align="center">
<img src="docs/paper/figures/figure1_architecture.png" alt="WIGVO 시스템 아키텍처" width="100%" />
<br />
<em>Figure 1: WIGVO 시스템 아키텍처. Session A(빨강)는 사용자 음성을 G.711로 변환하여 Twilio에 전달; Session B(파랑)는 PSTN 오디오를 3단계 필터 파이프라인(Echo Gate → Energy Gate → Silero VAD)을 통해 수신.</em>
</div>

<br />

브라우저 클라이언트가 WebSocket으로 릴레이 서버에 연결하여 16kHz PCM 오디오를 전송합니다. 릴레이는 **두 개의 독립적인 Realtime LLM 세션**과 Twilio 전화 게이트웨이를 관리합니다. `AudioRouter`가 이벤트를 V2V, T2V, Full-Agent 세 가지 파이프라인 중 하나로 배분합니다 (Strategy 패턴).

**이중 Realtime 세션.** Session A는 브라우저 오디오(PCM16)를 받아 번역된 G.711 μ-law를 전화 게이트웨이에 출력합니다. Session B는 PSTN 오디오(G.711 μ-law, 8kHz)를 받아 번역된 오디오/자막을 브라우저에 출력합니다. T2V/Full-Agent 모드에서 Session B는 STT만 수행하고, 번역은 별도 Chat Completion 호출(GPT-4o-mini, `temperature=0`)에 위임하여 생성적 할루시네이션을 방지합니다.

### 3.1 에코 루프 문제

단순한 단일 세션 설계에서는 수신자에게 재생된 TTS가 PSTN을 통해 80–600ms 후 에코되어 다시 인식되고, 또 다른 번역 사이클을 유발합니다 — 수동 개입 전까지 점진적으로 왜곡되는 패러프레이즈가 생성됩니다. **게이팅 없이 테스트 통화 10건 중 8건에서 에코 루프 발생; Echo Gate로 147건 완료 통화에서 0건.**

### 3.2 Echo Gate와 3단계 파이프라인

<div align="center">
<img src="docs/paper/figures/figure2_pipeline.png" alt="PSTN 오디오 처리 파이프라인" width="100%" />
<br />
<em>Figure 2: PSTN 오디오 처리 파이프라인. (A) Twilio 입력과 Session B 사이의 3단계 필터. (B) 시간적 동작: TTS 재생 중 Echo Gate가 오디오를 μ-law 무음으로 대체하고, 안정화 후 Stage 0이 투명해지며 Stage 1–2가 PSTN 잡음을 필터링.</em>
</div>

<br />

**Stage 0: Echo Gate.** Session A가 TTS 스트리밍을 시작하면 *에코 윈도우*가 활성화됩니다. 수신 PSTN 오디오가 μ-law 무음(`0xFF`)으로 대체된 후 Session B에 전달됩니다. 동적 쿨다운(TTS 길이 + 0.3초 에코 왕복 마진)과 post-echo settling 윈도우가 뒤따릅니다.

**Stage 1: RMS Energy Gate.** 에코 윈도우 중 ≥400 RMS 신호만 통과(에코 100–400 RMS는 차단). 에코 윈도우 외에는 150 RMS 임계값으로 회선 잡음 필터링. 3단계 인터럽트 우선순위(수신자 > 사용자 > AI TTS).

**Stage 2: Silero VAD.** 에너지 게이트를 통과한 프레임에 Silero VAD 실행. 8kHz → 16kHz 업샘플링 후 추론. 비대칭 히스테리시스 — 시작 96ms, 종료 480ms — 으로 `speech_stopped` 레이턴시를 15–72초에서 **480ms**로 단축.

### 3.3 가드레일 시스템

3단계 번역 품질 검증으로 95%+ 케이스에서 **추가 지연 0ms**:

| 레벨 | 트리거 | 동작 | 추가 지연 |
|------|--------|------|----------|
| **L1** | 정상 번역 | 통과 | **0 ms** |
| **L2** | 반말 감지 | TTS 먼저, 백그라운드 교정 | **0 ms** |
| **L3** | 욕설/유해 콘텐츠 | 차단 + 필러 음성 + GPT-4o-mini 교정 | **~800 ms** |

### 3.4 세션 복구

OpenAI Realtime 세션 끊김 시 30초 링 버퍼가 미전송 오디오를 보존합니다. 재연결 시 Whisper로 일괄 전사 후 재주입. 10초 복구 실패 시 Degraded 모드(Whisper STT + GPT-4o-mini 번역)로 전환.

---

## 4. Evaluation

**162건의 계측된 PSTN 통화** (총 169건, 2026-02-23 이후) 데이터로 한국어↔영어 번역을 평가.

### 4.1 레이턴시

<div align="center">
<img src="docs/paper/figures/figure3_latency_histogram.png" alt="레이턴시 분포" width="85%" />
<br />
<em>Figure 3: E2E 레이턴시 분포. Session A (사용자→수신자, N=814턴)와 Session B (수신자→사용자, N=744턴).</em>
</div>

<br />

| 구간 | P50 | P95 | Mean | N |
|------|----:|----:|-----:|--:|
| **Session A** (User→수신자) | **557 ms** | 1156 ms | 617 ms | 814턴 |
| **Session B E2E** (수신자→User) | **2868 ms** | 15482 ms | 3997 ms | 744턴 |
| Session B STT만 | 2675 ms | 15547 ms | 3868 ms | 744턴 |
| Session B 번역만 | 104 ms | 1934 ms | 535 ms | 585턴 |
| 첫 메시지 레이턴시 | 1215 ms | 6890 ms | 2081 ms | 162건 |

Session A **557ms 중앙값**은 인터랙티브 통신 범위 내. STT(Whisper)가 Session B E2E의 **87.3%**를 차지. Session B ~2.9초 중앙값은 전문 동시통역 ear-voice span(2–5초)의 하한.

<div align="center">
<img src="docs/paper/figures/figure4_utterance_scatter.png" alt="발화 길이 vs 레이턴시" width="85%" />
<br />
<em>Figure 4: 발화 길이 vs. Session B E2E 레이턴시. Pearson r=0.400 (p < 0.001).</em>
</div>

### 4.2 에코 및 안전성

| 지표 | 전체 | 통화당 |
|------|-----:|-------:|
| 에코 게이트 활성화 | 1128 | 7.0 |
| 에코 게이트 돌파 (수신자 인터럽트) | 358 | 2.2 |
| **에코 유발 번역 루프** | **0** | **0** |
| VAD 오탐 | 286 | 1.8 |
| 할루시네이션 차단 | 109 | 0.7 |
| 가드레일 L2 / L3 | 148 / 0 | — |

### 4.3 비용

| 지표 | 값 |
|------|-----|
| 전체 통화 | 169건 |
| 총 통화 시간 | 241.4분 |
| 총 비용 | $64.71 |
| **분당 비용** | **$0.27** |
| 통화당 비용 | $0.40 |

전문 전화 통역 서비스($1–3/분) 대비 **한 자릿수 저렴**.

---

## 5. Demonstration

<div align="center">
<img src="docs/paper/figures/figure5_screenshot_call.png" alt="WIGVO 통화 인터페이스" width="80%" />
<br />
<em>Figure 5: WIGVO 웹 인터페이스 — 실시간 양방향 자막, 채팅 기록, 통화 컨트롤.</em>
</div>

<br />

세션 흐름: (1) 시나리오 선택 → (2) AI와 채팅으로 전화 의도/번호 제공 → (3) PSTN 통화 발신 + 양방향 번역 → (4) 실시간 자막 + 통화 상태 표시. 통화 중 모드 전환(V2V, T2V, Agent) 가능.

### 통신 모드

| 모드 | 파이프라인 | 입력 | 출력 | 대상 |
|------|----------|------|------|------|
| **Voice → Voice** | `VoiceToVoicePipeline` | 모국어 음성 | 번역된 음성 + 자막 | 일반 사용자 |
| **Text → Voice** | `TextToVoicePipeline` | 텍스트 입력 | AI가 대신 말해줌 | 언어 장애, 콜포비아 |
| **Agent Mode** | `FullAgentPipeline` | 정보만 제공 | AI가 전화를 자율 진행 | 모든 사용자 |

---

## 기술 스택

| 레이어 | 기술 | 선택 이유 |
|--------|------|----------|
| **Relay Server** | Python 3.12+, FastAPI, uvicorn | 비동기 WebSocket 처리, 저지연 |
| **Web App** | Next.js 16, React 19, shadcn/ui, Zustand | SSR, 실시간 UI 업데이트 |
| **Mobile App** | React Native, Expo SDK 54 | 크로스 플랫폼, expo-av 오디오 |
| **실시간 AI** | OpenAI Realtime API (GPT-4o) | 1초 이내 STT + 번역 + TTS |
| **채팅 AI** | GPT-4o-mini | 비용 효율적 데이터 수집 + T2V/Agent 번역 |
| **전화** | Twilio (REST + Media Streams) | 안정적 전화 인프라 |
| **데이터베이스** | Supabase (PostgreSQL + Auth + RLS) | 실시간 구독, 행 수준 보안 |
| **배포** | Docker, Google Cloud Run | 자동 스케일링 |

---

## 프로젝트 구조

```
apps/
├── relay-server/                    # Python FastAPI — 실시간 번역 엔진
│   ├── src/
│   │   ├── main.py                  # FastAPI 진입점
│   │   ├── realtime/                # OpenAI Realtime 세션 관리
│   │   │   ├── pipeline/            # Strategy 패턴 (V2V, T2V, Agent)
│   │   │   ├── chat_translator.py   # Chat API 번역기 (GPT-4o-mini)
│   │   │   ├── audio_router.py      # 파이프라인 위임자
│   │   │   ├── local_vad.py         # Silero VAD + RMS 에너지 게이트
│   │   │   └── sessions/            # 이중 세션 핸들러 (A + B)
│   │   ├── guardrail/               # 3단계 번역 품질 시스템
│   │   └── tools/                   # Agent Mode function calling
│   ├── tests/                       # 430+ pytest 테스트
│
├── web/                             # Next.js 16 — Chat Agent + 통화 모니터
│   ├── app/                         # 대시보드, API 라우트, 통화 페이지
│   ├── lib/                         # 서비스, Supabase, 시나리오
│   └── hooks/                       # useChat, useRelayWebSocket 등
│
└── mobile/                          # React Native (Expo) — VAD + 오디오 클라이언트
    ├── hooks/                       # useRealtimeCall, useClientVad
    └── lib/vad/                     # VAD 코어
```

---

## 시작하기

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
cd apps/relay-server
uv run pytest -v                                  # 단위 테스트 (430+)
uv run python -m tests.run --suite integration    # 통합 테스트
uv run python -m tests.run --test call --phone +82... --scenario restaurant --auto  # E2E
```

---

## 부록: 프로덕션 지표 (2026-02-23 이후)

논문 평가 데이터를 넘어서는 확장 프로덕션 지표. `scripts/eval/`로 Supabase에서 수집.

### 모드별 분포

| 모드 | 통화 수 | 비율 | SA P50 | SB P50 |
|------|-------:|-----:|-------:|-------:|
| **Text-to-Voice** | 116 | 68.6% | 487 ms | 2557 ms |
| **Voice-to-Voice** | 52 | 30.8% | 603 ms | 2476 ms |
| **Agent** | 1 | 0.6% | — | — |

### 발화 길이별 레이턴시

| 문자 수 | N | P50 | P95 | Mean |
|---------|--:|----:|----:|-----:|
| ≤30자 | 565 | 2502 ms | 6511 ms | 3119 ms |
| 31–60자 | 132 | 4713 ms | 15692 ms | 5860 ms |
| 61–100자 | 32 | 7596 ms | 18402 ms | 8922 ms |
| 100+자 | 15 | 11100 ms | 18716 ms | 10185 ms |

*Pearson r = 0.400 (문자 수 vs. SB 레이턴시)*

### 분당 비용 (모드별, 누적)

| 모드 | 통화 수 | 분당 비용 |
|------|-------:|---------:|
| Voice-to-Voice | 67 | $0.3028 |
| Text-to-Voice | 138 | $0.2943 |
| Agent | 3 | $0.2991 |

---

## 시장 기회

| 세그먼트 | TAM (한국) | 지불 의향 |
|---------|-----------|----------|
| 재한 외국인 | 220만 (연 8% 성장) | 높음 — 일상 필수 |
| 재외 한국인 | 280만 | 중간 — 간헐적 사용 |
| 장애인 서비스 | 정부 지원 프로그램 | 기관 계약 |
| 콜포비아 (Gen-Z) | 추정 ~400만 | 구독 모델 |

---

## 라이선스

MIT License. [LICENSE](LICENSE) 참조.

---

<div align="center">

OpenAI Realtime API, Twilio, Supabase, 그리고 수많은 PSTN 오디오 디버깅으로 7일 만에 만들었습니다.

</div>
