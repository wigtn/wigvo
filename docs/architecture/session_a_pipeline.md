# Session A Pipeline: User → Callee (Outbound Translation)

## Overview

Session A는 사용자(User/App)의 음성을 수신자(Callee/Twilio)에게 전달하는 **아웃바운드 번역 파이프라인**이다.
핵심 특징은 OpenAI Realtime API가 `g711_ulaw` 포맷으로 직접 출력하여 Twilio로 **무변환(zero-conversion)** 전달하는 것이다.

## 두 가지 오디오 경로

WIGVO의 핵심 아키텍처: **User와 Callee는 서로 다른 전송 경로**를 사용한다.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│   User (App/Web)                                    Callee (전화기)         │
│       │                                                  │                  │
│       │ WebSocket 직접 연결                          PSTN 전화망            │
│       │ (PCM16 16kHz)                              (g711_ulaw 8kHz)         │
│       │                                                  │                  │
│       ▼                                                  ▼                  │
│   ┌────────────────── Relay Server ──────────────────────┐                  │
│   │                                                      │                  │
│   │  Session A: User(PCM16) → OpenAI → TTS(g711) → Twilio → Callee        │
│   │  Session B: Callee → Twilio(g711) → OpenAI → TTS(pcm16) → User        │
│   │                                                      │                  │
│   └──────────────────────────────────────────────────────┘                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

| 경로 | 전송 방식 | 오디오 포맷 | 샘플레이트 | 제약 |
|------|----------|-----------|----------|------|
| **User → Relay** | WebSocket 직접 | PCM16 | 16kHz | 디바이스 마이크 의존 (노트북, 이어폰 등) |
| **Relay → Callee** | Twilio Media Stream → PSTN | g711_ulaw | 8kHz | PSTN 표준 (ITU-T G.711) |
| **Callee → Relay** | PSTN → Twilio Media Stream | g711_ulaw | 8kHz | PSTN 표준 (ITU-T G.711) |
| **Relay → User** | WebSocket 직접 | PCM16 | 24kHz | OpenAI API 출력 기본값 |

**User 오디오는 Twilio를 거치지 않는다.** App/Web이 디바이스 마이크로 직접 녹음(16kHz PCM16)하여 WebSocket으로 Relay Server에 전달한다. Twilio PSTN 8kHz 제약은 Callee 경로에만 적용된다.

## Audio Format Chain (Session A)

```
User App/Web (마이크)
    │
    │ PCM16 16kHz — WebSocket 직접 전송 (Twilio 미경유)
    ▼
Relay Server ──────────── WebSocket ──────────→ OpenAI Session A
                                                     │
                                                     │ input: pcm16 (API 스펙: 24kHz)
                                                     │ → App 16kHz와 불일치, API 내부 리샘플링
                                                     │
                                                     │ output: g711_ulaw 8kHz
                                                     ▼
                          Relay Server ←── TTS audio (base64 g711_ulaw)
                               │
                               │ NO CONVERSION — raw g711_ulaw bytes forwarded
                               ▼
                          Twilio Media Stream ──PSTN──→ Callee Phone (8kHz)
```

### Format 설정 (session_manager.py:330-337)

`pcm16`과 `g711_ulaw`는 **OpenAI Realtime API 포맷 식별자**로, 각각 고정된 샘플레이트를 내포한다:

| 포맷 식별자 | 의미 | 샘플레이트 | 비트 깊이 | BPS |
|------------|------|----------|----------|-----|
| `pcm16` | 16-bit signed LE, mono | **24kHz** (API 스펙) | 16-bit | 48,000 B/s |
| `g711_ulaw` | ITU-T G.711 mu-law, mono | **8kHz** | 8-bit | 8,000 B/s |

```python
# STT 모델 선택: V2V는 whisper-1 (할루시네이션 블록리스트 호환), T2V/Agent는 gpt-4o-transcribe
stt_model = (
    settings.stt_model  # "gpt-4o-transcribe"
    if communication_mode in (CommunicationMode.TEXT_TO_VOICE, CommunicationMode.FULL_AGENT)
    else "whisper-1"
)

self.session_a = RealtimeSession(
    label="SessionA",
    config=SessionConfig(
        input_audio_format="pcm16",       # User App → WebSocket → Relay → OpenAI (App 녹음: 16kHz)
        output_audio_format="g711_ulaw",  # OpenAI TTS → Relay → Twilio → PSTN → Callee (8kHz)
        vad_mode=vad_mode,                # client VAD 시 turn_detection=null
        input_audio_transcription={"model": stt_model, "language": source_language},
    ),
)
```

