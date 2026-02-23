<div align="center">

<img src="docs/assets/wigvo_logo.png" alt="WIGVO" width="480" />

<br />
<br />

**AI-Powered Realtime Phone Translation & Relay Platform**

Real-time bidirectional voice translation over actual phone calls.
No apps needed on the recipient's end. Just call.

*From zero to working PSTN bidirectional translation calls in 7 days.*
*150+ tests. Production-deployed on Google Cloud Run.*

<br />

[![Live Demo](https://img.shields.io/badge/Live_Demo-wigvo.run-0F172A?style=for-the-badge&logo=google-cloud&logoColor=white)](https://wigvo.run)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](#tech-stack)
[![Next.js](https://img.shields.io/badge/Next.js-16-000000?style=for-the-badge&logo=nextdotjs&logoColor=white)](#tech-stack)
[![Tests](https://img.shields.io/badge/Tests-150+_passing-22C55E?style=for-the-badge&logo=pytest&logoColor=white)](#testing)

<br />

[한국어](README.ko.md)

</div>

---

## What is WIGVO?

WIGVO connects people across language barriers through **real phone calls** — not chat, not text translation, but actual voice conversations where each side speaks their own language naturally.

> **What WIGVO is**: A systems engineering platform that makes state-of-the-art AI models (OpenAI GPT-4o Realtime) work reliably over real telephone lines — solving echo, noise, latency, and codec problems that these models were never designed to handle.
>
> **What WIGVO is not**: We didn't build the AI model. OpenAI's Realtime API handles STT, translation, and TTS. Our contribution is the **entire layer between that API and the actual phone network** — the part that nobody else has built.

```
You say:      "I'd like to make a reservation for tonight"
                    ↓ OpenAI Realtime API (< 500ms)
Recipient hears:  "오늘 저녁 예약하고 싶은데요"  ← via Twilio phone call
                    ↓
Recipient says:   "네, 몇 시에 오실 건가요?"
                    ↓ OpenAI Realtime API (< 500ms)
You hear:         "Yes, what time would you like to come?"
```

The recipient just answers a normal phone call. No apps. No setup. They don't even know AI is involved.

---

## The Problem

Every year, **2M+ foreigners in South Korea** struggle with a simple task: making a phone call.

Booking a hospital appointment. Ordering delivery. Calling a restaurant. Contacting a government office. These require Korean-language phone calls — and existing translation apps only handle **one-way text**, not **real-time bidirectional voice over a phone line**.

| Who | Pain Point | Scale |
|-----|-----------|-------|
| Foreigners in Korea | Can't make calls in Korean | 2.2M residents (2024) |
| Koreans abroad | Can't make calls in local language | 2.8M overseas Koreans |
| Speech/hearing disabilities | Voice calls inaccessible | 390K registered in Korea |
| Phone anxiety (콜포비아) | Avoid calls entirely | ~40% of Korean Gen-Z |

**No existing product solves bidirectional real-time voice translation + phone connection in a single platform.**

---

## Competitive Landscape

Samsung and SKT solve real-time phone translation with hundreds of engineers, proprietary hardware, and telecom infrastructure access. WIGVO solves the same problem **with software alone, built in 7 days**.

This isn't a claim of superiority — it's a fundamentally different approach. They control the hardware and the network. We operate at the application layer, bridging existing infrastructure (Twilio, OpenAI) into a system that works over any phone line.

| | Samsung Galaxy AI | SKT A.dot | DeepL Voice | **WIGVO** |
|---|---|---|---|---|
| **Architecture** | On-Device NPU | Telco Network (IMS) | WebRTC App-to-App | **Web-PSTN Bridge (OTT)** |
| **Translation Engine** | Samsung Gauss (on-device) | Proprietary LLM | DeepL NMT | OpenAI GPT-4o Realtime* |
| **Echo Control** | Hardware AEC (chipset) | Telecom-level control | N/A (no phone) | **Software Silence Injection** |
| **Phone Call Support** | Galaxy S24+ only | SKT subscribers only | No PSTN support | **Any phone number, any carrier** |
| **Recipient Requirements** | Same device ecosystem | Same carrier | App installed | **Nothing — just answer the phone** |
| **Accessibility Modes** | Translation only | Call summary / recording | Translation only | **4 modes: V2V, V2T, T2V, AI Agent** |
| **Device Requirement** | Flagship Galaxy | SKT SIM required | App on both sides | **Any browser or smartphone** |

*\*WIGVO uses OpenAI's model as-is. Our engineering contribution is not the AI model itself, but the system that makes it work reliably over PSTN phone lines — echo cancellation, VAD, codec bridging, and session management that the API was never designed to handle.*

### Where WIGVO Fits

- **Samsung Galaxy AI** and **SKT A.dot** are infrastructure-level solutions with massive investment. They have advantages WIGVO cannot match (native latency, hardware AEC).
- **WIGVO's advantage** is accessibility and speed: any browser, any carrier, any phone number. No hardware lock-in, no carrier lock-in. And the entire system was built and deployed in 7 days — demonstrating what a small team can do with the right architecture.
- **DeepL Voice** operates in a different space entirely (app-to-app, no PSTN). It's not a direct competitor.

---

## Technical Moat — Why This Is Hard to Replicate

Building "realtime translation" is conceptually simple — OpenAI's Realtime API handles STT, translation, and TTS in a single WebSocket call. But connecting that API to **actual phone lines** introduces an entirely different class of engineering challenges that the API was never designed for, and that no amount of prompt engineering can solve.

### 1. The PSTN Audio Problem

PSTN (Public Switched Telephone Network) audio is fundamentally different from web audio:

- **Constant background noise** (RMS 50-200) that never goes silent — telephone lines always carry line noise
- **Narrowband codec** (g711 mu-law, 8kHz) with compression artifacts
- **Echo loops** — audio sent to the recipient bounces back through the phone network with 200-500ms delay
- **No silence** — unlike web audio where "no speech" = digital zero, PSTN "no speech" = noisy analog signal

Every voice AI demo works perfectly with clean WebRTC audio. WIGVO had to make it work with dirty, noisy, echoing PSTN audio — and that required building entirely new audio processing layers.

### 2. Independent Dual Session Architecture

A single AI session cannot handle bidirectional translation — it confuses source and target languages mid-conversation. WIGVO runs **two physically independent OpenAI Realtime sessions** with opposite translation directions:

```
Session A: User speaks English  →  AI translates to Korean  →  Twilio sends to phone
Session B: Phone receives Korean →  AI translates to English →  App plays to user
```

This separation is non-negotiable. It eliminates translation direction confusion, enables independent interrupt handling per direction, and allows each session to maintain its own conversation context.

### 3. Software-Only Echo Cancellation on PSTN

Hardware echo cancellation (AEC) is built into phone chipsets and telecom switches. WIGVO doesn't have access to either. The echo problem must be solved **purely in software**, on audio that has already been encoded, transmitted through the phone network, and echoed back.

The solution — **Silence Injection with Dynamic Cooldown** — was developed through 7 iterations of trial and error (detailed below). It replaces incoming audio with mu-law silence frames during TTS playback, with cooldown duration proportional to TTS length plus a 0.5s echo round-trip margin.

### 4. Local VAD for PSTN (Silero Neural Network)

OpenAI's built-in Server VAD cannot handle PSTN audio. Background noise causes it to never detect speech-end (we observed 45-72 second stuck states). WIGVO runs a **local Silero VAD** with a 2-stage pipeline:

- **Stage 1**: RMS energy gate — sub-150 audio is definite silence, skip neural inference
- **Stage 2**: Silero RNN — hysteresis state machine with separate start/stop thresholds

This reduced speech-end detection from 15-72 seconds to **480ms**.

### 5. Modular Pipeline Architecture (Strategy Pattern)

Four communication modes are implemented as independent pipeline strategies, sharing a common interface through `AudioRouter`:

```
AudioRouter (thin delegator, ~160 lines)
    │
    ├── VoiceToVoicePipeline  ← Echo Gate + Silence Injection + full audio path
    ├── TextToVoicePipeline   ← Per-response instruction override + text-only Session B
    └── FullAgentPipeline     ← Function calling + autonomous AI conversation
```

This isn't just code organization — it enables immediate expansion to new use cases (disability assistance, AI concierge, multi-party calls) without touching existing pipelines.

### 6. What We Built vs. What We Use

To be explicit about our technical boundaries:

| Layer | Who Built It | WIGVO's Role |
|-------|-------------|--------------|
| STT + Translation + TTS | **OpenAI** (GPT-4o Realtime API) | Consumer — we call the API |
| Phone network bridging | **Twilio** (Media Streams) | Consumer — we use their SIP trunking |
| PSTN echo cancellation | **WIGVO** | Built from scratch (Silence Injection + Dynamic Cooldown) |
| Local VAD for PSTN | **WIGVO** | Built from scratch (Silero integration + 2-stage pipeline) |
| Dual Session architecture | **WIGVO** | Designed and implemented |
| Pipeline Strategy system | **WIGVO** | Designed and implemented |
| Session recovery + degraded mode | **WIGVO** | Built from scratch |
| Guardrail system | **WIGVO** | Built from scratch |
| Codec bridging (g711↔pcm16) | **WIGVO** | Built from scratch |
| Full-stack product (Web + Mobile + Relay) | **WIGVO** | 7 days, 1 developer |

The AI model is not ours. The phone infrastructure is not ours. **Everything between them** — the part that makes the combination actually work — is what we built.

---

## Engineering Challenges — The Hard Parts

### Challenge: Recipient Speech Not Recognized — 7-Step Evolution

The longest single debugging session in WIGVO's development. The recipient was clearly speaking, but the system couldn't detect their speech or produce translations. **11 commits in one day** to solve this one problem.

**Root cause**: PSTN audio characteristics were fundamentally underestimated. Phone lines carry constant background noise at RMS 50-200, with real speech at RMS 500-2000+. This "never truly silent" noise broke every assumption.

<details>
<summary><b>Step 1: OpenAI Server VAD — Initial Design</b></summary>

Started by trusting OpenAI Realtime API's built-in Server VAD entirely.

```
threshold: 0.5, silence_duration: 200ms
```

**Problem**: PSTN background noise (RMS 50-200) looked like "still speaking" to Server VAD. `speech_started` fired, but `speech_stopped` never came. The recipient would say "네, 아직 있어요" but the post-speech line noise made VAD think they were still talking. Translation wouldn't begin until the 15-second timeout.
</details>

<details>
<summary><b>Step 2: Server VAD Tuning</b></summary>

Made VAD less sensitive:

```
threshold: 0.5 → 0.8 (only loud sounds = speech)
silence_duration: 200ms → 600ms (need longer silence)
```

Added client-side energy gate: drop audio with RMS < 150.

**Problem**: threshold 0.8 was too high — quiet speakers were completely ignored. Soft-spoken recipients got zero recognition.
</details>

<details>
<summary><b>Step 3: Energy Threshold Lowering</b></summary>

Kept lowering the RMS threshold:
```
150 → 80 → 30 → 20
```

**Dilemma**: Lower threshold = PSTN noise passes through = VAD stuck again. Higher threshold = real speech filtered out. The gap between PSTN noise (50-200) and real speech (500-2000+) wasn't clean enough for a simple threshold.
</details>

<details>
<summary><b>Step 4: Dynamic Energy Threshold</b></summary>

Changed approach: instead of a fixed threshold, **dynamically adjust based on context**.

- During echo window (TTS playing): threshold = 400 RMS (blocks echo ~100-400, passes speech ~500+)
- Normal operation: threshold = 80 RMS (blocks only line noise)

**Problem**: PSTN background noise immediately after echo window still caused VAD to get stuck.
</details>

<details>
<summary><b>Step 5: The Critical Discovery — "Don't Drop Audio, Replace It"</b></summary>

**This was the breakthrough insight** that took the longest to reach.

When noisy audio was *dropped* (not sent to OpenAI), the Server VAD interpreted this as "audio stream interrupted" — not as silence. VAD distinguishes between "no data arriving" and "silent data arriving". With no data, it waits indefinitely instead of firing `speech_stopped`.

**Solution**: Replace noisy audio with **silence frames** (`0xFF` mu-law) instead of dropping it. The audio stream stays continuous, but VAD correctly recognizes "oh, it's quiet now" and fires `speech_stopped`.

```python
# WRONG — VAD gets stuck (no data = "still waiting")
if rms < threshold:
    return  # drop

# CORRECT — VAD detects silence normally
if rms < threshold:
    silence = b"\xff" * len(audio)  # mu-law silence
    await session_b.send_audio(base64.b64encode(silence))
```
</details>

<details>
<summary><b>Step 6: Max Speech Timer (Safety Net)</b></summary>

Silence injection fixed most cases, but PSTN line noise occasionally spiked above RMS 200, bypassing the silence replacement. Observed 45-72 second stuck states in production logs.

**Solution**: Added an 8-second max speech timer as a safety net. If `speech_started` fires but `speech_stopped` doesn't arrive within 8 seconds, force-commit the audio buffer and trigger translation. Continuous speech auto-restarts.
</details>

<details>
<summary><b>Step 7: Abandon Server VAD — Local VAD (Silero Neural Network)</b></summary>

Ultimately, OpenAI Server VAD **cannot handle PSTN audio**. Replaced it entirely with a local Silero VAD running server-side.

**2-stage local detection**:

```
Stage 1: RMS Energy Gate
  RMS < 200  →  Definite silence, skip Silero (save CPU)
  RMS ≥ 200  →  Proceed to Stage 2

Stage 2: Silero VAD Neural Network (16kHz)
  Hysteresis State Machine:
    SILENCE → SPEAKING:  probability > 0.5 for 3 consecutive frames (96ms)
    SPEAKING → SILENCE:  probability < 0.35 for 15 consecutive frames (480ms)
```

**Additional technical hurdles**:
- Twilio sends 8kHz g711_ulaw; Silero requires 16kHz PCM → zero-order hold upsampling + mu-law→float32 decoding
- Twilio frames are 20ms (160 samples); Silero needs 32ms (512 samples) → frame adapter with internal buffering
- Skipping Silero during RMS-silence stalls internal RNN state → model reset on RMS-silence→active transition
- Silero `.so` file failed to load on Cloud Run gVisor → ELF binary patch to clear executable stack flag
- OpenAI `turn_detection=null` means manual `commit_audio_only()` + `response.create()` on speech end
- Continuous speech (short pauses between sentences) → 300ms debounce: if `speech_started` fires within 300ms of `speech_stopped`, cancel the commit and continue buffering

**Final results**:
| Metric | Before | After |
|--------|--------|-------|
| Speech-end detection | 15-72 seconds | **480ms** |
| Translation start (E2E) | 15+ seconds | **~3 seconds** |
| Hallucination | Frequent (noise segments) | Blocked by VAD parameter tuning |

Commit trail (one day of debugging):
```
09ac3c3  3-layer noise filter (Server VAD tuning)
20eb470  Energy threshold lowering (150→20)
309c502  Energy threshold lowering (150→80)
0ccc389  Dynamic Energy Threshold
8488084  PSTN VAD re-tuning
2554628  Energy threshold lowering (80→30) + diagnostic logging
e83c9d5  Critical discovery: silence injection
6f4668f  Silence injection expansion + max speech timer
3330eeb  Local VAD (Silero) introduction
b8f552e  Local VAD stabilization + debounce
6569d76  Hallucination blocklist + VAD parameter final tuning
```
</details>

### Challenge: Echo Cancellation Without Hardware — 4-Layer Evolution

When Session A sends translated TTS to the recipient via Twilio, that same audio echoes back through the phone network into Session B's input. Without mitigation, this creates an **infinite translation loop** — the system translates its own output endlessly.

WIGVO solved this entirely in software, without access to hardware AEC or telecom-level echo cancellation.

**Layer 1: Silence Injection + Dynamic Cooldown (primary)**

During TTS playback ("echo window"), all incoming Twilio audio is replaced with mu-law silence frames (`0xFF`). This prevents echo contamination while keeping the audio stream continuous for VAD.

```
Echo window lifecycle:
  TTS chunk arrives     → Activate echo window
  TTS complete          → Start dynamic cooldown
  Cooldown expired      → Deactivate echo window

Dynamic cooldown = remaining TTS playback time + 0.5s echo round-trip margin
  Short utterance ("네"): ~0.8s cooldown
  Long sentence:          ~3.0s cooldown
```

**Layer 2: Local VAD Echo Rejection**

During echo window, Local VAD receives silence frames, so it never fires `speech_started` for echo. If the recipient genuinely speaks during TTS playback (interrupt), their speech energy (500-2000+ RMS) is immediately detectable once the echo window closes.

**Layer 3: Interrupt Priority System**

```
Priority 1 (highest):  Recipient speech  → Immediately cancel AI output + close echo window
Priority 2:            User speech        → Cancel AI, queue for translation
Priority 3 (lowest):   AI generation      → Can be interrupted by anyone
```

Recipient speech always takes priority. A max speech duration safety net (8 seconds) force-commits audio if VAD fails to detect speech-end.

**Layer 4: Legacy Audio Fingerprint (disabled)**

A Pearson-correlation-based per-chunk echo detector exists in the codebase (`echo_detector.py`, `ECHO_DETECTOR_ENABLED=False`). It was the first approach attempted but proved unreliable for PSTN audio where echo arrives distorted and time-shifted. The Silence Injection approach is simpler and more robust.

---

## How It Works

### For the User (Web App)

```
┌─────────────────────────────────────────────────────────┐
│  1. Chat with AI         "식당 예약하고 싶어요"              │
│     ↓                                                    │
│  2. AI collects info     날짜, 시간, 인원, 요청사항         │
│     ↓                                                    │
│  3. Find the place       네이버 장소 검색 → 전화번호 확인    │
│     ↓                                                    │
│  4. One-click call       Relay Server → Twilio 발신       │
│     ↓                                                    │
│  5. Real-time monitor    자막 + 상태 표시                   │
└─────────────────────────────────────────────────────────┘
```

### Supported Modes & Pipeline Architecture

Each communication mode is handled by a dedicated **pipeline** (Strategy pattern), enabling independent development and testing:

| Mode | Pipeline | Input | Output | For |
|------|----------|-------|--------|-----|
| **Voice → Voice** | `VoiceToVoicePipeline` | Speak your language | Translated speech + captions | General users |
| **Voice → Text** | `VoiceToVoicePipeline` (suppress audio) | Speak | Real-time captions only | Hearing disabilities |
| **Text → Voice** | `TextToVoicePipeline` | Type text | AI speaks for you via phone | Speech disabilities, phone anxiety |
| **Agent Mode** | `FullAgentPipeline` | Provide info upfront | AI handles entire call autonomously | Anyone |

---

## Architecture

```
┌──────────────────┐         ┌───────────────────────────────┐         ┌──────────────────┐
│                  │         │                               │         │                  │
│   Next.js Web    │◄──WS──►│       Relay Server            │◄──WS──►│  OpenAI Realtime  │
│   (Chat + Call   │         │       (FastAPI)               │         │  API (GPT-4o)    │
│    Monitor)      │         │                               │         │                  │
│                  │         │  ┌───────────┐ ┌───────────┐  │         └──────────────────┘
└──────────────────┘         │  │ Session A │ │ Session B │  │
                             │  │ User→Recv │ │ Recv→User │  │         ┌──────────────────┐
┌──────────────────┐         │  └───────────┘ └───────────┘  │◄──WS──►│  Twilio Media    │
│                  │         │                               │         │  Streams         │
│  React Native    │◄──WS──►│  ┌───────────┐ ┌───────────┐  │         │  (Phone Bridge)  │
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

### Why Dual Sessions?

A single translation session can't handle bidirectional conversation — it confuses translation direction. WIGVO runs **two simultaneous OpenAI Realtime sessions**:

- **Session A** (User → Recipient): Translates user speech into recipient's language, outputs via Twilio
- **Session B** (Recipient → User): Captures recipient speech from Twilio, translates back to user's language

This is the core architectural decision that makes real-time bidirectional phone translation possible.

---

## Key Technical Innovations

### Guardrail System — Translation Quality Verification

Real-time translation must balance speed and quality. Our 3-level system adds **zero latency** in 95%+ of cases:

| Level | Trigger | Action | Added Latency |
|-------|---------|--------|---------------|
| **L1** | Clean translation | Pass through | **0ms** |
| **L2** | Informal speech detected | TTS immediately, correct in background | **0ms** |
| **L3** | Profanity / harmful content | Block + filler audio + GPT-4o-mini correction | **~800ms** |

### Session Recovery — Zero-Downtime Resilience

OpenAI Realtime sessions can drop. Mid-call recovery is critical.

```
Normal ──► Heartbeat miss ──► Reconnect (exponential backoff)
                                    │
                              ┌─────┴─────┐
                              │           │
                         Success      Fail (10s)
                              │           │
                    Ring buffer        Degraded mode
                    catch-up           (Whisper batch
                    (unsent audio)      fallback)
```

- **Ring buffer**: 30-second circular buffer retains undelivered audio chunks
- **Catch-up**: On reconnect, unsent audio is batch-transcribed via Whisper and re-injected
- **Degraded mode**: After 10s failure, switches to Whisper STT + GPT-4o-mini translation

### Voice Activity Detection — Multi-Level

**Client-side VAD (Mobile App)**

The mobile app performs Voice Activity Detection locally, sending only speech frames to the server:

- RMS energy-based detection with configurable thresholds
- State machine: `SILENT -> SPEAKING -> COMMITTED`
- 300ms pre-speech ring buffer prevents onset clipping
- Reduces audio data sent to OpenAI by ~40%, directly cutting costs

**Server-side Local VAD (Silero + RMS)**

Session B runs a local Silero neural VAD on incoming Twilio audio, replacing OpenAI's built-in Server VAD which cannot handle PSTN noise:

- 2-stage pipeline: RMS energy gate (CPU-efficient) → Silero RNN (accurate)
- Hysteresis state machine prevents rapid on/off flicker
- 300ms debounce handles continuous speech with short inter-sentence pauses
- Requires mu-law→PCM decoding and 8kHz→16kHz upsampling for Silero compatibility
- **Result**: speech-end detection improved from 15-72s to 480ms

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| **Relay Server** | Python 3.12+, FastAPI, uvicorn | Async WebSocket handling, low latency |
| **Web App** | Next.js 16, React 19, shadcn/ui, Zustand | SSR, real-time UI updates |
| **Mobile App** | React Native, Expo SDK 54 | Cross-platform, expo-av for audio |
| **Realtime AI** | OpenAI Realtime API (GPT-4o) | Sub-second STT + Translation + TTS |
| **Chat AI** | GPT-4o-mini | Cost-efficient data collection |
| **Local VAD** | Silero VAD (lightweight RNN) | PSTN-grade speech detection |
| **Telephony** | Twilio (REST + Media Streams) | Reliable phone call infrastructure |
| **Database** | Supabase (PostgreSQL + Auth + RLS) | Real-time subscriptions, row-level security |
| **Place Search** | Naver Place Search API | Korean business directory |
| **Deploy** | Docker, Google Cloud Run | Auto-scaling, zero cold start |
| **Package Mgmt** | uv (Python), npm (Web/Mobile) | Fast, reliable dependency resolution |

---

## Project Structure

```
apps/
├── relay-server/                    # Python FastAPI — Real-time Translation Engine
│   ├── src/
│   │   ├── main.py                  # FastAPI entrypoint + lifespan
│   │   ├── call_manager.py          # Call lifecycle singleton (register/cleanup/shutdown)
│   │   ├── config.py                # pydantic-settings env config
│   │   ├── types.py                 # ActiveCall, CostTokens, WsMessage, etc.
│   │   ├── logging_config.py        # Structured logging setup
│   │   ├── middleware/              # HTTP middleware
│   │   │   └── rate_limit.py        # Rate limiting
│   │   ├── routes/                  # HTTP + WebSocket endpoints
│   │   │   ├── calls.py             # POST /calls/start, /calls/{id}/end
│   │   │   ├── stream.py            # WS /calls/{id}/stream (app ↔ relay)
│   │   │   └── twilio_webhook.py    # Twilio status callbacks
│   │   ├── realtime/                # OpenAI Realtime session management
│   │   │   ├── pipeline/            # Strategy pattern — mode-specific pipelines
│   │   │   │   ├── base.py          # BasePipeline ABC
│   │   │   │   ├── voice_to_voice.py # V2V + V2T (Echo Gate + Silence Injection)
│   │   │   │   ├── text_to_voice.py  # T2V (per-response instruction, text-only B)
│   │   │   │   └── full_agent.py     # Agent (function calling, autonomous)
│   │   │   ├── audio_router.py      # Thin delegator → pipeline selection
│   │   │   ├── local_vad.py         # Silero neural VAD + RMS energy gate
│   │   │   ├── echo_detector.py     # Pearson correlation echo detection (legacy, disabled)
│   │   │   ├── audio_utils.py       # Shared mu-law audio utilities
│   │   │   ├── session_manager.py   # Dual session orchestrator
│   │   │   ├── session_a.py         # User → Recipient translation
│   │   │   ├── session_b.py         # Recipient → User translation
│   │   │   ├── context_manager.py   # 6-turn sliding context window
│   │   │   ├── recovery.py          # Session failure recovery + degraded mode
│   │   │   └── ring_buffer.py       # 30s circular audio buffer
│   │   ├── guardrail/               # 3-level translation quality system
│   │   ├── tools/                   # Agent Mode function calling
│   │   ├── prompt/                  # System prompt templates + generator
│   │   └── db/                      # Supabase client
│   ├── tests/                       # 150+ pytest unit tests
│   │   ├── component/              # Module benchmarks (cost tracking, ring buffer perf)
│   │   ├── integration/            # Server-required tests (API, WebSocket)
│   │   ├── e2e/                    # End-to-end call tests (Twilio + OpenAI required)
│   │   └── run.py                  # Test runner (--suite, --test options)
│
├── web/                             # Next.js 16 — Chat Agent + Call Monitor
│   ├── app/
│   │   ├── page.tsx                 # Dashboard (chat + call interface)
│   │   ├── api/                     # 7 API routes (chat, calls, conversations)
│   │   ├── calling/[id]/            # Real-time call monitoring
│   │   └── result/[id]/             # Call result display
│   ├── lib/
│   │   ├── services/                # Chat pipeline (chat-service, place-matcher, data-extractor)
│   │   ├── supabase/                # SSR client + helpers
│   │   └── scenarios/               # Scenario prompts (restaurant, hospital, salon, etc.)
│   ├── hooks/                       # useChat, useCallPolling, useRelayWebSocket, useDashboard
│   ├── components/                  # chat/, call/, dashboard/, ui/ (shadcn)
│   └── shared/types.ts              # Canonical type definitions (Call, Conversation, CallRow)
│
└── mobile/                          # React Native (Expo) — VAD + Audio Client
    ├── app/                         # Expo Router (auth + main screens)
    ├── hooks/                       # useRealtimeCall, useClientVad, useAudioRecorder
    ├── components/call/             # RealtimeCallView, LiveCaptionPanel, VadIndicator
    └── lib/vad/                     # VAD core (processor, ring buffer, config)
```

---

## API Reference

### Relay Server

| Endpoint | Type | Description |
|----------|------|-------------|
| `POST /relay/calls/start` | HTTP | Initiate outbound call via Twilio |
| `POST /relay/calls/{id}/end` | HTTP | Terminate active call |
| `WS /relay/calls/{id}/stream` | WebSocket | Bidirectional audio/text stream |
| `POST /twilio/incoming` | HTTP | Twilio call status webhook |
| `WS /twilio/media-stream` | WebSocket | Twilio Media Stream audio bridge |
| `GET /health` | HTTP | Health check |

### Web App

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | AI conversation (GPT-4o-mini, scenario-based) |
| `/api/conversations` | GET/POST | List or create conversation sessions |
| `/api/conversations/[id]` | GET | Conversation details with messages |
| `/api/calls` | GET/POST | List or create call records |
| `/api/calls/[id]` | GET | Call details (status, result, summary) |
| `/api/calls/[id]/start` | POST | Trigger call via Relay Server |

---

## Database Schema

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `conversations` | scenario, status, collected_data (JSONB) | Chat sessions with extracted info |
| `messages` | role, content, metadata (JSONB) | User + AI messages |
| `calls` | status, result, call_sid, duration_s, total_tokens | Call lifecycle tracking |
| `conversation_entities` | entity_type, value, confidence | Structured data extraction |
| `place_search_cache` | query, results (JSONB), expires_at | Naver API response cache |

All tables enforce **Row Level Security** — users can only access their own data.

---

## Getting Started

### Prerequisites

- Python 3.12+ / Node.js 18+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [ngrok](https://ngrok.com/) (Twilio webhooks in dev)
- API keys: OpenAI, Twilio, Supabase, Naver (optional)

### Quick Start

```bash
# 1. Clone
git clone https://github.com/wigtn/wigvo-v2.git
cd wigvo-v2

# 2. Relay Server
cd apps/relay-server
cp .env.example .env          # Fill in API keys
uv sync
uv run uvicorn src.main:app --reload --port 8000

# 3. Web App (new terminal)
cd apps/web
cp .env.example .env.local    # Fill in API keys
npm install
npm run dev

# 4. ngrok (new terminal)
ngrok http 8000               # Copy URL to .env RELAY_SERVER_URL
```

### Testing

```bash
# Unit tests (150+ tests, no server needed)
cd apps/relay-server
uv run pytest -v

# Component tests (ring buffer perf, cost tracking)
uv run python -m tests.run --suite component

# Integration tests (requires running server)
uv run python -m tests.run --suite integration

# Individual test
uv run python -m tests.run --test cost

# E2E call test (requires Twilio + OpenAI keys)
uv run python -m tests.run --test call --phone +82... --scenario restaurant --auto
```

### Deployment

Both services are containerized and deploy to **Google Cloud Run**:

```bash
# Build & deploy via Cloud Build
gcloud builds submit --config=cloudbuild.yaml
```

| Service | Dockerfile | Cloud Run |
|---------|-----------|-----------|
| Relay Server | `apps/relay-server/Dockerfile` | Auto-scaling, WebSocket support |
| Web App | `apps/web/Dockerfile` | Next.js standalone output |

---

## Market Opportunity

| Segment | TAM (Korea) | Willingness to Pay |
|---------|------------|-------------------|
| Foreign residents | 2.2M (growing 8% YoY) | High — daily necessity |
| Overseas Koreans | 2.8M | Medium — occasional use |
| Disability services | Government-funded programs | Institutional contracts |
| Phone anxiety (Gen-Z) | ~4M estimated | Subscription model |

---

## License

All rights reserved.

---

<div align="center">

Built in 7 days with OpenAI Realtime API, Twilio, Supabase, and a lot of PSTN audio debugging.

</div>
