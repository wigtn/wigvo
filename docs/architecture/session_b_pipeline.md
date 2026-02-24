# Session B Pipeline: Callee → User (Inbound Translation)

## Overview

Session B는 수신자(Callee/Twilio)의 음성을 사용자(User/App)에게 번역하여 전달하는 **인바운드 번역 파이프라인**이다.
핵심 특징은 **3단계 오디오 필터링** (Echo Gate → Local VAD → Whisper STT)과 **2단계 자막** (원문 STT + 번역)이다.

## 전송 경로 비교 (Session A 문서 참조)

Session B의 오디오는 **양 끝이 서로 다른 전송 경로**를 사용한다:

| 구간 | 전송 방식 | 포맷 | 이유 |
|------|----------|------|------|
| **Callee → Relay** | Twilio PSTN Media Stream | g711_ulaw 8kHz | PSTN 전화망 표준 (ITU-T G.711) |
| **Relay → User** | WebSocket 직접 | pcm16 24kHz | App/Web 재생 — PSTN 제약 없음 |

## Audio Format Chain

```
Callee Phone ──PSTN──→ Twilio Media Stream ──[g711_ulaw 8kHz]──→ Relay Server
                                                                      │
                                ┌─────────────────────────────────────┘
                                │
                                ▼
                      ┌─────────────────────┐
                      │  3-Stage Filtering  │
                      │  1. Echo Gate       │  ← g711_ulaw 8kHz 기반 RMS 계산
                      │  2. Energy Gate     │  ← g711_ulaw 8kHz 기반 RMS 계산
                      │  3. Local VAD       │  ← 8kHz → ZOH → 16kHz → Silero
                      └──────────┬──────────┘
                                 │
                                 │ g711_ulaw 8kHz (real audio or 0xFF silence)
                                 ▼
                      OpenAI Session B (input: g711_ulaw 8kHz)
                           │
                           ├─ STT (Whisper): 수신자 원문 (Stage 1 자막)
                           ├─ Translation: target→source 번역
                           └─ TTS: pcm16 24kHz 출력 (또는 text-only)
                                  │
                                  ▼
                      Relay Server ──[pcm16 24kHz, WebSocket 직접]──→ User App (스피커 재생)
                                  └──[text caption, WebSocket 직접]──→ User App (자막 표시)
```

### Format 설정 (session_manager.py:345-357)

`pcm16`과 `g711_ulaw`는 **OpenAI Realtime API 포맷 식별자**로, 각각 고정된 샘플레이트를 내포한다:

| 포맷 식별자 | 의미 | 샘플레이트 | 비트 깊이 | BPS |
|------------|------|----------|----------|-----|
| `g711_ulaw` | ITU-T G.711 mu-law, mono | **8kHz** | 8-bit | 8,000 B/s |
| `pcm16` | 16-bit signed LE, mono | **24kHz** | 16-bit | 48,000 B/s |

```python
# STT 모델 선택: V2V는 whisper-1 (할루시네이션 블록리스트 호환), T2V/Agent는 gpt-4o-transcribe
stt_model = (
    settings.stt_model  # "gpt-4o-transcribe"
    if communication_mode in (CommunicationMode.TEXT_TO_VOICE, CommunicationMode.FULL_AGENT)
    else "whisper-1"
)

self.session_b = RealtimeSession(
    label="SessionB",
    config=SessionConfig(
        input_audio_format="g711_ulaw",   # Twilio PSTN 오디오: 8kHz G.711 mu-law (무변환 직접 전달)
        output_audio_format="pcm16",      # OpenAI API: 24kHz PCM16 → App 스피커 재생
        vad_mode=session_b_vad_mode,      # local_vad 시 turn_detection=null
        modalities=session_b_modalities,  # ["text", "audio"] 또는 ["text"]
        input_audio_transcription={"model": stt_model, "language": target_language},
    ),
)
```

### Input vs Output 비대칭 — 전송 경로에 따른 포맷 차이

| 방향 | 전송 경로 | 포맷 | 샘플레이트 | BPS |
|------|----------|------|----------|-----|
| **Input** (Callee → Session B) | PSTN → Twilio → Relay → OpenAI | g711_ulaw | 8kHz | 8,000 B/s |
| **Output** (Session B → User) | OpenAI → Relay → WebSocket → App | pcm16 | 24kHz | 48,000 B/s |