#### 샘플레이트 불일치와 리샘플링

- **Input**: App이 16kHz PCM16를 전송하지만, OpenAI `pcm16`은 24kHz를 기대한다.
  - OpenAI API 내부에서 리샘플링이 발생하며, 이 과정에서 **추가 레이턴시**가 발생할 수 있다.
  - 단, Whisper STT는 16kHz 학습 모델이므로 16kHz→16kHz는 변환이 최소화되고, 24kHz→16kHz보다 유리할 수 있다.
  - 향후 최적화: App 녹음을 24kHz로 변경하면 API 내부 리샘플링을 제거 가능.
- **Output**: `g711_ulaw`는 PSTN과 동일한 8kHz이므로 Twilio로 **무변환 직접 전달**.

### Zero-Conversion 설계 근거

일반적인 VoIP 시스템은 고품질 오디오(PCM16 24kHz)를 출력한 후 Twilio 전송 전에 G.711 mu-law 인코딩을 수행한다.
WIGVO는 OpenAI에게 `g711_ulaw`를 **직접 출력**하도록 지시하여:

1. **서버 CPU 절약**: PCM→G.711 인코딩 불필요
2. **레이턴시 감소**: 서버 측 포맷 변환 단계 제거
3. **품질 일관성**: OpenAI가 G.711의 제약(8kHz, 8-bit)을 인지하고 TTS를 최적화
4. **바이트 기반 재생 시간 추정**: G.711의 1byte=1sample 특성 활용 → `total_bytes / 8000 = 재생 시간(초)`

## Pipeline 흐름 (Voice-to-Voice)

### 1. User Audio Input (voice_to_voice.py:240-259)

```
App ──[PCM16 16kHz base64]──→ handle_user_audio()
                                  │
                                  ├─ base64 decode
                                  ├─ ring_buffer_a.write() (Recovery용 버퍼링)
                                  ├─ RMS logging (~1초마다)
                                  ├─ recovery 중이면 return (또는 degraded mode)
                                  └─ session_a.send_user_audio(audio_b64)
```

- **Ring Buffer**: 세션 연결 끊김 시 Recovery Manager가 버퍼에서 미전송 오디오를 재전송
- **RMS 로깅**: 10 chunk마다 (`_user_audio_chunk_count % 10`) PCM16 RMS 계산으로 입력 레벨 모니터링

### 2. User Audio Commit (voice_to_voice.py:261-271)

Client VAD 모드(`turn_detection=null`)에서는 App이 발화 종료를 판단하고 commit을 요청한다.

```
App ──[commit 신호]──→ handle_user_audio_commit()
                           │
                           ├─ translation_state: "processing" → App에 전송
                           ├─ context_manager.inject_context(session_a)
                           └─ session_a.commit_user_audio()
```

- **Context Injection**: `conversation.item.create`로 최근 6턴의 대화를 주입 (session.update가 아님 — 세션 리셋 방지)
- **Translation State**: App UI에 "번역 중" 인디케이터 표시

### 3. OpenAI TTS Output (session_a.py:164-190 → voice_to_voice.py:352-365)

```
OpenAI Session A ──[response.audio.delta]──→ _handle_audio_delta()
                                                  │
                                                  ├─ 첫 번째 청크: 레이턴시 측정 (user_input_at → now)
                                                  ├─ guardrail Level 3 체크 (차단 시 return)
                                                  ├─ base64 decode → audio_bytes (g711_ulaw)
                                                  └─ _on_tts_audio(audio_bytes)
                                                          │
                                                          ↓
                                              _on_session_a_tts()
                                                  │
                                                  ├─ interrupt.is_recipient_speaking → return
                                                  ├─ is_first = echo_gate.on_tts_chunk(len(audio_bytes))
                                                  ├─ if is_first: first_message 레이턴시 측정
                                                  └─ twilio_handler.send_audio(audio_bytes)
```

### 4. Echo Window Activation (pipeline/echo_gate.py — EchoGateManager)

