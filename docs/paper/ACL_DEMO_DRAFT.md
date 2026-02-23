# WIGVO: A Server-Side LLM-Based Real-Time Speech Translation Bridge for Legacy PSTN Calls

> **Target**: ACL 2026 System Demonstrations
> **Status**: Draft (Sections 1-3, 6)
> **TODO**: Section 4 (Local VAD & Robustness), Section 5 (Evaluation), References, Figures

---

## 1 Introduction

Recent advances in large language models (LLMs) have enabled high-quality speech-to-speech translation in controlled environments such as mobile apps, web browsers, and video conferencing platforms. However, billions of users still rely on legacy Public Switched Telephone Network (PSTN) infrastructure, where deploying LLM-based translation is significantly more challenging due to limited control over devices, audio capture, and network behavior. Existing commercial solutions either depend on specific carriers (e.g., telco-native services) or on premium on-device hardware, leaving ordinary landlines and low-end smartphones largely unsupported.

In this work, we present **WIGVO**, a server-side relay system that brings LLM-based real-time speech translation to any standard phone call without requiring app installation, carrier support, or specialized hardware. WIGVO acts as a web-to-PSTN bridge: a caller connects via a browser, WIGVO relays media streams between Twilio and OpenAI's Realtime API, and the callee participates through an ordinary phone number on the PSTN. The system is designed to be **carrier-agnostic** and **device-agnostic**, enabling practical deployment in real-world telephony settings, including accessibility-focused scenarios such as text-to-voice and voice-to-text relay for users with speech or hearing impairments.

Deploying LLM-based translation over PSTN presents several unique technical challenges. First, the relay server cannot rely on client-side acoustic echo cancellation (AEC) or high-fidelity microphones. Instead, it must handle low-bandwidth G.711 μ-law audio at 8 kHz with unpredictable latency and jitter. Second, naive use of a single translation session for both speakers leads to direction confusion and self-translation loops, where the model accidentally translates its own synthesized speech. Third, background noise and low-energy PSTN artifacts often trigger hallucinated subtitles or TTS outputs from the underlying speech recognition and translation stack.

WIGVO addresses these challenges through three key design decisions:
(1) a **Dual-Session Architecture** that assigns independent Realtime sessions to each speaker direction (caller→callee and callee→caller) to avoid context mixing;
(2) a **server-side Echo Gating mechanism** based on deterministic silence injection and dynamic RMS thresholds, which suppresses acoustic echo loops without client control; and
(3) a **modular pipeline design** that supports multiple communication modes (voice-to-voice, text-to-voice, voice-to-text, and agent modes) on top of a shared audio routing core.

This paper describes the design and implementation of WIGVO and demonstrates its applicability through realistic telephone scenarios, including a restaurant reservation and a medical appointment with text-to-voice relay. We also report latency measurements from real PSTN calls and ablation-style observations on echo handling and robustness. **Our goal is not to propose a new LLM, but to show how existing commercial LLMs can be made usable in the harsh conditions of legacy telephony through careful system design.**

---

## 2 System Overview

WIGVO is implemented as a FastAPI-based relay server that connects three main endpoints: a browser or app client, Twilio's Media Streams gateway, and two concurrent OpenAI Realtime sessions. Figure 1 shows the high-level architecture.

**Client side.** The user interacts with WIGVO through a web application, which supports voice input, text input (for text-to-voice relay), and caption display. Audio is sent to the relay over a WebSocket in 16 kHz PCM16 format; captions and control messages are exchanged as JSON.

**Relay server.** At the core of the relay, an **AudioRouter** component implements a Strategy Pattern to dispatch audio and text events to different pipelines depending on the communication mode (e.g., voice-to-voice, text-to-voice, full-agent). Each pipeline manages the lifecycle of two OpenAI Realtime sessions -- **Session A** for user→recipient translation, and **Session B** for recipient→user -- plus PSTN audio from Twilio. The relay also hosts cross-cutting components such as a Guardrail module (for text filtering and hallucination mitigation), a Context Manager (sliding window of recent turns), a Local VAD module, and recovery logic for reconnection and catch-up.

**LLM backend.** For each call, WIGVO opens two independent WebSocket connections to OpenAI's Realtime API. Session A receives user-side PCM16 audio and produces translated G.711 μ-law audio for Twilio, while Session B receives Twilio's G.711 μ-law audio and produces PCM16 audio or text back to the browser. Both sessions share a structured system prompt but maintain separate context buffers and event streams, ensuring directional clarity.