- **Input**: Callee는 PSTN 전화기를 사용하므로 ITU-T G.711 mu-law (8kHz) 제약. Twilio가 무변환으로 전달하고, OpenAI `g711_ulaw` 포맷과 정확히 일치.
- **Output**: User App은 WebSocket 직접 연결이므로 PSTN 제약 없음. OpenAI가 고품질 pcm16 (24kHz)로 TTS를 출력하여 App 스피커에서 재생.

#### 샘플레이트 주의사항

- **Input 경로**: Twilio PSTN 8kHz → OpenAI `g711_ulaw` 8kHz — **포맷 완벽 일치**, 무변환.
- **Output 경로**: OpenAI `pcm16` 24kHz 출력이지만, Mobile App은 16kHz WAV 헤더로 재생 (`useAudioPlayback.ts:22`).
  - 24kHz 데이터를 16kHz로 재생하면 **속도 ~0.67배, 피치 하강** 발생 가능.
  - Relay Server의 재생 시간 추정은 24kHz 기준 (`_PCM16_24K_BPS = 48,000`)으로 정확하지만, App 측 실제 재생과 차이가 있을 수 있음.
  - 이 불일치는 별도 확인이 필요함 (App을 24kHz로 변경하거나, Relay에서 24→16kHz 다운샘플링).
- Relay Server의 재생 시간 추정은 24kHz 기준 (`_PCM16_24K_BPS = 48,000`)으로 계산 (voice_to_voice.py:424).

## Pipeline 흐름

### 1. Twilio Audio Input + Echo Gate (voice_to_voice.py:294-348)

Twilio에서 들어오는 수신자 오디오는 3단계 필터를 거친다.

```
Twilio ──[g711_ulaw 20ms]──→ handle_twilio_audio()
                                   │
                                   ├─ ring_buffer_b.write()
                                   │
                                   ├─── Stage 0: Echo Gate (EchoGateManager) ───
                                   │    effective_audio = echo_gate.filter_audio(audio)
                                   │    if echo_gate.in_echo_window:
                                   │        rms = ulaw_rms(audio)
                                   │        if rms > 500 (echo_energy_threshold):
                                   │            → echo gate BREAK (실제 발화)
                                   │        else:
                                   │            → silence: bytes([0xFF] * len)
                                   │
                                   ├─── Stage 1-2: Local VAD 경로 ───
                                   │    if local_vad is not None:
                                   │        if NOT echo_gate.is_suppressing:
                                   │            → local_vad.process(effective_audio)
                                   │        if is_speaking AND NOT suppressed:
                                   │            → send real audio to Session B
                                   │        else:
                                   │            → send 0xFF silence to Session B
                                   │
                                   ├─── Legacy: Server VAD 경로 ───
                                   │    if echo_window → send silence
                                   │    elif rms < 150 (energy_gate) → drop
                                   │    else → send real audio
                                   │
                                   └─ ring_buffer_b.mark_sent()
```

#### Echo Gate 에너지 판별 로직 (EchoGateManager.filter_audio)

Echo Window 중에도 **수신자가 실제로 말하면** (RMS > 400) 즉시 게이트를 해제한다:

```python
# pipeline/echo_gate.py — EchoGateManager.filter_audio()
effective_audio = self.echo_gate.filter_audio(audio_bytes)
# 내부 로직:
#   if in_echo_window:
#       rms = _ulaw_rms(audio_bytes)
#       if rms > echo_energy_threshold_rms (500):  → gate break + 원본 반환
#       else:                                       → bytes([0xFF] * len) 반환
#   else: → 원본 반환
```

| 오디오 유형 | RMS 범위 | 처리 |
|------------|---------|------|
| 배경 소음 | 50-200 | 0xFF silence |
| TTS 에코 | 100-400 | 0xFF silence |
| **수신자 실제 발화** | **500-2000+** | **echo gate break → 실제 오디오 전달** |

### 2. Local VAD: 2-Stage Voice Activity Detection (local_vad.py)

