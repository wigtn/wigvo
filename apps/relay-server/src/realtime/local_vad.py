"""Local VAD — Silero VAD + RMS Energy Gate 2단계 음성 감지.

Server VAD가 전화 소음 환경에서 speech_stopped을 감지하지 못하는 문제를 해결한다.
배경소음이 Server VAD를 "speaking" 상태에 영구 고정시켜 15초 timeout 후
불완전 오디오로 할루시네이션이 발생하는 근본 원인을 로컬 2단계 감지로 대체.

아키텍처:
  Stage 1: RMS Energy Gate — RMS < threshold → silence (SILENCE 상태에서만 Silero 스킵)
  Stage 2: Silero VAD prob → State Machine (hysteresis)
    - SILENCE→SPEAKING: RMS gate + Silero (엄격한 진입 — 노이즈 차단)
    - SPEAKING→SILENCE: Silero only (관대한 종료 — 음절 간 RMS 딥에서 끊김 방지)

Frame Adapter: 20ms (160 samples @ 8kHz) → 16kHz 업샘플링 → 32ms (512 samples)
  Twilio 오디오는 8kHz g711_ulaw. Silero VAD는 16kHz에서 최적 성능.
  8kHz → 16kHz zero-order hold 업샘플링 후 512 samples (32ms) 프레임으로 처리.

RMS Gate 복귀 시 Silero 리셋:
  RMS gate로 Silero 처리를 건너뛸 때 내부 RNN 상태가 정체됨.
  RMS-silence → RMS-active 전환 시 Silero 모델을 리셋하여 깨끗한 상태에서 시작.
"""

import asyncio
import logging
from enum import Enum
from typing import Callable, Coroutine

import numpy as np

from src.realtime.audio_utils import ulaw_rms, ulaw_to_float32

logger = logging.getLogger(__name__)


class _VadState(str, Enum):
    SILENCE = "silence"
    SPEAKING = "speaking"