TTS 오디오를 Twilio에 보내는 순간 `EchoGateManager`가 Echo Window를 활성화한다.

```python
# _on_session_a_tts() 내부
is_first = self.echo_gate.on_tts_chunk(len(audio_bytes))
# on_tts_chunk() → 첫 청크 시 _activate() 호출 → in_echo_window = True
```

`EchoGateManager`는 V2V와 T2V 파이프라인에서 공통으로 사용되는 독립 컴포넌트다:
- V2V: `echo_margin_s=0.3, max_echo_window_s=1.2, settling_s=2.0`
- T2V: `echo_margin_s=0.3, max_echo_window_s=None, settling_s=2.0` (cap 없음)

Echo Window 활성 중 → Session B로 들어오는 Twilio 오디오를 `0xFF` (mu-law silence)로 대체하여 에코 피드백 루프를 차단한다.

### 5. Response Done → Echo Cooldown (EchoGateManager._cooldown_timer)

```
OpenAI Session A ──[response.done]──→ _on_session_a_done()
                                          │
                                          ├─ echo_gate.on_tts_done()  → cooldown 시작
                                          ├─ translation_state: "done" → App
                                          └─ metrics snapshot → App
```

#### Echo Cooldown 공식 (핵심 아키텍처)

```python
# pipeline/echo_gate.py — EchoGateManager._cooldown_timer()
async def _cooldown_timer(self, first_chunk_at: float, total_bytes: int) -> None:
    audio_duration_s = total_bytes / 8000   # G.711 mu-law @ 8kHz: 1 byte = 1 sample
    elapsed = time.time() - first_chunk_at
    remaining_playback = max(audio_duration_s - elapsed, 0)
    cooldown = remaining_playback + self._echo_margin_s
    if self._max_echo_window_s is not None:
        cooldown = min(cooldown, self._max_echo_window_s)
    await asyncio.sleep(cooldown)
```

| 변수 | 의미 | 값 |
|------|------|-----|
| `total_bytes / 8000` | TTS 총 재생 시간 추정 | G.711: 1byte = 1sample @ 8kHz |
| `elapsed` | 첫 TTS 청크부터 경과한 시간 | 스트리밍 중 이미 재생된 부분 |
| `remaining_playback` | 아직 Twilio에서 재생 중인 TTS 길이 | `audio_duration - elapsed` |
| `_echo_margin_s` | 에코 왕복 마진 | 0.3s (V2V), 0.3s (T2V) |
| `_MAX_ECHO_WINDOW_S` | 최대 에코 윈도우 | 1.2s (수신자 발화 차단 방지) |

**`total_bytes / 8000` 공식의 의미**: G.711 mu-law는 8kHz 샘플링, 8bit(1byte/sample)이므로 **바이트 수 = 샘플 수**. 따라서 `총 바이트 수 / 8000 = 재생 시간(초)`. 이 공식은 별도의 샘플 레이트 메타데이터나 프레임 카운터 없이 **순수 바이트 축적만으로** TTS 재생 시간을 정확하게 추정한다.

#### Cooldown 후 처리

```
cooldown 완료 → in_echo_window = False
              → session_b.clear_input_buffer()   # 에코 잔여물 제거
              → local_vad.reset_state()           # VAD carry-over 방지
              → Post-Echo Settling 시작 (2.0s)
                  └─ is_suppressing = True 유지 → VAD 처리 억제
                  └─ AGC 자동 이득 제어 복원 대기 (마이크 게인 안정화)
```

**Post-Echo Settling (2.0s)**: TTS 에코가 끝난 직후 AGC(자동 이득 제어)가 마이크 게인을 복원하면서 증폭된 배경 소음이 발생한다. 2.0초의 settling 기간 동안 VAD를 억제하여 이 노이즈가 수신자 발화로 오감지되는 것을 방지한다. 수신자가 실제로 발화하면(`on_recipient_speech()`) settling을 즉시 해제한다.

## Session A Latency 측정

```
User audio commit ──→ [OpenAI STT + Translation + TTS generation] ──→ 첫 TTS audio.delta
       ↑                                                                       ↑
  user_input_at                                                      first_audio_received
       └───────────── latency_ms = (now - user_input_at) × 1000 ──────────────┘
```