Echo Gate를 통과한 오디오가 Local VAD로 전달된다.

```
                     ┌─────────── Stage 1: RMS Energy Gate ───────────┐
                     │                                                │
g711_ulaw 20ms ──→ ulaw_rms(audio)                                   │
                     │                                                │
                     ├─ RMS < 200 → SILENCE (Silero 스킵, CPU 절약)  │
                     │   └─ silence_count++ → min_silence_frames(15)  │
                     │       도달 시 SPEAKING→SILENCE 전환             │
                     │                                                │
                     └─ RMS ≥ 200 → Stage 2 진입                     │
                        └─ RMS silence 연속 5프레임 이상이면           │
                           Silero 모델 리셋 (깨끗한 상태에서 시작)     │
                     └────────────────────────────────────────────────┘

                     ┌─────────── Stage 2: Silero VAD ────────────────┐
                     │                                                │
                     │  mu-law → float32 (lookup table)               │
                     │  8kHz → 16kHz (np.repeat(samples, 2) ZOH)     │
                     │  Frame adapter: 320 samples → 512 버퍼링       │
                     │                                                │
                     │  Silero model: 512 samples (32ms) → prob       │
                     │                                                │
                     │  State Machine (hysteresis):                   │
                     │    SILENCE→SPEAKING: prob ≥ 0.5 × 3 frames    │
                     │    SPEAKING→SILENCE: prob < 0.35 × 15 frames  │
                     └────────────────────────────────────────────────┘
```

#### VAD State Machine 파라미터 (config.py 프로덕션 값)

| 파라미터 | 값 | 의미 |
|----------|-----|------|
| `local_vad_rms_threshold` | **200.0** | PSTN 배경소음(50-200) 위로 설정 |
| `local_vad_speech_threshold` | 0.5 | Silero speech 판정 확률 |
| `local_vad_silence_threshold` | 0.35 | Silero silence 판정 확률 |
| `local_vad_min_speech_frames` | **3** (96ms) | 오감지 방지: 3연속 speech 필요 |
| `local_vad_min_silence_frames` | 15 (480ms) | 음절 간 무음을 발화로 유지 |

#### Silero 입력 변환 체인

```
Twilio 20ms chunk (160 bytes, 160 samples @ 8kHz)
    ↓
ulaw_to_float32(): G.711 mu-law → float32 [-1.0, 1.0] (lookup table, 256 entries)
    ↓
np.repeat(samples, 2): 8kHz → 16kHz (Zero-Order Hold, 160→320 samples)
    ↓
Frame Adapter Buffer: 320 samples 축적 → 512 samples (32ms @ 16kHz) 도달 시 Silero 호출
    ↓
Silero VAD model.process(frame): → speech probability [0.0, 1.0]
```

#### VAD와 Echo Window의 상호작용

Echo Window 중에는 VAD 처리를 **완전히 건너뛴다**:

```python
vad_suppressed = self.echo_gate.is_suppressing  # = in_echo_window
if not vad_suppressed:
    await self.local_vad.process(effective_audio)
```

이유: 에코 노이즈가 speech로 오감지되는 것을 방지. Echo Window 종료 후에도 **Post-Echo Settling (2.0s)** 동안 VAD 처리를 계속 억제한다 (`is_suppressing = True`). AGC(자동 이득 제어) 복원 시 마이크 게인이 급등하면서 발생하는 노이즈가 speech로 오감지되는 것을 방지한다.

### 3. Session B에 오디오 전달

Local VAD의 판정에 따라:

| VAD 상태 | Echo 상태 | Session B에 전달 |
|----------|----------|------------------|
| SPEAKING | 비활성 | **실제 오디오** (Whisper STT 정확도 유지) |
| SILENCE | 비활성 | 0xFF silence (노이즈 축적 방지) |
| 무관 | Echo Window 중 | 0xFF silence (에코 차단) |

핵심: SILENCE 상태에서도 **0xFF silence를 전송**한다 (오디오를 DROP하지 않음). 이는 OpenAI Server VAD가 `speech_stopped` 이벤트를 자연스럽게 감지하도록 하기 위함이다.

