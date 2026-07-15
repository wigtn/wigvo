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
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from typing import Callable, Coroutine

import numpy as np
import onnxruntime as ort

from src.realtime.audio_utils import ulaw_rms, ulaw_to_float32

logger = logging.getLogger(__name__)

# Silero ONNX 추론(GIL 해제 C 호출, 프로파일링 결과 busy CPU의 ~51%)을 이벤트루프에서
# 분리하기 위한 고정 공유 스레드풀. 통화당 전용 스레드(§8-#6) 대신 코어 수 배수 고정 풀:
# ONNX가 GIL을 풀어 병렬도 상한은 어차피 코어 수이므로 스레드를 통화 수만큼 만들 이유가 없다.
_VAD_POOL_WORKERS = int(os.getenv("VAD_POOL_WORKERS", str(os.cpu_count() or 4)))
_VAD_EXECUTOR = ThreadPoolExecutor(
    max_workers=_VAD_POOL_WORKERS, thread_name_prefix="vad-infer"
)

# Silero VAD v6 (§8-#10). onnxruntime C++ 경로 유지(GIL 해제 → 오프로드 호환).
# ⚠ 실측: v6는 v5의 512샘플 호출로는 발화 검출 0% — 반드시 64샘플 컨텍스트(입력 576)가 필요하다
# (PRD가 dismiss했던 부분이 실제 정답). 세션은 stateless라 프로세스 공유, 상태/컨텍스트는 통화별.
_V6_MODEL_PATH = Path(__file__).resolve().parent / "models" / "silero_vad_v6.onnx"
_ort_session: ort.InferenceSession | None = None


def _get_ort_session() -> ort.InferenceSession:
    """v6 onnx 세션(프로세스 공유). intra/inter op 스레드 1 — 병렬은 _VAD_EXECUTOR가 담당."""
    global _ort_session
    if _ort_session is None:
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        _ort_session = ort.InferenceSession(str(_V6_MODEL_PATH), sess_options=so)
    return _ort_session


class _SileroV6Model:
    """Silero VAD v6 추론기. v5(silero-vad-lite) 대체.

    .process(512프레임)/.reset() 인터페이스는 v5와 동일하게 유지(호출부·테스트 무변경).
    내부적으로 64샘플 컨텍스트를 앞에 붙여 576샘플로 v6 모델을 구동하고,
    RNN 상태(state[2,1,128])와 컨텍스트를 인스턴스별로 관리한다.
    """

    _CONTEXT = 64  # v6 필수 컨텍스트 샘플 수

    def __init__(self, session: ort.InferenceSession) -> None:
        self._session = session
        self._sr = np.array(16000, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self._CONTEXT, dtype=np.float32)

    def process(self, frame) -> float:
        """512샘플(@16k) float32 프레임 → speech 확률. 앞에 64샘플 컨텍스트 부착(576)."""
        f = np.frombuffer(frame, dtype=np.float32)
        inp = np.concatenate([self._context, f]).reshape(1, -1)
        out = self._session.run(None, {"input": inp, "state": self._state, "sr": self._sr})
        self._state = out[1]
        self._context = f[-self._CONTEXT:].copy()
        return float(np.asarray(out[0]).reshape(-1)[0])


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
    _MIN_RMS_SILENCE_FOR_RESET = 10  # 10 × 20ms = 200ms (100ms 호흡에서 Silero 리셋 방지)

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
        # 추론은 워커 스레드에서, reset()은 이벤트루프 스레드에서 호출되므로
        # 같은 C 모델에 대한 동시 접근을 막는 락 (추론↔reset 상호배제).
        self._model_lock = threading.Lock()
        self._model = None
        self._init_model()

    def _init_model(self) -> None:
        """Silero VAD v6 모델을 로드한다 (onnxruntime, 16kHz, 64샘플 컨텍스트)."""
        try:
            self._model = _SileroV6Model(_get_ort_session())
            logger.info("[LocalVAD] Silero VAD v6 loaded (onnxruntime, 16kHz, 64-sample context)")
        except Exception:
            logger.exception("[LocalVAD] Failed to load Silero VAD v6 model")
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
                    with self._model_lock:
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
            # ONNX 추론(GIL 해제)을 이벤트루프 밖 고정 스레드풀로 오프로드 →
            # 추론 중 루프가 다른 통화 프레임을 처리 (통화 간 병렬).
            frame_writable = frame.copy()
            loop = asyncio.get_running_loop()
            prob = await loop.run_in_executor(
                _VAD_EXECUTOR, self._infer, memoryview(frame_writable.data)
            )
            logger.debug("[LocalVAD] silero prob=%.3f rms=%.0f state=%s", prob, rms, self._state.value)
            await self._update_state(prob)

    def _infer(self, frame_mv: memoryview) -> float:
        """워커 스레드에서 Silero ONNX 추론을 실행한다 (모델 락으로 reset과 상호배제)."""
        with self._model_lock:
            return self._model.process(frame_mv)

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
                with self._model_lock:
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
                with self._model_lock:
                    self._model.reset()
            except Exception:
                pass
        logger.debug("[LocalVAD] Reset")