평가 결과 (paper_metrics.json, N=141):
- **평균**: 562ms
- **P50**: 478ms
- **P95**: 1,023ms

## Interrupt 처리 (interrupt_handler.py)

### 우선순위 (PRD 3.6)

```
1. 수신자 발화 (최고) — 수신자를 기다리게 하면 안 됨
2. User 발화 — 의도적으로 말하고 있으므로 존중
3. AI 생성 (최저) — 언제든 중단하고 재생성 가능
```

### 수신자 발화 시 Session A 인터럽트 (Case 1, 4)

**V2V (VoiceToVoicePipeline)**:
```
수신자 발화 감지 → on_recipient_speech_started()
                      │
                      ├─ session_a.is_generating? → session_a.cancel()
                      ├─ twilio_handler.send_clear()  # Twilio 재생 버퍼 클리어
                      └─ App에 INTERRUPT_ALERT 전송
```

**T2V (TextToVoicePipeline)** — Interrupt Guard:
```
수신자 발화 감지 → _on_recipient_started()
                      │
                      ├─ echo window 활성? → echo gate 즉시 해제
                      ├─ session_a.is_generating? → interrupt 차단 (TTS 완전 전달 우선)
                      │   └─ "Recipient speech during TTS generation — skipping interrupt (T2V)"
                      └─ TTS 생성 안 함 → interrupt.on_recipient_speech_started()
```

T2V에서는 사용자 입력이 텍스트이므로 TTS를 끊으면 **메시지가 유실**된다. 따라서 `session_a.is_generating` 중에는 interrupt를 차단하고, TTS 완료 후에만 수신자 발화를 처리한다.

- **_on_session_a_tts()에서의 체크**: `interrupt.is_recipient_speaking`이 True면 TTS 오디오를 폐기 (voice_to_voice.py:353)
- **쿨다운**: 수신자 발화 종료 후 1.5초 동안 여전히 "말하는 중"으로 간주 (잠깐 쉬었다 이어 말하는 패턴 보호)

## Context Manager (context_manager.py)

```
대화 진행 중:
  Session A/B 번역 완료 → context_manager.add_turn(role, text)

다음 발화 시:
  handle_user_audio_commit() → context_manager.inject_context(session_a)
      ↓
  conversation.item.create([Previous conversation for context]
    User: ...
    Recipient: ...
    [End context — now translate the next utterance])
```

- **슬라이딩 윈도우**: 최근 6턴 유지 (MAX_TURNS = 6)
- **턴당 최대 100자** (MAX_CHARS_PER_TURN = 100)
- **비용**: ~200 토큰/injection (오디오 대비 무시 가능)
- **주입 방식**: `conversation.item.create` (session.update 아님 — 세션 리셋 방지)

## Recovery & Degraded Mode

Session A 연결이 끊기면:
1. **Recovery Manager**: ring_buffer_a에서 미전송 오디오를 읽어 재전송 시도
2. **Degraded Mode**: Recovery 실패 시 Whisper STT 폴백으로 텍스트 전사만 제공

## 상수/설정값 요약

| 파라미터 | 프로덕션 값 | 소스 |
|----------|------------|------|
| Input format | PCM16 16kHz | session_manager.py:336 |
| Output format | g711_ulaw 8kHz | session_manager.py:337 |
| Echo margin (V2V) | 0.3s | voice_to_voice.py:156 |
| Echo margin (T2V) | 0.3s | text_to_voice.py:161 |
| Max echo window (V2V) | 1.2s | voice_to_voice.py:157 |
| Max echo window (T2V) | None (cap 없음) | text_to_voice.py:162 |
| Post-Echo Settling | 2.0s | config.py:87 (AGC 안정화 대기) |
| Mu-law silence byte | 0xFF | pipeline/echo_gate.py |
| Echo energy threshold | 500 RMS | config.py:108 |
| Context window | 6 turns | context_manager.py:17 |
| Context max chars | 100/turn | context_manager.py:18 |
| Recipient cooldown | 1.5s | interrupt_handler.py:31 |
| STT model (V2V) | whisper-1 | session_manager.py (할루시네이션 블록리스트 호환) |
| STT model (T2V/Agent) | gpt-4o-transcribe | session_manager.py (settings.stt_model) |