### 4. Recipient Speech Detection (session_b.py:169-249)

#### Local VAD 모드 (프로덕션 기본)

```
LocalVAD SILENCE→SPEAKING ──→ _on_local_vad_speech_start()
                                   ↓
                              session_b.notify_speech_started()
                                   │
                                   ├─ session.clear_input_buffer()  # 축적된 무음/노이즈 제거
                                   ├─ _is_recipient_speaking = True
                                   ├─ _speech_started_at = now (E2E 레이턴시 기준점)
                                   ├─ debounce 취소 (연속 발화)
                                   ├─ silence_timeout 시작 (15s)
                                   └─ on_recipient_speech_started 콜백
                                        → _on_recipient_started() [Pipeline]
                                            ├─ echo window 활성이면 → 즉시 해제
                                            ├─ first_message 미전송이면 → first_message 처리
                                            ├─ [V2V] 이미 전송됨 → interrupt.on_recipient_speech_started()
                                            └─ [T2V] TTS 생성 중(is_generating) → interrupt 차단 (메시지 완전 전달 우선)
```

```
LocalVAD SPEAKING→SILENCE ──→ _on_local_vad_speech_end()
                                   ↓
                              session_b.notify_speech_stopped(peak_rms)
                                   │
                                   ├─ 최소 발화 길이 필터 (< 250ms → 무시 + buffer clear)
                                   ├─ Peak RMS 필터 (< 300 RMS → 노이즈/잔향 → 무시 + buffer clear)
                                   ├─ timeout 이미 강제 응답 생성 → skip
                                   ├─ on_recipient_speech_stopped 콜백
                                   │    → context_manager.inject_context(session_b)
                                   │    → interrupt.on_recipient_speech_stopped()
                                   └─ debounced_create_response() 시작 (300ms)
```

#### Debounced Response Creation (session_b.py:461-499)

```
speech_stopped ──[300ms debounce]──→ _debounced_create_response()
                                         │
                                         ├─ 이전 response 활성? → 완료 대기 (최대 5s)
                                         │   (conversation_already_has_active_response 방지)
                                         │
                                         ├─ Local VAD 모드:
                                         │   session.commit_audio_only() + session.create_response()
                                         │
                                         └─ Server VAD 모드:
                                             session.create_response() (자동 commit됨)
```

### 5. STT + Translation + TTS Output

OpenAI Session B가 처리를 완료하면 3종류의 이벤트를 발생시킨다:

```
OpenAI Session B
    │
    ├─ [input_audio_transcription.completed] ──→ Stage 1 자막 (원문 STT)
    │   "안녕하세요, 예약하고 싶은데요" (targetLanguage)
    │       ├─ Whisper 할루시네이션 필터 (15 패턴 blocklist, V2V whisper-1 전용)
    │       ├─ STT 레이턴시 임시 저장 (_pending_stt_ms)
    │       └─ App에 CAPTION_ORIGINAL 전송
    │
    ├─ [response.audio.delta] ──→ 번역 TTS 오디오 (pcm16 24kHz)
    │       └─ App에 RECIPIENT_AUDIO 전송
    │
    ├─ [response.audio_transcript.delta] ──→ Stage 2 자막 (번역 텍스트 스트리밍)
    │   "Hello, I'd like to make a reservation" (sourceLanguage)
    │       └─ App에 CAPTION_TRANSLATED 전송
    │
    └─ [response.audio_transcript.done] ──→ 번역 완료
            ├─ E2E 레이턴시 측정 (speech_started_at → now)
            ├─ STT latency와 E2E를 동시에 기록 (리스트 인덱스 정합성 보장)
            ├─ processing latency 기록 (speech_stopped → 번역 완료)
            ├─ transcript_bilingual 저장 (원문 + 번역)
            ├─ context_manager.add_turn("recipient", text)
            └─ App에 TRANSLATION_STATE: "caption_done" 전송
```

### 6. Session B 출력 큐 (voice_to_voice.py:415-490)

Session B 출력은 **큐 기반 순차 스트리밍**으로 관리된다:

