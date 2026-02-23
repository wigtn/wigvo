# WIGVO Relay Server

[한국어](#한국어)

Bidirectional real-time translation call server based on OpenAI Realtime API + Twilio Media Streams.

## Quick Start

```bash
uv sync                              # Install dependencies
cp .env.example .env                  # Configure environment variables
uv run uvicorn src.main:app --reload  # Development server
uv run pytest -v                      # Tests (147 tests)
```

## Directory Structure

```
src/
├── main.py                  # FastAPI entrypoint, router registration, lifespan
├── config.py                # Environment variables (pydantic-settings)
├── types.py                 # Shared types/Enum/Pydantic models
├── call_manager.py          # Call lifecycle singleton (central resource management)
├── logging_config.py        # Structured logging infrastructure
│
├── middleware/
│   ├── __init__.py
│   └── rate_limit.py        # Rate limiting middleware
│
├── routes/
│   ├── calls.py             # POST /relay/calls/start, /end — call start/end API
│   ├── stream.py            # WS /relay/calls/{id}/stream — App <-> Relay WebSocket
│   ├── twilio_webhook.py    # POST /twilio/webhook — TwiML, WS /twilio/media-stream
│   └── health.py            # GET /health
│
├── realtime/
│   ├── pipeline/            # Strategy pattern pipelines (independent per-mode processing)
│   │   ├── __init__.py      # Pipeline module documentation + mode mapping
│   │   ├── base.py          # BasePipeline ABC (common interface)
│   │   ├── voice_to_voice.py # VoiceToVoicePipeline (EchoDetector + full audio)
│   │   ├── text_to_voice.py  # TextToVoicePipeline (per-response instruction + text-only B)
│   │   └── full_agent.py     # FullAgentPipeline (Function Calling + autonomous AI)
│   │
│   ├── audio_router.py      # AudioRouter — thin delegator (Pipeline selection + common lifecycle)
│   ├── echo_detector.py     # EchoDetector — Pearson correlation-based echo detection (legacy)
│   ├── local_vad.py         # Local VAD — Silero + RMS server-side voice detection
│   ├── audio_utils.py       # Shared mu-law -> linear PCM conversion + RMS calculation
│   ├── session_manager.py   # RealtimeSession (OpenAI WS wrapper) + DualSessionManager
│   ├── session_a.py         # SessionAHandler — User->recipient translation event handling
│   ├── session_b.py         # SessionBHandler — Recipient->User translation event handling
│   ├── interrupt_handler.py # Turn overlap/interrupt handling (recipient priority)
│   ├── first_message.py     # Recipient answer detection -> AI notice + Exact Utterance
│   ├── context_manager.py   # Conversation context sliding window (6 turns)
│   ├── recovery.py          # Session failure detection -> auto-reconnect + catch-up
│   └── ring_buffer.py       # 30-second circular audio buffer (for Recovery)
│
├── guardrail/
│   ├── checker.py           # GuardrailChecker — Level 1/2/3 classification pipeline
│   ├── filter.py            # Rule-based text filter (regex + keyword)
│   ├── dictionary.py        # Prohibited/correction dictionary (ko/en/ja/zh)
│   └── fallback_llm.py      # GPT-4o-mini text correction (Level 2/3)
│
├── prompt/
│   ├── generator_v3.py      # Dynamic system prompt generation for Session A/B
│   └── templates.py         # Prompt templates + per-language variables
│
├── tools/
│   ├── definitions.py       # Agent Mode Function Calling tool definitions
│   └── executor.py          # Function Call execution + result recording to ActiveCall
│
├── twilio/
│   ├── outbound.py          # Twilio REST API outbound call (sync -> async wrapping)
│   └── media_stream.py      # TwilioMediaStreamHandler (WS parsing/audio transmission)
│
└── db/
    └── supabase_client.py   # Supabase persistence (upsert on call end)

tests/
├── conftest.py              # pytest fixtures
├── helpers.py               # ANSI color utilities (run.py dependency)
├── run.py                   # Custom runner (uv run python -m tests.run)
├── test_*.py                # pytest unit tests (incl. test_local_vad, test_echo_gate)
├── component/               # Module benchmarks (no server required)
│   ├── test_cost_tracking.py
│   └── test_ring_buffer_perf.py
├── integration/             # Requires live server (localhost:8000)
│   ├── test_api.py
│   └── test_websocket.py
└── e2e/                     # Requires live server + Twilio + OpenAI
    ├── call_client.py
    └── scenarios.py
```

## Architecture Overview

### Pipeline Architecture (Strategy Pattern)

`AudioRouter` is a **thin delegator** that selects the appropriate pipeline based on `communication_mode` and only manages the common lifecycle (timer, recovery). The actual audio/text handling is processed independently by each pipeline.

```
AudioRouter._create_pipeline(call)
    │
    ├─ VOICE_TO_VOICE  → VoiceToVoicePipeline(...)
    ├─ VOICE_TO_TEXT    → VoiceToVoicePipeline(..., suppress_b_audio=True)
    ├─ TEXT_TO_VOICE    → TextToVoicePipeline(...)
    └─ FULL_AGENT       → FullAgentPipeline(...)
```

#### BasePipeline Interface

```python
class BasePipeline(ABC):
    async def handle_user_audio(audio_b64: str) -> None      # User audio input
    async def handle_user_audio_commit() -> None              # Client VAD commit
    async def handle_user_text(text: str) -> None             # User text input
    async def handle_twilio_audio(audio_bytes: bytes) -> None # Twilio recipient audio
    async def start() -> None                                 # Start pipeline
    async def stop() -> None                                  # Stop pipeline
```

#### Per-Pipeline Configuration

| Component | VoiceToVoice | VoiceToText (sub-mode) | TextToVoice | FullAgent |
|-----------|-------------|----------------------|-------------|-----------|
| **Pipeline** | VoiceToVoicePipeline | VoiceToVoicePipeline | TextToVoicePipeline | FullAgentPipeline |
| **Session A modalities** | `['text', 'audio']` | `['text', 'audio']` | `['text', 'audio']` | `['text', 'audio']` |
| **Session A turn_detection** | client/server VAD | client/server VAD | `null` (manual) | `null` (manual) |
| **Session B modalities** | `['text', 'audio']` | `['text', 'audio']` | **`['text']`** | **`['text']`** |
| **Session B -> App audio** | Sent | **Suppressed** | N/A (text only) | N/A (text only) |
| **Silence Injection** | **Active** | **Active** | Not needed | Not needed |
| **Echo Gate** | Active | Active | Not needed | Not needed |
| **Energy Gate** | Active | Active | Active | Active |
| **Local VAD** | Active | Active | Active | Active |
| **Interrupt Handler** | Active | Active | Active | Active |
| **Function Calling** | None | None | None | **Active** |
| **Per-response instruction** | None | None | **Active** | **Active** |

### Dual Session Structure

Each call operates with **2 independent OpenAI Realtime sessions**:

```
Session A: User -> Recipient (source -> target translation)
  - Input: pcm16 (from App)
  - Output: g711_ulaw (to Twilio)

Session B: Recipient -> User (target -> source translation)
  - Input: g711_ulaw (from Twilio)
  - Output: pcm16 (to App) or text-only (T2V/Agent)
```

The reason for separating the two sessions: to prevent confusion of translation direction. If merged into a single session, source/target languages get mixed up.

### Call Start Sequence

```
1. App -> POST /relay/calls/start
   ├─ ActiveCall creation
   ├─ System Prompt generation (prompt/generator_v3)
   ├─ DualSessionManager -> 2 OpenAI Realtime WS connections
   │   └─ Session B modalities branching based on communication_mode
   └─ Outbound call via Twilio REST API

2. App -> WS /relay/calls/{id}/stream connection
   └─ Register App WS with call_manager

3. Twilio -> POST /twilio/webhook/{id}
   └─ TwiML <Stream> response -> instruct Media Stream connection

4. Twilio -> WS /twilio/media-stream/{id} connection
   ├─ TwilioMediaStreamHandler creation
   ├─ AudioRouter creation -> Pipeline selection + register with call_manager
   ├─ dual_session.listen_all() background start
   └─ Start Twilio audio receive loop
```

> AudioRouter is created **when the Twilio WS connects**. Even if the App WS connects first, audio is not processed without an AudioRouter and the system waits.

### Audio Pipeline

#### Pipeline A: User -> Recipient (Voice Mode)

```
App                       Relay Server                      Twilio
 │                             │                              │
 │── audio_chunk ──> stream.py │                              │
 │                      │      │                              │
 │              Pipeline.handle_user_audio()                   │
 │                      │                                     │
 │              Record to RingBuffer A                         │
 │                      │                                     │
 │              SessionAHandler.send_user_audio()              │
 │                      │                                     │
 │                      v                                     │
 │              OpenAI Session A                               │
 │              [User speech STT -> translate -> TTS]          │
 │                      │                                     │
 │        response.audio.delta (g711_ulaw)                    │
 │                      │                                     │
 │              SessionAHandler._handle_audio_delta()          │
 │                      │                                     │
 │                (Guardrail check)                             │
 │                      │                                     │
 │              Pipeline._on_session_a_tts()                   │
 │                      │                                     │
 │                (EchoDetector.record_sent_chunk())            │
 │                (Interrupt check)                             │
 │                      │                                     │
 │              TwilioHandler.send_audio() ─────────────────> │
 │                                          g711_ulaw          │
```

#### Pipeline A: User -> Recipient (Text Mode)

```
App                       Relay Server                      Twilio
 │                             │                              │
 │── text_input ──> stream.py  │                              │
 │                      │      │                              │
 │              Pipeline.handle_user_text()                    │
 │                      │                                     │
 │              conversation.item.create (text)                │
 │              response.create (per-response instruction)     │
 │                      │                                     │
 │                      v                                     │
 │              OpenAI Session A                               │
 │              [text -> translate -> TTS] (strict relay)      │
 │                      │                                     │
 │              TwilioHandler.send_audio() ─────────────────> │
```

#### Pipeline B: Recipient -> User

```
Twilio                    Relay Server                      App
 │                             │                              │
 │── media event ──> twilio_   │                              │
 │   (g711_ulaw)     webhook   │                              │
 │                      │      │                              │
 │              Pipeline.handle_twilio_audio()                 │
 │                      │                                     │
 │              Energy Gate check (RMS threshold)              │
 │              ├─ Below threshold -> silence replacement      │
 │              └─ Above threshold -> continue                 │
 │                      │                                     │
 │              Silence Injection (during echo window)         │
 │              ├─ Echo window active -> replace with silence  │
 │              └─ No echo window -> pass through              │
 │                      │                                     │
 │              Local VAD (Silero + RMS)                       │
 │              ├─ Speech detected -> send to Session B        │
 │              └─ Silence -> skip                             │
 │                      │                                     │
 │              Record to RingBuffer B                         │
 │              SessionBHandler.send_recipient_audio()         │
 │                      │                                     │
 │                      v                                     │
 │              OpenAI Session B                               │
 │                      │                                     │
 │  ┌── Voice mode ─────┼──── Text mode ──────────┐           │
 │  │                   │                         │           │
 │  │ response.audio_   │  response.text.delta    │           │
 │  │ transcript.done   │  response.text.done     │           │
 │  │ + audio.delta     │  (no audio)             │           │
 │  │                   │                         │           │
 │  └───────────────────┼─────────────────────────┘           │
 │                      │                                     │
 │        ──────────────────────────────────────────────────> │
 │                   caption + audio (Voice)                   │
 │                   caption only   (Text)                     │
```

### Core Mechanisms

#### Silence Injection + Dynamic Energy Threshold (Primary Echo Prevention)

**VoiceToVoice/VoiceToText pipelines.** When Session A sends TTS to Twilio, the echo returns to Session B. The primary defense is replacing incoming Twilio audio with silence frames during the echo window, combined with energy-based filtering.

```
Session A TTS chunk sent to Twilio
       │
       v
  Start echo window (dynamic cooldown proportional to TTS length)
       │
       v
  Twilio audio arrives during echo window
       │
       ├─ RMS < ECHO_ENERGY_THRESHOLD_RMS (400) -> replace with silence (\xff)
       └─ RMS >= ECHO_ENERGY_THRESHOLD_RMS      -> genuine speech (pass through)
       │
       v
  Echo window expires -> normal processing resumes
```

**Principle**: PSTN echo has low energy (~100-400 RMS) while genuine recipient speech is much louder (~500-2000+ RMS). During echo windows, the energy threshold filters echo while allowing real speech through. Outside echo windows, `AUDIO_ENERGY_MIN_RMS` (150) filters PSTN background noise.

#### Local VAD (Server-Side Voice Activity Detection)

Silero VAD + RMS energy gate running on the relay server for Twilio-side audio (Session B input). Replaces reliance on OpenAI server VAD for recipient speech detection.

```
Twilio audio (g711_ulaw) -> mu-law decode -> PCM16
       │
       ├─ RMS < LOCAL_VAD_RMS_THRESHOLD (200) -> silence (skip)
       │
       └─ Silero VAD inference
          ├─ confidence >= SPEECH_THRESHOLD (0.5)
          │   └─ speech_frame_count >= MIN_SPEECH_FRAMES (3 = 96ms) -> SPEECH
          └─ confidence < SILENCE_THRESHOLD (0.35)
              └─ silence_frame_count >= MIN_SILENCE_FRAMES (15 = 480ms) -> SILENCE
```

**Benefits**: Consistent latency independent of OpenAI server VAD variability. Allows fine-grained control of speech detection thresholds for PSTN audio quality.

#### EchoDetector (Legacy -- Disabled by Default)

Pearson correlation-based echo fingerprinting. Exists in code but disabled (`ECHO_DETECTOR_ENABLED=False`). Can be re-enabled for experimentation. When active, compares energy patterns of sent TTS against incoming audio using normalized correlation.

Set `ECHO_DETECTOR_ENABLED=True` to activate. When disabled, Silence Injection + Dynamic Energy Threshold handles all echo prevention.

#### Echo Gate v2 (Output Gating)

Output-side protection that works alongside Silence Injection:

| Phase | Action |
|---|---|
| Session A TTS starts | `output_suppressed = True` (suppress Session B OUTPUT only, INPUT always active) |
| Session A response complete | Start cooldown timer (default 0.3s) |
| Cooldown complete | `output_suppressed = False` -> `flush_pending_output()` |
| Recipient speaks during suppression | Immediately release gate (recipient priority) |

Key point: **INPUT is never blocked** -> recipient speech can always be detected. Only OUTPUT is suppressed and stored in a pending queue for later flush.

> In TextToVoice/FullAgent pipelines, Echo Gate and Silence Injection are disabled. Since user input is text, a TTS echo loop is impossible.

#### Per-Response Instruction (TextToVoice Only)

TextToVoicePipeline injects a per-response instruction at `response.create` time to force the AI to only translate without adding arbitrary sentences:

```python
# TextToVoicePipeline.handle_user_text()
await session_a.send_text_item(text)                     # conversation.item.create
await session_a.create_response(instructions=strict_instruction)  # translate only
```

#### Exact Utterance (First Message)

Prevents AI expansion when sending the first greeting in TextToVoice/FullAgent:

```python
# first_message.py
await session_a.send_text_item(
    f'Say exactly this sentence and nothing else: "{greeting}"'
)
```

#### Session B Text-Only Modality

In TextToVoice/FullAgent, Session B is set to `modalities=['text']`:
- **0 audio output tokens** (cost savings)
- Receives translated text via `response.text.delta` / `response.text.done` events
- Uses `response.text.*` handlers instead of `response.audio_transcript.*`
- Note: `response.text.done` uses the `text` field (not `transcript`)

#### Interrupt (Turn Overlap Handling)

Priority: Recipient speech > User speech > AI generation

| Case | Handling |
|---|---|
| Recipient interrupts during Session A TTS | `response.cancel` + Twilio buffer `clear` |
| Recipient interrupts during User speech | Notify App, User audio remains buffered |
| Session A/B simultaneous output | Independent paths, parallel allowed |

After recipient speech ends, a **1.5-second cooldown** protects the pattern of continuing to speak after a short pause.

#### Context Manager (Conversation Context Retention)

Tracks the conversation with a **6-turn sliding window**. Injects into the session via `conversation.item.create` at utterance commit/end time to ensure translation consistency. Reason for not using `session.update`: it resets the entire session configuration.

#### First Message

When the recipient answers the phone and speaks for the first time ("Hello") -> Session B Server VAD detects it -> AI notice message is delivered as TTS through Session A.

#### Recovery

- **RingBuffer**: All audio is constantly recorded in a 30-second circular buffer
- **Heartbeat**: Failure detected if no events within 45 seconds
- **Reconnection**: Exponential backoff (1s -> 2s -> 4s -> 8s, max 30s)
- **Catch-up**: Unsent audio is batch-STT'd via Whisper API
- **Degraded Mode**: Falls back to Whisper batch if recovery fails for 10+ seconds

#### Guardrail (Translation Quality Protection)

```
Text delta arrives (ahead of audio)
 │
 ├─ Level 1: PASS -> audio forwarded to Twilio as-is
 ├─ Level 2: Informal expression detected -> audio forwarded, background LLM correction (logged)
 └─ Level 3: Profanity/prohibited word detected -> audio blocked, LLM correction then re-TTS
```

### Mode Comparison

| | Voice-to-Voice | Voice-to-Text | Text-to-Voice | Full Agent |
|---|---|---|---|---|
| Session A role | Voice translation | Voice translation | Text translation (strict) | AI autonomous conversation |
| User input | Voice | Voice | Text | Text instructions |
| Session B output | Voice + text | Text only (App) | Text only | Text only |
| Session B -> A feedback | None | None | None | Recipient translation auto-forwarded |
| Function Calling | Disabled | Disabled | Disabled | Active |
| Echo detection | Silence Injection + Gate | Silence Injection + Gate | Not needed | Not needed |
| B audio tokens | Consumed | Consumed | **0** | **0** |

### CallManager (Central Resource Management)

A singleton that manages all resources via a `call_id`-based dictionary:

```python
_calls:        dict[str, ActiveCall]           # Call state
_sessions:     dict[str, DualSessionManager]   # OpenAI sessions
_routers:      dict[str, AudioRouter]          # Audio routers (-> Pipeline)
_app_ws:       dict[str, WebSocket]            # App WebSocket
_listen_tasks: dict[str, asyncio.Task]         # Session listening tasks
```

`cleanup_call(call_id)` -- **idempotent** central cleanup:
1. Pipeline.stop() (via AudioRouter)
2. listen_task.cancel()
3. DualSession.close()
4. App WS notification + close
5. Supabase DB persist
6. Remove from dictionaries

Call sites: App WS disconnect, Twilio disconnect, status-callback, manual end, server shutdown.

## Communication Protocol

### App -> Relay (WebSocket JSON)

| type | data | Description |
|---|---|---|
| `audio_chunk` | `{audio: base64}` | User voice (pcm16) |
| `vad_state` | `{state: "committed"}` | Client VAD utterance end |
| `text_input` | `{text: string}` | User text input |
| `end_call` | `{}` | End call |

### Relay -> App (WebSocket JSON)

| type | data | Description |
|---|---|---|
| `caption.original` | `{role, text, stage:1}` | Recipient original caption (immediate) |
| `caption.translated` | `{role, text, stage:2}` | Recipient translated caption |
| `caption` | `{role, text, direction}` | Session A translation caption |
| `recipient_audio` | `{audio: base64}` | Recipient translated voice (pcm16, Voice mode only) |
| `call_status` | `{status, message}` | Call status change |
| `translation.state` | `{state}` | Translation progress state (processing/done) |
| `interrupt_alert` | `{speaking}` | Recipient interruption alert |
| `session.recovery` | `{status, gap_ms}` | Session recovery status |
| `guardrail.triggered` | `{level, original}` | Guardrail event |
| `error` | `{message}` | Error |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | | Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | | Twilio Auth Token |
| `TWILIO_PHONE_NUMBER` | | Twilio outbound phone number |
| `OPENAI_API_KEY` | | OpenAI API key |
| `OPENAI_REALTIME_MODEL` | `gpt-4o-realtime-preview` | Realtime model |
| `SUPABASE_URL` | | Supabase URL |
| `SUPABASE_SERVICE_KEY` | | Supabase service key |
| `RELAY_SERVER_URL` | `http://localhost:8000` | Server URL (for Twilio webhooks) |
| `GUARDRAIL_ENABLED` | `true` | Enable Guardrail |
| `HEARTBEAT_TIMEOUT_S` | `120` | Heartbeat timeout (seconds) |
| **Echo Prevention** | | |
| `ECHO_GATE_COOLDOWN_S` | `2.5` | Legacy Echo Gate cooldown (seconds, for fallback) |
| `AUDIO_ENERGY_GATE_ENABLED` | `true` | Enable PSTN noise energy gate |
| `AUDIO_ENERGY_MIN_RMS` | `150.0` | PSTN noise filter threshold (RMS) |
| `ECHO_ENERGY_THRESHOLD_RMS` | `400.0` | Echo window energy threshold (RMS) |
| `ECHO_DETECTOR_ENABLED` | `false` | Enable legacy EchoDetector (Pearson correlation) |
| `ECHO_DETECTOR_THRESHOLD` | `0.6` | Pearson correlation threshold |
| `ECHO_DETECTOR_SAFETY_COOLDOWN_S` | `0.15` | Safety margin after TTS ends (seconds) |
| `ECHO_DETECTOR_MIN_DELAY_CHUNKS` | `4` | Minimum echo delay (80ms) |
| `ECHO_DETECTOR_MAX_DELAY_CHUNKS` | `20` | Maximum echo delay (400ms) |
| `ECHO_DETECTOR_CORRELATION_WINDOW` | `8` | Comparison window size (160ms) |
| **Local VAD** | | |
| `LOCAL_VAD_ENABLED` | `true` | Enable Local VAD (Silero + RMS) |
| `LOCAL_VAD_RMS_THRESHOLD` | `200.0` | RMS energy threshold for speech |
| `LOCAL_VAD_SPEECH_THRESHOLD` | `0.5` | Silero speech confidence threshold |
| `LOCAL_VAD_SILENCE_THRESHOLD` | `0.35` | Silero silence confidence threshold |
| `LOCAL_VAD_MIN_SPEECH_FRAMES` | `3` | Minimum speech frames (96ms) |
| `LOCAL_VAD_MIN_SILENCE_FRAMES` | `15` | Minimum silence frames (480ms) |
| **Audio** | | |
| `MAX_SPEECH_DURATION_S` | `8.0` | Force commit after max speech time (seconds) |

---

## 한국어

OpenAI Realtime API + Twilio Media Streams 기반 양방향 실시간 번역 통화 서버.

## Quick Start

```bash
uv sync                              # 의존성 설치
cp .env.example .env                  # 환경변수 설정
uv run uvicorn src.main:app --reload  # 개발 서버
uv run pytest -v                      # 테스트 (147개)
```

## 디렉토리 구조

```
src/
├── main.py                  # FastAPI 엔트리포인트, 라우터 등록, lifespan
├── config.py                # 환경변수 (pydantic-settings)
├── types.py                 # 공유 타입/Enum/Pydantic 모델
├── call_manager.py          # 통화 라이프사이클 싱글톤 (중앙 리소스 관리)
├── logging_config.py        # 구조화 로깅 인프라
│
├── middleware/
│   ├── __init__.py
│   └── rate_limit.py        # Rate limiting 미들웨어
│
├── routes/
│   ├── calls.py             # POST /relay/calls/start, /end — 통화 시작/종료 API
│   ├── stream.py            # WS /relay/calls/{id}/stream — App ↔ Relay WebSocket
│   ├── twilio_webhook.py    # POST /twilio/webhook — TwiML, WS /twilio/media-stream
│   └── health.py            # GET /health
│
├── realtime/
│   ├── pipeline/            # Strategy 패턴 파이프라인 (모드별 독립 처리)
│   │   ├── __init__.py      # Pipeline 모듈 문서화 + 모드 매핑
│   │   ├── base.py          # BasePipeline ABC (공통 인터페이스)
│   │   ├── voice_to_voice.py # VoiceToVoicePipeline (EchoDetector + 전체 오디오)
│   │   ├── text_to_voice.py  # TextToVoicePipeline (per-response instruction + 텍스트 전용 B)
│   │   └── full_agent.py     # FullAgentPipeline (Function Calling + 자율 AI)
│   │
│   ├── audio_router.py      # AudioRouter — 얇은 위임자 (Pipeline 선택 + 공통 생명주기)
│   ├── echo_detector.py     # EchoDetector — Pearson 상관계수 기반 에코 감지 (레거시)
│   ├── local_vad.py         # Local VAD — Silero + RMS 서버 측 음성 감지
│   ├── audio_utils.py       # 공유 mu-law → linear PCM 변환 + RMS 계산
│   ├── session_manager.py   # RealtimeSession (OpenAI WS 래퍼) + DualSessionManager
│   ├── session_a.py         # SessionAHandler — User→수신자 번역 이벤트 처리
│   ├── session_b.py         # SessionBHandler — 수신자→User 번역 이벤트 처리
│   ├── interrupt_handler.py # 턴 겹침/인터럽트 처리 (수신자 우선)
│   ├── first_message.py     # 수신자 응답 감지 → AI 고지 전송 + Exact Utterance
│   ├── context_manager.py   # 대화 컨텍스트 슬라이딩 윈도우 (6턴)
│   ├── recovery.py          # 세션 장애 감지 → 자동 재연결 + catch-up
│   └── ring_buffer.py       # 30초 순환 오디오 버퍼 (Recovery용)
│
├── guardrail/
│   ├── checker.py           # GuardrailChecker — Level 1/2/3 분류 파이프라인
│   ├── filter.py            # 규칙 기반 텍스트 필터 (regex + keyword)
│   ├── dictionary.py        # 금지어/교정 사전 (ko/en/ja/zh)
│   └── fallback_llm.py      # GPT-4o-mini 텍스트 교정 (Level 2/3)
│
├── prompt/
│   ├── generator_v3.py      # Session A/B 시스템 프롬프트 동적 생성
│   └── templates.py         # 프롬프트 템플릿 + 언어별 변수
│
├── tools/
│   ├── definitions.py       # Agent Mode Function Calling 도구 정의
│   └── executor.py          # Function Call 실행 + ActiveCall에 결과 기록
│
├── twilio/
│   ├── outbound.py          # Twilio REST API 발신 (sync → async 래핑)
│   └── media_stream.py      # TwilioMediaStreamHandler (WS 파싱/오디오 전송)
│
└── db/
    └── supabase_client.py   # Supabase 영속화 (통화 종료 시 upsert)

tests/
├── conftest.py              # pytest fixtures
├── helpers.py               # ANSI 색상 유틸 (run.py 의존)
├── run.py                   # 커스텀 러너 (uv run python -m tests.run)
├── test_*.py                # pytest 단위 테스트 (test_local_vad, test_echo_gate 포함)
├── component/               # 모듈 벤치마크 (서버 불필요)
│   ├── test_cost_tracking.py
│   └── test_ring_buffer_perf.py
├── integration/             # 라이브 서버 필요 (localhost:8000)
│   ├── test_api.py
│   └── test_websocket.py
└── e2e/                     # 라이브 서버 + Twilio + OpenAI
    ├── call_client.py
    └── scenarios.py
```

## 아키텍처 개요

### Pipeline Architecture (Strategy 패턴)

`AudioRouter`는 **얇은 위임자**로, `communication_mode`에 따라 적절한 파이프라인을 선택하고 공통 생명주기(timer, recovery)만 관리한다. 실제 오디오/텍스트 핸들링은 각 파이프라인이 독립적으로 처리한다.

```
AudioRouter._create_pipeline(call)
    │
    ├─ VOICE_TO_VOICE  → VoiceToVoicePipeline(...)
    ├─ VOICE_TO_TEXT    → VoiceToVoicePipeline(..., suppress_b_audio=True)
    ├─ TEXT_TO_VOICE    → TextToVoicePipeline(...)
    └─ FULL_AGENT       → FullAgentPipeline(...)
```

#### BasePipeline 인터페이스

```python
class BasePipeline(ABC):
    async def handle_user_audio(audio_b64: str) → None     # User 오디오 입력
    async def handle_user_audio_commit() → None             # Client VAD 커밋
    async def handle_user_text(text: str) → None            # User 텍스트 입력
    async def handle_twilio_audio(audio_bytes: bytes) → None # Twilio 수신자 오디오
    async def start() → None                                # 파이프라인 시작
    async def stop() → None                                 # 파이프라인 중지
```

#### 파이프라인별 구성

| 구성 | VoiceToVoice | VoiceToText (서브모드) | TextToVoice | FullAgent |
|------|-------------|----------------------|-------------|-----------|
| **파이프라인** | VoiceToVoicePipeline | VoiceToVoicePipeline | TextToVoicePipeline | FullAgentPipeline |
| **Session A modalities** | `['text', 'audio']` | `['text', 'audio']` | `['text', 'audio']` | `['text', 'audio']` |
| **Session A turn_detection** | client/server VAD | client/server VAD | `null` (manual) | `null` (manual) |
| **Session B modalities** | `['text', 'audio']` | `['text', 'audio']` | **`['text']`** | **`['text']`** |
| **Session B → App 오디오** | 전송 | **생략** | N/A (텍스트만) | N/A (텍스트만) |
| **Silence Injection** | **활성** | **활성** | 불필요 | 불필요 |
| **Echo Gate** | 활성 | 활성 | 불필요 | 불필요 |
| **Energy Gate** | 활성 | 활성 | 활성 | 활성 |
| **Local VAD** | 활성 | 활성 | 활성 | 활성 |
| **Interrupt Handler** | 활성 | 활성 | 활성 | 활성 |
| **Function Calling** | 없음 | 없음 | 없음 | **활성** |
| **Per-response instruction** | 없음 | 없음 | **활성** | **활성** |

### Dual Session 구조

하나의 통화에 **2개의 독립적인 OpenAI Realtime 세션**이 동작한다:

```
Session A: User → 수신자 (source → target 번역)
  - 입력: pcm16 (App에서)
  - 출력: g711_ulaw (Twilio로)

Session B: 수신자 → User (target → source 번역)
  - 입력: g711_ulaw (Twilio에서)
  - 출력: pcm16 (App으로) 또는 text-only (T2V/Agent)
```

두 세션을 분리하는 이유: 번역 방향을 혼동하지 않기 위함. 단일 세션으로 합치면 source/target 언어가 뒤섞인다.

### 통화 시작 시퀀스

```
① App → POST /relay/calls/start
   ├─ ActiveCall 생성
   ├─ System Prompt 생성 (prompt/generator_v3)
   ├─ DualSessionManager → OpenAI Realtime WS 2개 연결
   │   └─ communication_mode에 따라 Session B modalities 분기
   └─ Twilio REST API로 전화 발신

② App → WS /relay/calls/{id}/stream 연결
   └─ call_manager에 App WS 등록

③ Twilio → POST /twilio/webhook/{id}
   └─ TwiML <Stream> 응답 → Media Stream 연결 지시

④ Twilio → WS /twilio/media-stream/{id} 연결
   ├─ TwilioMediaStreamHandler 생성
   ├─ AudioRouter 생성 → Pipeline 선택 + call_manager에 등록
   ├─ dual_session.listen_all() 백그라운드 시작
   └─ Twilio 오디오 수신 루프 시작
```

> AudioRouter는 **Twilio WS 연결 시점**에 생성된다. App WS가 먼저 연결되더라도 AudioRouter 없이는 오디오를 처리하지 않고 대기한다.

### 오디오 파이프라인

#### Pipeline A: User → 수신자 (Voice 모드)

```
App                       Relay Server                      Twilio
 │                             │                              │
 │── audio_chunk ──► stream.py │                              │
 │                      │      │                              │
 │              Pipeline.handle_user_audio()                   │
 │                      │                                     │
 │              RingBuffer A에 기록                             │
 │                      │                                     │
 │              SessionAHandler.send_user_audio()              │
 │                      │                                     │
 │                      ▼                                     │
 │              OpenAI Session A                               │
 │              [User 음성 STT → 번역 → TTS]                   │
 │                      │                                     │
 │        response.audio.delta (g711_ulaw)                    │
 │                      │                                     │
 │              SessionAHandler._handle_audio_delta()          │
 │                      │                                     │
 │                (Guardrail 검사)                              │
 │                      │                                     │
 │              Pipeline._on_session_a_tts()                   │
 │                      │                                     │
 │                (EchoDetector.record_sent_chunk())            │
 │                (Interrupt 체크)                              │
 │                      │                                     │
 │              TwilioHandler.send_audio() ───────────────────►│
 │                                          g711_ulaw          │
```

#### Pipeline A: User → 수신자 (Text 모드)

```
App                       Relay Server                      Twilio
 │                             │                              │
 │── text_input ──► stream.py  │                              │
 │                      │      │                              │
 │              Pipeline.handle_user_text()                    │
 │                      │                                     │
 │              conversation.item.create (텍스트)               │
 │              response.create (per-response instruction)     │
 │                      │                                     │
 │                      ▼                                     │
 │              OpenAI Session A                               │
 │              [텍스트 → 번역 → TTS] (strict relay)           │
 │                      │                                     │
 │              TwilioHandler.send_audio() ───────────────────►│
```

#### Pipeline B: 수신자 → User

```
Twilio                    Relay Server                      App
 │                             │                              │
 │── media event ──► twilio_   │                              │
 │   (g711_ulaw)     webhook   │                              │
 │                      │      │                              │
 │              Pipeline.handle_twilio_audio()                 │
 │                      │                                     │
 │              Energy Gate 검사 (RMS 임계값)                   │
 │              ├─ 임계값 미만 → silence 교체                   │
 │              └─ 임계값 이상 → 계속                           │
 │                      │                                     │
 │              Silence Injection (echo window 중)             │
 │              ├─ Echo window 활성 → silence 교체              │
 │              └─ Echo window 없음 → 통과                     │
 │                      │                                     │
 │              Local VAD (Silero + RMS)                       │
 │              ├─ 발화 감지 → Session B로 전달                 │
 │              └─ 무음 → 건너뜀                               │
 │                      │                                     │
 │              RingBuffer B에 기록                             │
 │              SessionBHandler.send_recipient_audio()         │
 │                      │                                     │
 │                      ▼                                     │
 │              OpenAI Session B                               │
 │                      │                                     │
 │  ┌── Voice 모드 ─────┼──── Text 모드 ──────────┐           │
 │  │                   │                         │           │
 │  │ response.audio_   │  response.text.delta    │           │
 │  │ transcript.done   │  response.text.done     │           │
 │  │ + audio.delta     │  (audio 없음)           │           │
 │  │                   │                         │           │
 │  └───────────────────┼─────────────────────────┘           │
 │                      │                                     │
 │        ────────────────────────────────────────────────────►│
 │                   caption + audio (Voice)                   │
 │                   caption only   (Text)                     │
```

### 핵심 메커니즘

#### Silence Injection + Dynamic Energy Threshold (주요 에코 방지)

**VoiceToVoice/VoiceToText 파이프라인.** Session A가 TTS를 Twilio로 보내면 에코가 Session B로 돌아온다. 주요 방어 기법은 echo window 동안 Twilio 수신 오디오를 silence 프레임으로 교체하는 것이며, 에너지 기반 필터링과 함께 동작한다.

```
Session A TTS 청크를 Twilio로 전송
       │
       ▼
  Echo window 시작 (TTS 길이에 비례하는 동적 cooldown)
       │
       ▼
  Echo window 동안 Twilio 오디오 도착
       │
       ├─ RMS < ECHO_ENERGY_THRESHOLD_RMS (400) → silence로 교체 (\xff)
       └─ RMS >= ECHO_ENERGY_THRESHOLD_RMS      → 실제 발화 (통과)
       │
       ▼
  Echo window 만료 → 정상 처리 재개
```

**원리**: PSTN 에코는 낮은 에너지(~100-400 RMS)인 반면, 실제 수신자 발화는 훨씬 높다(~500-2000+ RMS). Echo window 동안 에너지 임계값으로 에코를 필터링하면서 실제 발화는 통과시킨다. Echo window 밖에서는 `AUDIO_ENERGY_MIN_RMS` (150)로 PSTN 배경 소음을 필터링한다.

#### Local VAD (서버 측 음성 감지)

Silero VAD + RMS 에너지 게이트를 relay 서버에서 실행하여 Twilio 측 오디오(Session B 입력)의 음성을 감지한다. 수신자 발화 감지를 OpenAI 서버 VAD에만 의존하지 않는다.

```
Twilio 오디오 (g711_ulaw) → mu-law 디코드 → PCM16
       │
       ├─ RMS < LOCAL_VAD_RMS_THRESHOLD (200) → 무음 (건너뜀)
       │
       └─ Silero VAD 추론
          ├─ 신뢰도 >= SPEECH_THRESHOLD (0.5)
          │   └─ speech_frame_count >= MIN_SPEECH_FRAMES (3 = 96ms) → 발화
          └─ 신뢰도 < SILENCE_THRESHOLD (0.35)
              └─ silence_frame_count >= MIN_SILENCE_FRAMES (15 = 480ms) → 무음
```

**장점**: OpenAI 서버 VAD 변동과 무관한 일관된 지연 시간. PSTN 오디오 품질에 맞춘 세밀한 발화 감지 임계값 제어 가능.

#### EchoDetector (레거시 -- 기본 비활성)

Pearson 상관계수 기반 에코 핑거프린팅. 코드에 존재하나 비활성 상태(`ECHO_DETECTOR_ENABLED=False`). 실험 목적으로 재활성화 가능. 활성화 시 보낸 TTS의 에너지 패턴과 수신 오디오를 정규화 상관계수로 비교한다.

`ECHO_DETECTOR_ENABLED=True`로 설정하면 활성화. 비활성 시 Silence Injection + Dynamic Energy Threshold가 모든 에코 방지를 담당한다.

#### Echo Gate v2 (출력 게이팅)

Silence Injection과 함께 동작하는 출력측 보호:

| 단계 | 동작 |
|---|---|
| Session A TTS 시작 | `output_suppressed = True` (Session B OUTPUT만 억제, INPUT은 항상 활성) |
| Session A 응답 완료 | 쿨다운 타이머 시작 (기본 0.3초) |
| 쿨다운 완료 | `output_suppressed = False` → `flush_pending_output()` |
| 억제 중 수신자 발화 | 즉시 게이트 해제 (수신자 우선) |

핵심: **INPUT은 차단하지 않음** → 수신자 발화를 항상 감지할 수 있다. OUTPUT만 억제하고 pending 큐에 저장 후 나중에 배출.

> TextToVoice/FullAgent 파이프라인에서는 Echo Gate/Silence Injection 모두 비활성. 사용자 입력이 텍스트이므로 TTS echo loop 자체가 불가능.

#### Per-Response Instruction (TextToVoice 전용)

TextToVoicePipeline은 `response.create` 시 per-response instruction을 주입하여 AI가 번역만 하고 임의 문장을 추가하지 않도록 강제한다:

```python
# TextToVoicePipeline.handle_user_text()
await session_a.send_text_item(text)                     # conversation.item.create
await session_a.create_response(instructions=strict_instruction)  # 번역만 하라
```

#### Exact Utterance (First Message)

TextToVoice/FullAgent에서 첫 인사를 보낼 때 AI 확장을 방지:

```python
# first_message.py
await session_a.send_text_item(
    f'Say exactly this sentence and nothing else: "{greeting}"'
)
```

#### Session B Text-Only Modality

TextToVoice/FullAgent에서 Session B는 `modalities=['text']`로 설정되어:
- **audio output 토큰 0** (비용 절약)
- `response.text.delta` / `response.text.done` 이벤트로 번역 텍스트 수신
- `response.audio_transcript.*` 대신 `response.text.*` 핸들러 사용
- 주의: `response.text.done`은 `text` 필드 사용 (`transcript` 아님)

#### Interrupt (턴 겹침 처리)

우선순위: 수신자 발화 > User 발화 > AI 생성

| 케이스 | 처리 |
|---|---|
| Session A TTS 중 수신자 끼어듦 | `response.cancel` + Twilio 버퍼 `clear` |
| User 발화 중 수신자 끼어듦 | App에 알림, User 오디오는 버퍼링 유지 |
| Session A/B 동시 출력 | 독립 경로이므로 병렬 허용 |

수신자 발화 종료 후 **1.5초 쿨다운**으로 짧은 쉼 후 이어 말하는 패턴을 보호한다.

#### Context Manager (대화 맥락 유지)

최근 **6턴 슬라이딩 윈도우**로 대화를 추적한다. 발화 커밋/종료 시점에 `conversation.item.create`로 세션에 주입하여 번역 일관성을 확보한다. `session.update`를 쓰지 않는 이유: 세션 전체 설정이 리셋되기 때문.

#### First Message

수신자가 전화를 받고 처음 발화하면("여보세요") → Session B Server VAD가 감지 → AI 고지 메시지를 Session A를 통해 TTS로 전달.

#### Recovery

- **RingBuffer**: 30초 순환 버퍼에 모든 오디오를 항상 기록
- **Heartbeat**: 45초 이내 이벤트 없으면 장애로 판단
- **재연결**: exponential backoff (1s → 2s → 4s → 8s, max 30s)
- **Catch-up**: 미전송 오디오를 Whisper API로 배치 STT
- **Degraded Mode**: 10초 이상 복구 실패 시 Whisper batch fallback

#### Guardrail (번역 품질 보호)

```
텍스트 델타 도착 (오디오보다 먼저)
 │
 ├─ Level 1: PASS → 오디오 그대로 Twilio로 전달
 ├─ Level 2: 비격식 표현 감지 → 오디오는 전달, 백그라운드 LLM 교정 (로그)
 └─ Level 3: 욕설/금지어 감지 → 오디오 차단, LLM 교정 후 재TTS
```

### 모드별 비교

| | Voice-to-Voice | Voice-to-Text | Text-to-Voice | Full Agent |
|---|---|---|---|---|
| Session A 역할 | 음성 번역 | 음성 번역 | 텍스트 번역 (strict) | AI 자율 대화 |
| User 입력 | 음성 | 음성 | 텍스트 | 텍스트 지시 |
| Session B 출력 | 음성 + 텍스트 | 텍스트만 (App) | 텍스트만 | 텍스트만 |
| Session B → A 피드백 | 없음 | 없음 | 없음 | 수신자 번역 자동 전달 |
| Function Calling | 비활성 | 비활성 | 비활성 | 활성 |
| Echo 감지 | Silence Injection + Gate | Silence Injection + Gate | 불필요 | 불필요 |
| B audio 토큰 | 소비 | 소비 | **0** | **0** |

### CallManager (중앙 리소스 관리)

`call_id` 기반 딕셔너리로 모든 리소스를 관리하는 싱글톤:

```python
_calls:        dict[str, ActiveCall]           # 통화 상태
_sessions:     dict[str, DualSessionManager]   # OpenAI 세션
_routers:      dict[str, AudioRouter]          # 오디오 라우터 (→ Pipeline)
_app_ws:       dict[str, WebSocket]            # App WebSocket
_listen_tasks: dict[str, asyncio.Task]         # 세션 리스닝 태스크
```

`cleanup_call(call_id)` — **idempotent**한 중앙 정리:
1. Pipeline.stop() (via AudioRouter)
2. listen_task.cancel()
3. DualSession.close()
4. App WS 알림 + 닫기
5. Supabase DB persist
6. 딕셔너리에서 제거

호출 지점: App WS 끊김, Twilio 끊김, status-callback, 수동 종료, 서버 셧다운.

## 통신 프로토콜

### App → Relay (WebSocket JSON)

| type | data | 설명 |
|---|---|---|
| `audio_chunk` | `{audio: base64}` | User 음성 (pcm16) |
| `vad_state` | `{state: "committed"}` | Client VAD 발화 종료 |
| `text_input` | `{text: string}` | User 텍스트 입력 |
| `end_call` | `{}` | 통화 종료 |

### Relay → App (WebSocket JSON)

| type | data | 설명 |
|---|---|---|
| `caption.original` | `{role, text, stage:1}` | 수신자 원문 자막 (즉시) |
| `caption.translated` | `{role, text, stage:2}` | 수신자 번역 자막 |
| `caption` | `{role, text, direction}` | Session A 번역 자막 |
| `recipient_audio` | `{audio: base64}` | 수신자 번역 음성 (pcm16, Voice 모드만) |
| `call_status` | `{status, message}` | 통화 상태 변경 |
| `translation.state` | `{state}` | 번역 진행 상태 (processing/done) |
| `interrupt_alert` | `{speaking}` | 수신자 끼어듦 알림 |
| `session.recovery` | `{status, gap_ms}` | 세션 복구 상태 |
| `guardrail.triggered` | `{level, original}` | Guardrail 이벤트 |
| `error` | `{message}` | 에러 |

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | | Twilio 계정 SID |
| `TWILIO_AUTH_TOKEN` | | Twilio 인증 토큰 |
| `TWILIO_PHONE_NUMBER` | | Twilio 발신 번호 |
| `OPENAI_API_KEY` | | OpenAI API 키 |
| `OPENAI_REALTIME_MODEL` | `gpt-4o-realtime-preview` | Realtime 모델 |
| `SUPABASE_URL` | | Supabase URL |
| `SUPABASE_SERVICE_KEY` | | Supabase 서비스 키 |
| `RELAY_SERVER_URL` | `http://localhost:8000` | 서버 URL (Twilio webhook용) |
| `GUARDRAIL_ENABLED` | `true` | Guardrail 활성화 |
| `HEARTBEAT_TIMEOUT_S` | `120` | Heartbeat 타임아웃 (초) |
| **에코 방지** | | |
| `ECHO_GATE_COOLDOWN_S` | `2.5` | Legacy Echo Gate 쿨다운 (초, 폴백용) |
| `AUDIO_ENERGY_GATE_ENABLED` | `true` | PSTN 소음 에너지 게이트 활성화 |
| `AUDIO_ENERGY_MIN_RMS` | `150.0` | PSTN 소음 필터 임계값 (RMS) |
| `ECHO_ENERGY_THRESHOLD_RMS` | `400.0` | Echo window 에너지 임계값 (RMS) |
| `ECHO_DETECTOR_ENABLED` | `false` | 레거시 EchoDetector 활성화 (Pearson 상관계수) |
| `ECHO_DETECTOR_THRESHOLD` | `0.6` | Pearson 상관계수 임계값 |
| `ECHO_DETECTOR_SAFETY_COOLDOWN_S` | `0.15` | TTS 종료 후 안전 마진 (초) |
| `ECHO_DETECTOR_MIN_DELAY_CHUNKS` | `4` | 최소 에코 딜레이 (80ms) |
| `ECHO_DETECTOR_MAX_DELAY_CHUNKS` | `20` | 최대 에코 딜레이 (400ms) |
| `ECHO_DETECTOR_CORRELATION_WINDOW` | `8` | 비교 윈도우 크기 (160ms) |
| **Local VAD** | | |
| `LOCAL_VAD_ENABLED` | `true` | Local VAD 활성화 (Silero + RMS) |
| `LOCAL_VAD_RMS_THRESHOLD` | `200.0` | 발화 판정 RMS 에너지 임계값 |
| `LOCAL_VAD_SPEECH_THRESHOLD` | `0.5` | Silero 발화 신뢰도 임계값 |
| `LOCAL_VAD_SILENCE_THRESHOLD` | `0.35` | Silero 무음 신뢰도 임계값 |
| `LOCAL_VAD_MIN_SPEECH_FRAMES` | `3` | 최소 발화 프레임 (96ms) |
| `LOCAL_VAD_MIN_SILENCE_FRAMES` | `15` | 최소 무음 프레임 (480ms) |
| **오디오** | | |
| `MAX_SPEECH_DURATION_S` | `8.0` | 최대 발화 시간 후 강제 commit (초) |
