"""LocalVAD 단위 테스트.

핵심 검증 사항:
  - Stage 1: RMS Energy Gate (Silero 스킵)
  - Stage 2: Silero VAD 확률 → State Machine (hysteresis)
  - Frame adapter: 20ms (160 samples @ 8kHz) → 16kHz 업샘플링 → 32ms (512 samples)
  - Async callbacks: on_speech_start, on_speech_end
  - reset(): 상태 초기화
  - RMS silence → active 전환 시 Silero 리셋
  - ulaw_to_float32: 변환 정확성
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.realtime.audio_utils import _ULAW_TO_LINEAR, ulaw_to_float32


class TestUlawToFloat32:
    """ulaw_to_float32 변환 테스트."""

    def test_silence_converts_to_zero(self):
        """무음(0xFF)이 0.0으로 변환된다."""
        silence = bytes([0xFF] * 10)
        result = ulaw_to_float32(silence)
        assert len(result) == 10
        np.testing.assert_allclose(result, 0.0, atol=1e-6)

    def test_output_range(self):
        """출력이 -1.0 ~ 1.0 범위."""
        all_bytes = bytes(range(256))
        result = ulaw_to_float32(all_bytes)
        assert result.min() >= -1.0
        assert result.max() <= 1.0

    def test_output_dtype(self):
        """출력이 float32 타입."""
        result = ulaw_to_float32(b"\x00\x01\x02")
        assert result.dtype == np.float32

    def test_empty_input(self):
        """빈 입력에 대해 빈 배열 반환."""
        result = ulaw_to_float32(b"")
        assert len(result) == 0

    def test_consistent_with_linear_table(self):
        """_ULAW_TO_LINEAR 테이블과 일관성."""
        max_abs = max(abs(v) for v in _ULAW_TO_LINEAR)
        for byte_val in [0x00, 0x10, 0x80, 0xFE, 0xFF]:
            result = ulaw_to_float32(bytes([byte_val]))
            expected = _ULAW_TO_LINEAR[byte_val] / max_abs
            np.testing.assert_allclose(result[0], expected, atol=1e-6)


class TestLocalVADInit:
    """LocalVAD 초기화 테스트."""

    def test_init_without_silero(self):
        """silero-vad-lite가 없어도 에러 없이 초기화."""
        with patch("src.realtime.local_vad.LocalVAD._init_model") as mock_init:
            from src.realtime.local_vad import LocalVAD
            vad = LocalVAD()
            vad._model = None  # 모델 없는 상태 시뮬레이션
            assert vad.is_speaking is False

    def test_initial_state_is_silence(self):
        """초기 상태가 SILENCE."""
        with patch("src.realtime.local_vad.LocalVAD._init_model"):
            from src.realtime.local_vad import LocalVAD
            vad = LocalVAD()
            vad._model = None
            assert vad.is_speaking is False


class TestLocalVADStateMachine:
    """LocalVAD 상태 머신 테스트 (Silero 모델 mock)."""

    def _make_vad(self, **kwargs):
        """Silero 모델을 mock한 LocalVAD 인스턴스 생성."""
        with patch("src.realtime.local_vad.LocalVAD._init_model"):
            from src.realtime.local_vad import LocalVAD
            vad = LocalVAD(**kwargs)
        # Mock Silero 모델
        vad._model = MagicMock()
        return vad

    @pytest.mark.asyncio
    async def test_rms_gate_skips_silero(self):
        """Stage 1: RMS 임계값 미만이면 Silero 호출 없이 silence 카운트."""
        vad = self._make_vad(rms_threshold=150.0)

        # 무음 오디오 (0xFF = RMS 0)
        silence = bytes([0xFF] * 160)
        await vad.process(silence)

        # Silero가 호출되지 않아야 함
        vad._model.process.assert_not_called()
        assert vad._silence_count == 1

    @pytest.mark.asyncio
    async def test_rms_gate_passes_loud_audio(self):
        """Stage 1: RMS 임계값 이상이면 Silero 호출."""
        vad = self._make_vad(rms_threshold=150.0)
        vad._model.process.return_value = 0.1  # 낮은 확률 (silence)

        # 큰 소리 오디오 (0x00 = 최대 진폭)
        loud = bytes([0x00] * 160)
        await vad.process(loud)

        # Silero가 호출되어야 함 (frame adapter 때문에 바로 호출 안 될 수 있음)
        # 160 samples → 320 upsampled → 아직 512 안 됨
        assert vad._model.process.call_count == 0  # 320 < 512 이므로 아직 미호출

        # 다시 보내면 320+320=640 → 512 frame 1개 처리 가능
        await vad.process(loud)
        assert vad._model.process.call_count == 1

    @pytest.mark.asyncio
    async def test_silence_to_speaking_transition(self):
        """SILENCE → SPEAKING 전환 (min_speech_frames 충족 시)."""
        on_start = AsyncMock()
        vad = self._make_vad(
            rms_threshold=0.0,  # RMS 게이트 비활성
            speech_threshold=0.5,
            min_speech_frames=2,
            on_speech_start=on_start,
        )
        vad._model.process.return_value = 0.8  # 높은 확률 (speech)

        assert vad.is_speaking is False

        # 512 samples씩 2 프레임 필요 → 320*4 = 1280 upsampled → 2 frames + 256 leftover
        loud = bytes([0x10] * 160)
        for _ in range(4):
            await vad.process(loud)

        assert vad.is_speaking is True
        on_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_speaking_to_silence_transition(self):
        """SPEAKING → SILENCE 전환 (min_silence_frames 충족 시)."""
        on_end = AsyncMock()
        vad = self._make_vad(
            rms_threshold=0.0,
            speech_threshold=0.5,
            silence_threshold=0.35,
            min_speech_frames=1,
            min_silence_frames=2,
            on_speech_end=on_end,
        )

        # 먼저 SPEAKING 상태로 전환
        vad._model.process.return_value = 0.8
        loud = bytes([0x10] * 160)
        for _ in range(4):
            await vad.process(loud)
        assert vad.is_speaking is True

        # Silero가 낮은 확률 반환 → silence 전환
        vad._model.process.return_value = 0.1
        for _ in range(4):
            await vad.process(loud)

        assert vad.is_speaking is False
        on_end.assert_called_once()

    @pytest.mark.asyncio
    async def test_hysteresis_prevents_flapping(self):
        """Hysteresis: speech_threshold와 silence_threshold 사이 확률은 상태 변화 없음."""
        on_start = AsyncMock()
        on_end = AsyncMock()
        vad = self._make_vad(
            rms_threshold=0.0,
            speech_threshold=0.5,
            silence_threshold=0.35,
            min_speech_frames=1,
            min_silence_frames=2,
            on_speech_start=on_start,
            on_speech_end=on_end,
        )

        # SPEAKING 상태로 전환
        vad._model.process.return_value = 0.8
        loud = bytes([0x10] * 160)
        for _ in range(4):
            await vad.process(loud)
        assert vad.is_speaking is True
        on_start.assert_called_once()

        # 중간 확률 (0.4: silence_threshold < 0.4 < speech_threshold)
        # SPEAKING에서 silence 전환하려면 < 0.35 필요
        vad._model.process.return_value = 0.4
        for _ in range(10):
            await vad.process(loud)

        # 여전히 SPEAKING 상태 유지
        assert vad.is_speaking is True
        on_end.assert_not_called()

    @pytest.mark.asyncio
    async def test_rms_silence_with_silero_causes_silence_transition(self):
        """SPEAKING 상태에서 RMS silence + Silero silence → SPEAKING→SILENCE 전환.

        SPEAKING→SILENCE 전환은 Silero only 설계 (음절 간 RMS 딥에서 끊김 방지).
        RMS < threshold이어도 Silero에 위임하므로 Silero도 silence를 반환해야 전환됨.
        """
        on_end = AsyncMock()
        vad = self._make_vad(
            rms_threshold=150.0,
            speech_threshold=0.5,
            min_speech_frames=1,
            min_silence_frames=3,
            on_speech_end=on_end,
        )

        # SPEAKING 상태로 전환 (RMS 게이트 우회를 위해 threshold=0 로 임시 변경)
        vad._rms_threshold = 0.0
        vad._model.process.return_value = 0.8
        loud = bytes([0x10] * 160)
        for _ in range(4):
            await vad.process(loud)
        assert vad.is_speaking is True

        # RMS 게이트 복원 + Silero도 silence 반환
        vad._rms_threshold = 150.0
        vad._model.process.return_value = 0.1  # < silence_threshold (0.35)

        # 무음 오디오 → Silero에 위임 → silence 전환
        silence = bytes([0xFF] * 160)
        for _ in range(10):  # 충분한 프레임으로 Silero silence 누적
            await vad.process(silence)

        assert vad.is_speaking is False
        on_end.assert_called_once()

    @pytest.mark.asyncio
    async def test_silero_reset_after_sustained_rms_silence(self):
        """충분히 긴 RMS silence(5+ frames) 후 active 전환 시 Silero 리셋."""
        vad = self._make_vad(rms_threshold=150.0)
        vad._model.process.return_value = 0.1
        vad._model.reset = MagicMock()

        # 1) 5프레임(100ms) 이상 무음 → 충분한 RMS silence
        silence = bytes([0xFF] * 160)
        for _ in range(6):
            await vad.process(silence)
        assert vad._rms_silence_frames == 6

        # 2) 큰 소리 오디오 → Silero 리셋됨
        loud = bytes([0x00] * 160)
        await vad.process(loud)
        assert vad._rms_silence_frames == 0
        vad._model.reset.assert_called_once()

    @pytest.mark.asyncio
    async def test_silero_no_reset_on_brief_rms_silence(self):
        """짧은 RMS silence(1-2 frames)에서는 Silero 리셋하지 않음."""
        vad = self._make_vad(rms_threshold=150.0)
        vad._model.process.return_value = 0.1
        vad._model.reset = MagicMock()

        # 1) 2프레임만 무음 (음절 간 짧은 무음 시뮬레이션)
        silence = bytes([0xFF] * 160)
        for _ in range(2):
            await vad.process(silence)
        assert vad._rms_silence_frames == 2

        # 2) 큰 소리 오디오 → Silero 리셋 안 됨 (< 5 frames)
        loud = bytes([0x00] * 160)
        await vad.process(loud)
        vad._model.reset.assert_not_called()
        assert vad._rms_silence_frames == 0

    @pytest.mark.asyncio
    async def test_no_model_noop(self):
        """모델이 없으면 process가 no-op."""
        vad = self._make_vad()
        vad._model = None

        loud = bytes([0x10] * 160)
        await vad.process(loud)

        assert vad.is_speaking is False

    def test_reset(self):
        """reset()이 상태를 초기화한다."""
        from src.realtime.local_vad import _VadState

        vad = self._make_vad()
        vad._state = _VadState.SPEAKING
        vad._speech_count = 5
        vad._silence_count = 3
        vad._frame_buffer = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        vad._model.reset = MagicMock()

        vad.reset()

        assert vad.is_speaking is False
        assert vad._speech_count == 0
        assert vad._silence_count == 0
        assert len(vad._frame_buffer) == 0
        vad._model.reset.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_error_does_not_crash(self):
        """콜백 에러가 process를 중단하지 않는다."""
        async def bad_callback():
            raise RuntimeError("callback error")

        vad = self._make_vad(
            rms_threshold=0.0,
            speech_threshold=0.5,
            min_speech_frames=1,
            on_speech_start=bad_callback,
        )
        vad._model.process.return_value = 0.8

        loud = bytes([0x10] * 160)
        # 에러가 발생해도 process가 완료되어야 함
        for _ in range(4):
            await vad.process(loud)

        # 상태는 전환됨 (콜백 에러와 무관)
        assert vad.is_speaking is True