```
SessionBHandler 콜백들 ──→ _b_output_queue (asyncio.Queue)
                                  │
                                  ▼
                          _drain_b_output() (Background Task)
                                  │
                                  ├─ "audio" → App에 RECIPIENT_AUDIO
                                  │   + _b_playback_total_bytes 축적
                                  │
                                  ├─ "caption" → App에 CAPTION_TRANSLATED
                                  │
                                  ├─ "original_caption" → App에 CAPTION_ORIGINAL
                                  │
                                  └─ "caption_done" → 응답 경계
                                       └─ 클라이언트 재생 완료 추정 대기:
                                          remaining = (total_bytes / 48000) - elapsed
                                          asyncio.sleep(remaining)
```

#### App 재생 시간 추정 (pcm16 24kHz)

```python
_PCM16_24K_BPS = 48_000  # bytes per second (24kHz × 2 bytes/sample)
audio_duration_s = self._b_playback_total_bytes / _PCM16_24K_BPS
remaining = max(audio_duration_s - elapsed, 0)
```

Session A의 Echo Cooldown(`total_bytes / 8000`)과 동일한 원리이지만, 포맷이 pcm16 24kHz이므로 BPS가 다르다:
- **Session A (Twilio)**: g711_ulaw 8kHz → 8,000 B/s
- **Session B (App)**: pcm16 24kHz → 48,000 B/s

### 7. Output Suppression (Echo Gate v2)

Session A TTS가 재생 중일 때 Session B의 출력을 **억제**하여 에코 아티팩트를 방지:

```
echo window 활성화 → session_b.output_suppressed = True
                         │
                         ├─ audio/caption/original_caption → pending_output 큐에 저장
                         │
echo window 종료 → session_b.output_suppressed = False
                         │
                         ├─ pending_output → flush_pending_output()  # 정상 출력 배출
                         └─ 또는 → clear_pending_output()            # 에코 환각이면 폐기
```

## Safety Nets

### Silence Timeout (15s)

```
speech_started ──[15s 대기]──→ _silence_timeout_handler()
                                    │
                                    ├─ _is_recipient_speaking = False
                                    ├─ _timeout_forced = True
                                    ├─ commit_audio_only() (Local VAD 모드)
                                    └─ create_response()
```

배경소음이 Server VAD를 "speaking" 상태로 영구 고정시키는 경우에 대한 안전망.

### Min Speech Duration (250ms)

`speech_duration < 250ms`인 segment는 노이즈로 간주하고 무시 + buffer clear.
한국어 짧은 단어("여보세요?" ~290ms)는 통과, 순수 노이즈(<200ms)는 차단.

### Peak RMS Quality Filter

`peak_rms < 300` (session_b_min_peak_rms)인 speech는 에너지가 약하므로 노이즈/잔향으로 간주하고 무시.
조용한 PSTN 발화(200-500 RMS)도 통과하도록 설정.

### Whisper Hallucination Blocklist (15 patterns)

무음/저에너지 구간에서 Whisper가 생성하는 한국어 방송 뉴스 패턴을 차단:

```python
_STT_HALLUCINATION_BLOCKLIST = frozenset({
    "MBC 뉴스 이덕영입니다",
    "시청해주셔서 감사합니다",
    "구독과 좋아요 부탁드립니다",
    "밝혔습니다", "전해드립니다",
    "플러스포어 픽업",
    # ... 총 15개 패턴
})
```

## Session B Latency 측정

### E2E Latency (speech_started → translation_done)

```
수신자 발화 시작 (speech_started_at)
    ↓
  [VAD 감지] + [Debounce 300ms] + [Whisper STT] + [GPT Translation] + [TTS 생성]
    ↓
번역 완료 (response.audio_transcript.done)
```

평가 결과 (paper_metrics.json, N=207):
- **평균**: 2,249ms
- **P50**: 1,994ms
- **P95**: 4,667ms

### STT Latency (speech_started → original STT)

평가 결과 (paper_metrics.json, N=189):
- **평균**: 1,537ms
- **P50**: 1,226ms
- **P95**: 5,116ms

### STT/E2E 메트릭 정합성