class LocalVAD:
    """Silero VAD + RMS Energy Gate 2단계 로컬 음성 감지기.

    Args:
        rms_threshold: RMS 에너지 임계값 (이하 → silence, Silero 스킵)
        speech_threshold: Silero VAD speech 확률 임계값 (이상 → speech candidate)
        silence_threshold: Silero VAD silence 확률 임계값 (이하 → silence candidate)
        min_speech_frames: speech 전환까지 필요한 연속 speech 프레임 수
        min_silence_frames: silence 전환까지 필요한 연속 silence 프레임 수
        on_speech_start: speech 시작 콜백
        on_speech_end: speech 종료 콜백
    """

    # Silero VAD 프레임: 16kHz에서 512 samples = 32ms (8kHz 업샘플링)
    _SILERO_FRAME_SIZE = 512
    _SILERO_SAMPLE_RATE = 16000  # Silero 모델 입력 sample rate
    _INPUT_SAMPLE_RATE = 8000    # Twilio 입력 sample rate
    # Silero 리셋 전 최소 연속 RMS silence 프레임 수 (음절 간 짧은 무음에서 리셋 방지)
    _MIN_RMS_SILENCE_FOR_RESET = 5  # 5 × 20ms = 100ms

    def __init__(
        self,
        rms_threshold: float = 150.0,
        speech_threshold: float = 0.5,
        silence_threshold: float = 0.35,
        min_speech_frames: int = 2,
        min_silence_frames: int = 15,
        on_speech_start: Callable[[], Coroutine] | None = None,
        on_speech_end: Callable[[], Coroutine] | None = None,
    ):
        self._rms_threshold = rms_threshold
        self._speech_threshold = speech_threshold
        self._silence_threshold = silence_threshold
        self._min_speech_frames = min_speech_frames
        self._min_silence_frames = min_silence_frames
        self._on_speech_start = on_speech_start
        self._on_speech_end = on_speech_end

        # State machine
        self._state = _VadState.SILENCE
        self._speech_count = 0
        self._silence_count = 0

        # Frame adapter buffer: 20ms (160→320 upsampled) → 32ms (512 samples @ 16kHz)
        self._frame_buffer = np.empty(0, dtype=np.float32)

        # RMS gate 연속 silence 프레임 수 (Silero 리셋 판단용)
        # 음절 사이 짧은 무음(1-2프레임)에서 리셋되면 Silero 문맥이 깨짐
        # _MIN_RMS_SILENCE_FOR_RESET 이상 연속 silence여야 리셋
        self._rms_silence_frames = 0

        # Speech quality tracking: speech 중 최대 RMS (노이즈 vs 실제 발화 구분용)
        self._peak_rms: float = 0.0

        # Silero VAD model (lazy init)
        self._model = None
        self._init_model()

    def _init_model(self) -> None:
        """Silero VAD 모델을 로드한다 (16kHz)."""
        try:
            from silero_vad_lite import SileroVAD
            self._model = SileroVAD(self._SILERO_SAMPLE_RATE)
            logger.info("[LocalVAD] Silero VAD model loaded (16kHz)")
        except ImportError:
            logger.error("[LocalVAD] silero-vad-lite not installed — LocalVAD disabled")
            self._model = None
        except Exception:
            logger.exception("[LocalVAD] Failed to load Silero VAD model")
            self._model = None

    @property
    def is_speaking(self) -> bool:
        return self._state == _VadState.SPEAKING

    @property
    def peak_rms(self) -> float:
        """현재/마지막 speech 구간의 최대 RMS."""
        return self._peak_rms

    async def process(self, audio: bytes) -> None:
        """20ms g711_ulaw 오디오 프레임을 처리한다.

        Stage 1: RMS Energy Gate
        Stage 2: Silero VAD (8kHz→16kHz 업샘플링 + 32ms 프레임 어댑터)

        Args:
            audio: g711_ulaw 오디오 바이트 (20ms = 160 samples @ 8kHz)
        """
        if self._model is None:
            return

        # Stage 1: RMS Energy Gate
        rms = ulaw_rms(audio)

        # Peak RMS tracking (SPEAKING 상태 + speech candidate 중)
        # SPEAKING 전환 전 candidate 프레임의 높은 RMS를 캡처하기 위해
        # _speech_count > 0 (Silero가 speech 감지 시작) 조건 추가
        if (self._state == _VadState.SPEAKING or self._speech_count > 0) and rms > self._peak_rms:
            self._peak_rms = rms

        # 디버그: 500ms마다 RMS 로그
        self._debug_frame_count = getattr(self, "_debug_frame_count", 0) + 1
        if self._debug_frame_count % 25 == 0:
            logger.debug(
                "[LocalVAD] rms=%.0f state=%s speech_cnt=%d silence_cnt=%d buf=%d",
                rms, self._state.value, self._speech_count, self._silence_count, len(self._frame_buffer),
            )

        if rms < self._rms_threshold:
            self._rms_silence_frames += 1

            if self._state == _VadState.SILENCE:
                # SILENCE 상태: RMS gate로 Silero 스킵 (엄격한 진입 — 노이즈 차단)
                self._speech_count = 0
                self._silence_count += 1
                return

            # SPEAKING 상태: Silero에게 종료 판단 위임 (fall through)
            # 음절 간 RMS 딥에서도 Silero가 음성으로 판단하면 speech 유지
        else:
            # RMS >= threshold: Silero 리셋 체크 + _rms_silence_frames 리셋
            # 충분히 긴 silence 후에만 리셋 (음절 간 짧은 무음에서 리셋 방지)
            if self._rms_silence_frames >= self._MIN_RMS_SILENCE_FOR_RESET:
                self._frame_buffer = np.empty(0, dtype=np.float32)
                try:
                    self._model.reset()
                except Exception:
                    pass
                logger.debug(
                    "[LocalVAD] Silero reset after %d RMS silence frames",
                    self._rms_silence_frames,
                )
            self._rms_silence_frames = 0

        # mu-law → float32 변환 (8kHz)
        samples = ulaw_to_float32(audio)

        # 8kHz → 16kHz 업샘플링 (zero-order hold)
        samples_16k = np.repeat(samples, 2)

        # Frame adapter: 32ms (512 samples @ 16kHz) 버퍼링
        self._frame_buffer = np.concatenate([self._frame_buffer, samples_16k])

        while len(self._frame_buffer) >= self._SILERO_FRAME_SIZE:
            frame = self._frame_buffer[: self._SILERO_FRAME_SIZE]
            self._frame_buffer = self._frame_buffer[self._SILERO_FRAME_SIZE:]

            # Stage 2: Silero VAD (writable memoryview 필요)
            frame_writable = frame.copy()
            prob = self._model.process(memoryview(frame_writable.data))
            logger.debug("[LocalVAD] silero prob=%.3f rms=%.0f state=%s", prob, rms, self._state.value)
            await self._update_state(prob)

    async def _update_state(self, prob: float) -> None:
        """Silero VAD 확률로 상태 머신을 업데이트한다 (hysteresis)."""
        if self._state == _VadState.SILENCE:
            if prob >= self._speech_threshold:
                if self._speech_count == 0:
                    self._peak_rms = 0.0  # 새 speech candidate 시작 — peak 리셋
                self._speech_count += 1
                self._silence_count = 0
                if self._speech_count >= self._min_speech_frames:
                    await self._transition_to_speaking()
            else:
                self._speech_count = 0
        else:  # SPEAKING
            if prob < self._silence_threshold:
                self._silence_count += 1
                self._speech_count = 0
                if self._silence_count >= self._min_silence_frames:
                    await self._transition_to_silence()
            else:
                self._silence_count = 0

    async def _transition_to_speaking(self) -> None:
        """SILENCE → SPEAKING 전환."""
        self._state = _VadState.SPEAKING
        self._speech_count = 0
        self._silence_count = 0
        # peak_rms는 candidate 단계에서 이미 추적 중 — 여기서 리셋하면 pre-transition 값 손실
        logger.info("[LocalVAD] Speech started (peak_rms=%.0f)", self._peak_rms)
        if self._on_speech_start:
            try:
                await self._on_speech_start()
            except Exception:
                logger.exception("[LocalVAD] on_speech_start callback error")

    async def _transition_to_silence(self) -> None:
        """SPEAKING → SILENCE 전환."""
        self._state = _VadState.SILENCE
        self._speech_count = 0
        self._silence_count = 0
        logger.info("[LocalVAD] Speech ended")
        if self._on_speech_end:
            try:
                await self._on_speech_end()
            except Exception:
                logger.exception("[LocalVAD] on_speech_end callback error")

    def force_speaking_state(self) -> None:
        """VAD를 SPEAKING 상태로 강제 전환한다 (콜백 미호출).

        Post-echo settling 중 RMS로 수신자 발화를 감지했을 때 사용.
        외부에서 이미 notify_speech_started()를 호출한 상태이므로
        콜백 없이 상태만 동기화한다.
        """
        self._state = _VadState.SPEAKING
        self._speech_count = 0
        self._silence_count = 0
        self._peak_rms = 0.0
        self._frame_buffer = np.empty(0, dtype=np.float32)
        self._rms_silence_frames = 0
        if self._model is not None:
            try:
                self._model.reset()
            except Exception:
                pass
        logger.info("[LocalVAD] Forced to SPEAKING state (settling breakthrough)")

    def reset_state(self) -> None:
        """VAD 상태만 초기화한다 (echo window 종료 시).

        Silero 모델은 리셋하지 않아 warm 상태 유지.
        """
        self._state = _VadState.SILENCE
        self._speech_count = 0
        self._silence_count = 0
        self._frame_buffer = np.empty(0, dtype=np.float32)
        logger.debug("[LocalVAD] State reset (model preserved)")

    def reset(self) -> None:
        """상태를 초기화한다 (통화 종료 시)."""
        self._state = _VadState.SILENCE
        self._speech_count = 0
        self._silence_count = 0
        self._frame_buffer = np.empty(0, dtype=np.float32)
        self._rms_silence_frames = 0
        if self._model is not None:
            try:
                self._model.reset()
            except Exception:
                pass
        logger.debug("[LocalVAD] Reset")
