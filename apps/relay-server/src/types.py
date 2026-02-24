from __future__ import annotations

from enum import Enum
from typing import Any

import re

from pydantic import BaseModel, Field, field_validator


# --- Enums ---


class CallMode(str, Enum):
    RELAY = "relay"
    AGENT = "agent"


class CallStatus(str, Enum):
    PENDING = "pending"
    CALLING = "calling"
    CONNECTED = "connected"
    ENDED = "ended"
    FAILED = "failed"


# DB 저장용 매핑: Agent mode call_result → Web App result 컬럼 값
CALL_RESULT_MAP: dict[str, str] = {
    "success": "SUCCESS",
    "partial_success": "SUCCESS",
    "failed": "ERROR",
    "callback_needed": "NO_ANSWER",
}


class CommunicationMode(str, Enum):
    VOICE_TO_VOICE = "voice_to_voice"
    TEXT_TO_VOICE = "text_to_voice"
    FULL_AGENT = "full_agent"


class VadMode(str, Enum):
    CLIENT = "client"
    SERVER = "server"
    LOCAL = "local"
    PUSH_TO_TALK = "push_to_talk"


class SessionState(str, Enum):
    """OpenAI Realtime 세션 상태."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    DEGRADED = "degraded"


class RecoveryEventType(str, Enum):
    """Recovery 이벤트 유형."""

    SESSION_DISCONNECTED = "session_disconnected"
    RECONNECT_ATTEMPT = "reconnect_attempt"
    RECONNECT_SUCCESS = "reconnect_success"
    RECONNECT_FAILED = "reconnect_failed"
    CATCHUP_STARTED = "catchup_started"
    CATCHUP_COMPLETED = "catchup_completed"
    DEGRADED_MODE_ENTERED = "degraded_mode_entered"
    DEGRADED_MODE_EXITED = "degraded_mode_exited"
    NORMAL_RESTORED = "normal_restored"


# --- Request / Response ---


class CallStartRequest(BaseModel):
    call_id: str
    phone_number: str
    mode: CallMode = CallMode.RELAY
    source_language: str = "en"
    target_language: str = "ko"
    collected_data: dict[str, Any] | None = None
    vad_mode: VadMode = VadMode.CLIENT
    system_prompt_override: str | None = None
    communication_mode: CommunicationMode = CommunicationMode.VOICE_TO_VOICE

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, v: str) -> str:
        if not re.match(r"^\+[1-9]\d{1,14}$", v):
            raise ValueError("Phone number must be in E.164 format (e.g., +14155552671)")
        return v


class CallStartResponse(BaseModel):
    call_id: str
    call_sid: str
    relay_ws_url: str
    session_ids: dict[str, str]


class CallEndRequest(BaseModel):
    call_id: str
    reason: str = "user_hangup"


# --- WebSocket Messages (App ↔ Relay Server) ---


class WsMessageType(str, Enum):
    # App → Relay
    AUDIO_CHUNK = "audio_chunk"
    TEXT_INPUT = "text_input"
    VAD_STATE = "vad_state"
    TYPING_STATE = "typing_state"
    END_CALL = "end_call"

    # Relay → App
    CAPTION = "caption"
    CAPTION_ORIGINAL = "caption.original"       # 원문 자막 (즉시)
    CAPTION_TRANSLATED = "caption.translated"    # 번역 자막 (0.5초 후)
    RECIPIENT_AUDIO = "recipient_audio"
    CALL_STATUS = "call_status"
    INTERRUPT_ALERT = "interrupt_alert"
    SESSION_RECOVERY = "session.recovery"
    GUARDRAIL_TRIGGERED = "guardrail.triggered"
    TRANSLATION_STATE = "translation.state"
    METRICS = "metrics"
    ERROR = "error"


class WsMessage(BaseModel):
    type: WsMessageType
    data: dict[str, Any] = {}


# --- Session Config ---


class SessionConfig(BaseModel):
    session_id: str = ""
    mode: CallMode = CallMode.RELAY
    source_language: str = "en"
    target_language: str = "ko"
    input_audio_format: str = "pcm16"
    output_audio_format: str = "g711_ulaw"
    vad_mode: VadMode = VadMode.SERVER
    input_audio_transcription: dict[str, str] | None = None  # e.g. {"model": "whisper-1"}
    modalities: list[str] = Field(default_factory=lambda: ["text", "audio"])


# --- Twilio Media Stream Events ---


class TwilioMediaEvent(BaseModel):
    """Twilio Media Stream WebSocket 이벤트.

    Twilio는 camelCase (streamSid, sequenceNumber)로 보내므로 alias 매핑 필요.
    """

    model_config = {"populate_by_name": True}

    event: str
    stream_sid: str | None = Field(None, alias="streamSid")
    sequence_number: str | None = Field(None, alias="sequenceNumber")
    media: dict[str, str] | None = None  # {"payload": base64, "track": "inbound"}
    start: dict[str, Any] | None = None
    stop: dict[str, Any] | None = None


# --- Active Call State ---


class RecoveryEvent(BaseModel):
    """Recovery 이벤트 로그 항목."""

    type: RecoveryEventType
    session_label: str = ""
    gap_ms: int = 0
    attempt: int = 0
    status: str = ""
    timestamp: float = 0.0
    detail: str = ""


class TranscriptEntry(BaseModel):
    """양쪽 언어 트랜스크립트 항목 (transcript_bilingual)."""
    role: str  # "user" | "recipient" | "ai"
    original_text: str = ""
    translated_text: str = ""
    language: str = ""  # source language code
    timestamp: float = 0.0


class CostTokens(BaseModel):
    """OpenAI Realtime API 토큰 사용량 추적."""
    audio_input: int = 0
    audio_output: int = 0
    text_input: int = 0
    text_output: int = 0

    def add(self, other: "CostTokens") -> None:
        """다른 CostTokens를 더한다."""
        self.audio_input += other.audio_input
        self.audio_output += other.audio_output
        self.text_input += other.text_input
        self.text_output += other.text_output

    @property
    def total(self) -> int:
        return self.audio_input + self.audio_output + self.text_input + self.text_output

    @property
    def cost_usd(self) -> float:
        """OpenAI Realtime API 가격 기준 USD 비용 계산.

        Pricing (per 1K tokens):
          audio_input: $0.06, audio_output: $0.24
          text_input: $0.005, text_output: $0.02
        """
        return (
            self.audio_input * 0.06 / 1000
            + self.audio_output * 0.24 / 1000
            + self.text_input * 0.005 / 1000
            + self.text_output * 0.02 / 1000
        )


class CallMetrics(BaseModel):
    """통화 성능 지표 (통화 종료 시 로그 출력 + call_result_data에 저장)."""

    # Session A: User 입력 완료 → TTS first chunk (번역 라운드트립)
    session_a_latencies_ms: list[float] = Field(default_factory=list)
    # Session B: 수신자 발화 시작 → 번역 완료 (end-to-end)
    session_b_e2e_latencies_ms: list[float] = Field(default_factory=list)
    # Session B: 수신자 발화 시작 → STT 완료
    session_b_stt_latencies_ms: list[float] = Field(default_factory=list)
    # 첫 메시지 지연 (pipeline start → first TTS to Twilio)
    first_message_latency_ms: float = 0.0
    # 번역 턴 수 (Session A + Session B 각 번역 완료 시 +1)
    turn_count: int = 0
    # 에코 윈도우 활성화 횟수
    echo_suppressions: int = 0
    # STT 환각 차단 횟수
    hallucinations_blocked: int = 0
    # VAD false trigger 횟수 (speech_started → 유효 번역 없이 종료)
    vad_false_triggers: int = 0
    # Echo window 중 speech 감지 횟수 (에코가 발화로 오인)
    echo_loops_detected: int = 0
    # 에코 윈도우 중 고에너지 발화로 게이트 해제 횟수
    echo_gate_breakthroughs: int = 0
    # callee가 Session A TTS를 중단한 횟수
    interrupt_count: int = 0
    # Guardrail 비동기 교정 횟수 (Level 2)
    guardrail_level2_count: int = 0
    # Guardrail 동기 차단 횟수 (Level 3)
    guardrail_level3_count: int = 0
    # Session B: 수신자 발화 구간 (speech_started → speech_stopped)
    session_b_speech_durations_ms: list[float] = Field(default_factory=list)
    # Session B: 처리 지연 (speech_stopped → 번역 완료), STT와 독립적
    session_b_processing_latencies_ms: list[float] = Field(default_factory=list)
    # Session B: STT 완료가 speech_stopped 이후에 발생한 지연
    session_b_stt_after_stop_ms: list[float] = Field(default_factory=list)


class ActiveCall(BaseModel):
    call_id: str
    call_sid: str = ""
    mode: CallMode = CallMode.RELAY
    source_language: str = "en"
    target_language: str = "ko"
    status: CallStatus = CallStatus.PENDING
    communication_mode: CommunicationMode = CommunicationMode.VOICE_TO_VOICE
    stream_sid: str = ""
    session_a_id: str = ""
    session_b_id: str = ""
    collected_data: dict[str, Any] = {}
    started_at: float = 0.0
    first_message_sent: bool = False
    prompt_a: str = ""
    prompt_b: str = ""
    # Phase 3: Recovery
    session_a_state: SessionState = SessionState.CONNECTED
    session_b_state: SessionState = SessionState.CONNECTED
    recovery_events: list[RecoveryEvent] = Field(default_factory=list)
    transcript_history: list[dict[str, str]] = Field(default_factory=list)
    # Phase 5: Transcript & Cost
    transcript_bilingual: list[TranscriptEntry] = Field(default_factory=list)
    cost_tokens: CostTokens = Field(default_factory=CostTokens)
    call_result: str = ""
    call_result_data: dict[str, Any] = Field(default_factory=dict)
    auto_ended: bool = False
    function_call_logs: list[dict[str, Any]] = Field(default_factory=list)
    guardrail_events_log: list[dict[str, Any]] = Field(default_factory=list)
    call_metrics: CallMetrics = Field(default_factory=CallMetrics)