STT latency는 `_handle_stt_transcript()`에서 계산되고, E2E latency는 `_save_transcript_and_notify()`에서 계산된다.
두 이벤트의 발생 시점이 다르므로 `_pending_stt_ms` 패턴으로 **동시에 리스트에 기록**하여 인덱스 정합성을 보장한다:

```python
# STT handler: 임시 저장만
self._pending_stt_ms = stt_ms

# E2E handler: STT와 E2E를 동시에 append
call_metrics.session_b_e2e_latencies_ms.append(e2e_ms)
call_metrics.session_b_stt_latencies_ms.append(self._pending_stt_ms or e2e_ms)
self._pending_stt_ms = 0.0
```

`[unclear]` 필터에서 E2E 기록을 건너뛸 때 `_pending_stt_ms`도 함께 폐기하여 리스트 길이 불일치를 방지한다.

### 추가 메트릭

| 메트릭 | 구간 | 용도 |
|--------|------|------|
| `session_b_speech_durations_ms` | speech_started → speech_stopped | 수신자 발화 길이 (노이즈 필터 통과 후) |
| `session_b_processing_latencies_ms` | speech_stopped → 번역 완료 | 처리 지연 (STT와 독립적) |
| `session_b_stt_after_stop_ms` | speech_stopped → STT 완료 | STT가 speech 종료 후 얼마나 더 걸렸는지 |

## 2단계 자막 시스템 (PRD 5.4)

```
Stage 1 (원문 STT):
  input_audio_transcription.completed → CAPTION_ORIGINAL
  "안녕하세요, 예약하고 싶은데요"
  → 수신자가 뭐라고 했는지 즉시 확인 가능

Stage 2 (번역):
  response.audio_transcript.delta → CAPTION_TRANSLATED (스트리밍)
  response.audio_transcript.done → 완료
  "Hello, I'd like to make a reservation"
  → 번역이 완료되면 최종 텍스트 표시
```

App은 Stage 1 자막을 먼저 표시하고, Stage 2 번역이 도착하면 함께 표시한다.

## text-only 모드 (modalities=['text'])

Text-to-Voice 파이프라인에서 Session B는 `modalities=["text"]`로 설정:
- Server VAD 비활성화 (turn_detection 자동 off)
- TTS 출력 없음 → 텍스트 번역만
- `response.text.delta`/`response.text.done` 이벤트 사용 (`text` 필드, NOT `transcript`)

## 상수/설정값 요약

| 파라미터 | 프로덕션 값 | 소스 |
|----------|------------|------|
| Input format | g711_ulaw 8kHz | session_manager.py:351 |
| Output format | pcm16 24kHz | session_manager.py:352 |
| Output BPS | 48,000 B/s | voice_to_voice.py:424 |
| Local VAD RMS threshold | 200.0 | config.py:94 |
| Silero speech threshold | 0.5 | config.py:95 |
| Silero silence threshold | 0.35 | config.py:96 |
| Min speech frames | 3 (96ms) | config.py:97 |
| Min silence frames | 15 (480ms) | config.py:98 |
| Silero frame size | 512 samples (32ms) | local_vad.py:53 |
| Silero sample rate | 16kHz | local_vad.py:54 |
| Input sample rate | 8kHz | local_vad.py:55 |
| Min RMS silence for Silero reset | 5 frames (100ms) | local_vad.py:57 |
| Response debounce | 300ms | session_b.py:99 |
| Silence timeout | 15s | session_b.py:104 |
| Min speech duration | 250ms | config.py:92 |
| Peak RMS quality filter | 300 RMS | config.py:109 |
| Echo energy threshold | 500 RMS | config.py:108 |
| Energy gate threshold | 150 RMS | config.py:107 |
| Echo post settling | 2.0s | config.py:87 (AGC 안정화 대기) |
| Hallucination blocklist | 15 patterns | session_b.py:24-40 (V2V whisper-1 전용) |
| STT model (V2V) | whisper-1 | session_manager.py (할루시네이션 블록리스트 호환) |
| STT model (T2V/Agent) | gpt-4o-transcribe | session_manager.py (settings.stt_model) |
| Server VAD threshold | 0.8 | config.py:87 |
| Server VAD silence ms | 500ms | config.py:88 |
| Server VAD prefix padding | 300ms | config.py:89 |