**Telephony gateway.** On the telephony side, WIGVO uses Twilio REST APIs to initiate outbound PSTN calls and Twilio Media Streams to exchange audio frames with the recipient. Twilio sends and receives audio in G.711 μ-law at 8 kHz via WebSocket, which WIGVO converts to and from 16 kHz PCM16 for the LLM backend. The relay is thus fully decoupled from any specific carrier or device and treats the PSTN as a media transport layer.

Figure 2 illustrates the call flow: the client first calls `/relay/calls/start`, WIGVO creates an `ActiveCall`, opens the two Realtime sessions, and triggers a Twilio outbound call. Once the user and Twilio both establish WebSocket connections, the AudioRouter starts streaming media between the browser, OpenAI sessions, and Twilio, while applying echo gating, VAD, and guardrails as described in the next section.

---

## 3 Core Design Choices

### 3.1 Dual-Session Architecture

A straightforward implementation might route both speakers' audio into a single Realtime session and rely on the model to distinguish directions. In practice, we observed that such a setup frequently leads to **translation direction confusion** and **self-translation loops**, where the model starts translating its own TTS output as new input. This is especially problematic in full-duplex telephone conversations, where interruptions and overlaps are common.

To avoid this, WIGVO uses **two physically separate Realtime sessions per call**:

- **Session A (User→Recipient).**
  - Input: user audio from the browser (PCM16, 16 kHz).
  - Output: translated audio in G.711 μ-law, streamed to Twilio and played to the recipient.
  - Responsibilities: translation from source to target language, guardrails on outgoing content, and context tracking for the user's perspective.

- **Session B (Recipient→User).**
  - Input: recipient audio from Twilio (G.711 μ-law, 8 kHz), converted to PCM16.
  - Output: translated PCM16 audio and/or text (for captions) back to the browser.
  - Responsibilities: translation in the opposite direction, local VAD and echo gating on incoming PSTN audio, and interrupt handling to prioritize recipient speech.

The two sessions share a high-level conversation state (e.g., languages, mode, scenario metadata) but never see each other's TTS outputs as inputs. This separation significantly reduces hallucinations such as "the customer says they would like to..." when the user actually spoke in first-person, and stabilizes translation behavior under overlapping speech.

### 3.2 Server-Side Echo Gating with Silence Injection

PSTN calls are highly susceptible to acoustic echo: audio played on the recipient's handset leaks back into the microphone and travels through Twilio to the relay, where it may be misinterpreted as new speech. In early versions, we attempted to detect echo using a Pearson-correlation-based audio fingerprinting approach, comparing outgoing TTS buffers with incoming audio chunks. While this worked in controlled tests, it failed in real PSTN conditions due to G.711 μ-law nonlinear quantization, variable round-trip delays (80-600 ms jitter), and background noise that occasionally produced high correlation scores. As a result, the correlation-based EchoDetector was disabled in production.

Instead, WIGVO adopts a **deterministic silence injection strategy** we call **Echo Gate v2**:

1. **Echo window activation.** When Session A begins streaming TTS audio to Twilio, the pipeline marks an "echo window" as active and computes an expected playback duration based on the number of bytes and sampling rate.
2. **Silence injection.** During the echo window, incoming Twilio audio is not passed through to Session B. Instead, it is replaced with μ-law silence frames (0xFF), effectively blocking the echo at the server level while still allowing VAD to see "silence" and end-of-speech boundaries naturally.
3. **Dynamic cooldown.** After the TTS stream finishes, a cooldown period proportional to the TTS duration (e.g., 0.8 s for short prompts, ~3 s for longer sentences) is applied to account for PSTN jitter and residual echo. Only after this cooldown does the pipeline resume forwarding Twilio audio to Session B.

We also employ a **dynamic RMS threshold** to distinguish low-energy echo from genuine speech. In practice, PSTN echo tends to fall in the 100-400 RMS range, whereas real speech aligns around 500-2000+ RMS. WIGVO uses a higher threshold during echo windows to aggressively suppress likely echo, and a lower gate (e.g., 150 RMS) outside echo windows to filter background noise without cutting off real speech.

This combination of silence injection and dynamic RMS gating proved robust against codec distortion and network jitter, eliminating echo translation loops in our internal tests with real PSTN lines. **[TODO: Insert concrete numbers, e.g., "0/N calls exhibited echo loops with Echo Gate v2 enabled, compared to N/N without it."]**

### 3.3 Pipeline Strategy and Accessibility Modes

Beyond standard voice-to-voice translation, WIGVO supports multiple interaction modes through a **pipeline Strategy Pattern**:

- **Voice-to-Voice (V2V):** full bidirectional speech translation with audio in both directions.
- **Text-to-Voice (T2V):** user types text; Session A generates TTS for the recipient, while Session B returns captions only.
- **Voice-to-Text (VTT):** user or recipient speaks; only text captions are shown, useful for hearing-impaired users.
- **FullAgent:** an experimental mode where the LLM acts as an autonomous agent, using function calling to perform actions on behalf of the caller.

Each pipeline shares the same underlying AudioRouter and DualSessionManager but configures different modalities for Session A/B (e.g., suppressing B-side audio in T2V) and different guardrail behaviors. This modularity allows WIGVO to implement accessibility-focused scenarios -- such as a speech-anxious user booking a medical appointment via text-to-voice relay -- without changing the core telephony and echo-handling logic.

---

## 4 Local VAD and Robustness

> **[TODO: Write this section]**
>
> Topics to cover:
> - Why OpenAI Server VAD fails on PSTN (constant background noise, stuck "speaking" state)
> - 2-stage Local VAD: RMS Energy Gate (200 RMS threshold) → Silero RNN (8kHz→16kHz frame adapter)
> - Hysteresis state machine: SILENCE→SPEAKING (3×32ms=96ms), SPEAKING→SILENCE (15×32ms=480ms)
> - STT hallucination blocklist (Whisper outputs "MBC 뉴스 이덕영입니다" on low-energy input)
> - 3-level interrupt priority: Recipient > User > AI
> - Session recovery: exponential backoff + ring buffer audio catchup + degraded mode (Whisper batch)
> - 6-turn conversation context sliding window (~200 tokens per injection)

---

## 5 Evaluation

> **[TODO: Collect data and write this section]**
>
> ### 5.1 Latency Measurements
>
> Data source: `calls.call_result_data->'metrics'` from Supabase
>
> Metrics to report:
> - `session_a_latencies_ms`: User speech commit → First TTS chunk to Twilio (avg ± std, N calls)
> - `session_b_e2e_latencies_ms`: Recipient speech start → Translation text complete (avg ± std)
> - `first_message_latency_ms`: Pipeline start → First TTS chunk
>
> Supabase query:
> ```sql
> SELECT
>   call_result_data->'metrics'->'session_a_latencies_ms' as a_latencies,
>   call_result_data->'metrics'->'session_b_e2e_latencies_ms' as b_latencies,
>   call_result_data->'metrics'->'first_message_latency_ms' as first_msg,
>   call_result_data->'metrics'->'turn_count' as turns,
>   call_result_data->'metrics'->'echo_suppressions' as echo_count,
>   call_result_data->'cost_usd' as cost,
>   duration_s,
>   communication_mode
> FROM calls
> WHERE call_result_data->'metrics' IS NOT NULL
> ORDER BY created_at DESC
> LIMIT 20;
> ```
>
> ### 5.2 Echo Gate Ablation
>
> Config flags for ablation:
> - Full system (default): all enabled
> - `-Echo Gate`: bypass `_in_echo_window` logic
> - `-Local VAD`: set `local_vad_enabled=False` (Server VAD fallback)
> - `-Context Window`: set context turns to 0
> - `-STT Blocklist`: disable guardrail
>
> Table template:
>
> | Configuration | Echo Loops (N/total) | False VAD Triggers | Avg Latency (ms) | Hallucinations |
> |---------------|---------------------|-------------------|-------------------|----------------|
> | Full system   | [TODO]              | [TODO]            | [TODO]            | [TODO]         |
> | -Echo Gate    | [TODO]              | [TODO]            | [TODO]            | [TODO]         |
> | -Local VAD    | [TODO]              | [TODO]            | [TODO]            | [TODO]         |
> | -Context      | [TODO]              | [TODO]            | [TODO]            | [TODO]         |
> | -Blocklist    | [TODO]              | [TODO]            | [TODO]            | [TODO]         |
>
> ### 5.3 Cost Analysis
>
> CostTokens pricing (per 1K tokens):
> - audio_input: $0.06, audio_output: $0.24
> - text_input: $0.005, text_output: $0.02
>
> Report: average cost per call by mode and duration

---

## 6 Demonstration Scenarios

We plan to demonstrate WIGVO through two realistic PSTN scenarios.

### 6.1 Restaurant Reservation (Voice-to-Voice Relay)

In the main demo, an English-speaking user calls a Korean restaurant using WIGVO. The user connects via a browser, while the restaurant answers on a standard landline.

1. The restaurant picks up: "여보세요, 강남면옥입니다."
   Session B translates this into English and plays "Hello, this is Gangnam Myeonok" to the user, while also displaying captions.
