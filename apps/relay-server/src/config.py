from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

# Monorepo 루트의 .env 파일 경로 (config.py → src → relay-server → apps → wigvo)
# Docker(/app/src/config.py)에서는 parents[3]이 없으므로 안전하게 처리
try:
    _ROOT_DIR = Path(__file__).resolve().parents[3]
except IndexError:
    _ROOT_DIR = Path(__file__).resolve().parent  # Docker fallback → env vars 사용
_ENV_FILE = _ROOT_DIR / ".env"


class Settings(BaseSettings):
    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # OpenAI
    openai_api_key: str = ""
    openai_realtime_model: str = "gpt-4o-realtime-preview"
    openai_ws_connect_timeout_s: float = 30.0  # WebSocket handshake timeout (기본 10s → 30s)
    openai_ws_connect_retries: int = 2  # 연결 실패 시 재시도 횟수

    # Supabase
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_service_key: str = ""
    supabase_service_role_key: str = ""

    @model_validator(mode="after")
    def resolve_service_key(self) -> "Settings":
        """SUPABASE_SERVICE_ROLE_KEY 우선, fallback SUPABASE_SERVICE_KEY."""
        if self.supabase_service_role_key:
            self.supabase_service_key = self.supabase_service_role_key
        return self

    # Relay Server
    relay_server_url: str = "http://localhost:8000"
    relay_server_port: int = 8000
    relay_server_host: str = "0.0.0.0"

    # CORS
    allowed_origins: list[str] = Field(
        default=[
            "http://localhost:3000",
            "https://wigvo.run",
            "https://wigvo-web-gzjzn35jyq-du.a.run.app",
        ],
        description="CORS allowed origins",
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",")]
        return v

    # Call limits (M-3: 최대 통화 시간 10분)
    max_call_duration_ms: int = 600_000
    call_warning_ms: int = 480_000  # 8분 경고

    # First message timeouts (C-3)
    recipient_answer_timeout_s: int = 45

    # Phase 3: Recovery settings (PRD 5.3)
    recovery_max_attempts: int = 5
    recovery_initial_backoff_s: float = 1.0
    recovery_max_backoff_s: float = 30.0
    recovery_backoff_multiplier: float = 2.0
    recovery_timeout_s: float = 10.0  # 10초 초과 시 Degraded Mode
    heartbeat_interval_s: float = 5.0
    heartbeat_timeout_s: float = 120.0  # 45→120s: 대화 중 자연스러운 침묵(타이핑 대기 등)을 disconnect로 오판 방지
    ring_buffer_capacity_slots: int = 1500  # 30초 / 20ms

    # Whisper fallback (Degraded Mode)
    whisper_model: str = "whisper-1"

    # Echo Gate (에코 피드백 루프 차단)
    echo_gate_cooldown_s: float = 2.5  # TTS 완료 후 에코 소멸 대기 (레거시 폴백용)
    # Session B VAD 설정 (수신자 음성 감지 민감도)
    session_b_vad_threshold: float = 0.8  # 0.0~1.0, 높을수록 큰 소리만 감지 (전화 오디오 권장 0.8~0.85)
    session_b_vad_silence_ms: int = 500  # 발화 종료 판정까지 필요한 무음 시간 (기본 200ms → 500ms)
    session_b_vad_prefix_padding_ms: int = 300  # 발화 시작 전 포함할 오디오 (기본 300ms)
    session_b_min_speech_ms: int = 400  # 최소 발화 길이 — 이보다 짧은 segment는 노이즈로 간주 (250→400: 할루시네이션 방지)

    # Local VAD (Silero VAD + RMS Energy Gate)
    local_vad_enabled: bool = True
    local_vad_rms_threshold: float = 200.0  # PSTN 배경 소음(50-200) 위로 설정하여 오감지 방지
    local_vad_speech_threshold: float = 0.5
    local_vad_silence_threshold: float = 0.35
    local_vad_min_speech_frames: int = 3    # 3 × 32ms = 96ms (할루시네이션 방지: 짧은 노이즈 무시)
    local_vad_min_silence_frames: int = 15  # 15 × 32ms = 480ms

    # 클라이언트 측 오디오 에너지 게이트 (무음/소음 필터링)
    # 에너지 게이트: 임계값 이하 오디오를 silence로 교체하여 VAD에 전달
    # PSTN 배경 소음(50-200 RMS)을 silence로 교체 → VAD가 speech_stopped 자연 감지
    # 수신자 직접 발화(500-2000+ RMS)는 항상 통과
    audio_energy_gate_enabled: bool = True
    audio_energy_min_rms: float = 150.0  # PSTN 소음(50-200) → silence 교체, 발화(500+) → 통과
    echo_energy_threshold_rms: float = 500.0  # Echo window: 에코(100-400) → silence, 발화(500+) → 통과

    # Max speech duration: 에너지 게이트로도 VAD speech_stopped가 지연되는 극단 케이스 안전망
    # 이 시간 초과 시 오디오 버퍼를 강제 commit하여 번역 시작
    max_speech_duration_s: float = 8.0

    # Logging
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_max_bytes: int = 10_485_760  # 10MB
    log_backup_count: int = 5

    # Phase 4: Guardrail (PRD M-2)
    guardrail_enabled: bool = True
    guardrail_fallback_model: str = "gpt-4o-mini"
    guardrail_fallback_timeout_ms: int = 2000

    model_config = {
        "env_file": str(_ENV_FILE),
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # 공유 .env의 Web 전용 변수(NEXT_PUBLIC_* 등) 무시
    }


settings = Settings()