2. The user says: "Hi, I'd like to make a reservation for four people tomorrow at 7 PM."
   Session A translates and plays "안녕하세요, 내일 저녁 7시에 4명 예약하고 싶습니다" to the restaurant.
3. The restaurant replies: "네, 확인해볼게요. 성함이 어떻게 되세요?"
   Session B returns "Sure, let me check. What's your name?" to the user.
4. The user answers: "John Smith, and could we get a window seat?"
   Session A plays "존 스미스요, 창가 자리 가능할까요?" to the restaurant.
5. The restaurant confirms: "네, 창가 자리 예약 도와드리겠습니다."
   Session B delivers "Yes, I'll help you reserve a window seat."

This scenario highlights WIGVO's ability to handle real PSTN audio, maintain directional context via dual sessions, and suppress echo loops while keeping latency within a practical range for natural conversation. **[TODO: Insert measured average latency per turn, e.g., "average A→B latency ~X ms, B→A latency ~Y ms across N turns."]**

### 6.2 Medical Appointment with Text-to-Voice Relay

In the second demo, a Korean user with speech anxiety or a stutter books a medical appointment using the **text-to-voice** mode. The user types messages in Korean; WIGVO converts them to natural spoken Korean for the clinic, and displays incoming speech as text captions.

1. The user types: "안녕하세요, 금요일 오후 2시 예약 가능한가요?"
   Session A uses an "exact utterance" prompt to ensure the TTS says exactly this sentence and nothing else to the clinic staff.
2. The clinic asks: "네, 어떤 진료과로 예약하시나요?"
   Session B transcribes and shows this as text to the user.
3. The user types: "내과 진료요, 두통이 계속 있어서요."
   Session A synthesizes and plays this sentence to the clinic.
4. The clinic confirms: "김민지 님으로 금요일 오후 2시 내과 접수해드릴게요."
   Session B displays this confirmation as text.

This scenario demonstrates WIGVO's role as an accessibility tool: users who are unable or unwilling to speak on the phone can still complete high-stakes tasks such as medical bookings through a combination of text input and speech output. **[TODO: Insert anecdotal or pilot-user feedback, if available.]**

---

## 7 Related Work

> **[TODO: Write this section]**
>
> Topics to cover:
> - Real-time speech translation systems (Meta SeamlessM4T, Google Translate, etc.)
> - Telephony AI systems (Google Duplex, telco-native solutions)
> - Echo cancellation in VoIP (WebRTC AEC, speex)
> - Accessibility communication tools (relay services, TTY/TDD)
> - LLM-based voice agents (OpenAI Realtime API, LiveKit)

---

## 8 Conclusion and Future Work

> **[TODO: Write after evaluation data is collected]**

---

## References

> **[TODO: Add references]**

---

## Appendix: Data Collection Guide

### How to extract latency data from production

```sql
-- Supabase SQL Editor
SELECT
  call_id,
  duration_s,
  communication_mode,
  call_result_data->'metrics'->'session_a_latencies_ms' as a_latencies,
  call_result_data->'metrics'->'session_b_e2e_latencies_ms' as b_latencies,
  call_result_data->'metrics'->'first_message_latency_ms' as first_msg_ms,
  call_result_data->'metrics'->'turn_count' as turns,
  call_result_data->'metrics'->'echo_suppressions' as echo_count,
  call_result_data->'cost_usd' as cost_usd,
  total_tokens
FROM calls
WHERE call_result_data->'metrics' IS NOT NULL
  AND status = 'COMPLETED'
ORDER BY created_at DESC
LIMIT 20;
```

### Config flags for ablation experiments

```python
# Full system (control)
local_vad_enabled = True
echo_gate_enabled = True  # (controlled via _in_echo_window logic)
audio_energy_gate_enabled = True

# -Local VAD
local_vad_enabled = False  # Falls back to Server VAD

# -Echo Gate
# Bypass: comment out _activate_echo_window() call in pipeline

# -Context Window
# Set ConversationContextManager.MAX_TURNS = 0

# -STT Blocklist
# Empty _STT_HALLUCINATION_BLOCKLIST in session_b.py
```

### Architecture diagram assets needed

- Fig.1: High-level architecture (Browser ↔ Relay ↔ OpenAI ↔ Twilio ↔ PSTN)
- Fig.2: Call flow sequence diagram (time-ordered message passing)
- Fig.3: Echo Gate v2 timing diagram (TTS window → silence injection → cooldown)
- Fig.4: Local VAD 2-stage pipeline (RMS gate → Silero → state machine)
