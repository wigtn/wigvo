"""EchoGateManager 단위 테스트.

EchoGateManager의 독립 동작을 검증한다:
  - Echo window 활성화/비활성화
  - filter_audio: 에너지 기반 필터링 + gate break
  - 동적 cooldown: max cap 적용/미적용
  - Post-echo settling: AGC 안정화 대기
  - on_recipient_speech: 즉시 해제 (settling 포함)
  - stop: cooldown task 취소
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.realtime.pipeline.echo_gate import EchoGateManager


def _make_call_metrics():
    """CallMetrics mock을 생성한다."""
    metrics = MagicMock()
    metrics.echo_suppressions = 0
    metrics.echo_gate_breakthroughs = 0
    return metrics


def _make_echo_gate(
    echo_margin_s: float = 0.3,
    max_echo_window_s: float | None = 1.2,
) -> tuple[EchoGateManager, MagicMock, MagicMock]:
    """EchoGateManager + mock session_b + mock call_metrics를 생성한다."""
    session_b = MagicMock()
    session_b.clear_input_buffer = AsyncMock()
    call_metrics = _make_call_metrics()
    gate = EchoGateManager(
        session_b=session_b,
        local_vad=None,
        call_metrics=call_metrics,
        echo_margin_s=echo_margin_s,
        max_echo_window_s=max_echo_window_s,
    )
    return gate, session_b, call_metrics


class TestEchoGateActivation:
    """Echo window 활성화/비활성화 테스트."""

    def test_initial_state(self):
        """초기 상태: echo window 비활성."""
        gate, _, _ = _make_echo_gate()
        assert gate.in_echo_window is False
        assert gate.is_suppressing is False

    def test_activate(self):
        """_activate() → in_echo_window = True."""
        gate, _, metrics = _make_echo_gate()
        gate._activate()
        assert gate.in_echo_window is True
        assert gate.is_suppressing is True
        assert metrics.echo_suppressions == 1

    def test_activate_idempotent(self):
        """이미 활성 상태에서 _activate() → 카운터 증가 없음."""
        gate, _, metrics = _make_echo_gate()
        gate._activate()
        gate._activate()
        assert metrics.echo_suppressions == 1

    def test_deactivate(self):
        """_deactivate() → in_echo_window = False + TTS 추적 리셋."""
        gate, _, _ = _make_echo_gate()
        gate._tts_first_chunk_at = time.time()
        gate._tts_total_bytes = 1000
        gate._activate()

        gate._deactivate()

        assert gate.in_echo_window is False
        assert gate._tts_first_chunk_at == 0.0
        assert gate._tts_total_bytes == 0

    def test_activate_cancels_existing_cooldown(self):
        """_activate() → 기존 cooldown task 취소."""
        gate, _, _ = _make_echo_gate()
        old_task = MagicMock()
        old_task.done.return_value = False
        gate._echo_cooldown_task = old_task

        gate._activate()

        old_task.cancel.assert_called_once()
        assert gate._echo_cooldown_task is None


class TestOnTtsChunk:
    """on_tts_chunk 메서드 테스트."""

    def test_first_chunk_returns_true(self):
        """첫 번째 청크 → True 반환 + echo window 활성화."""
        gate, _, _ = _make_echo_gate()
        result = gate.on_tts_chunk(100)
        assert result is True
        assert gate.in_echo_window is True
        assert gate._tts_total_bytes == 100

    def test_subsequent_chunks_return_false(self):
        """두 번째 이후 청크 → False 반환 + 바이트 누적."""
        gate, _, _ = _make_echo_gate()
        gate.on_tts_chunk(100)
        result = gate.on_tts_chunk(200)
        assert result is False
        assert gate._tts_total_bytes == 300


class TestFilterAudio:
    """filter_audio 메서드 테스트."""

    def test_passthrough_outside_echo_window(self):
        """Echo window 비활성 → 원본 그대로 전달."""
        gate, _, _ = _make_echo_gate()
        audio = bytes([0x10] * 160)
        result = gate.filter_audio(audio)
        assert result == audio

    def test_silence_during_echo_window_low_energy(self):
        """Echo window 중 저에너지 → mu-law silence(0xFF)."""
        gate, _, _ = _make_echo_gate()
        gate.in_echo_window = True
        audio = bytes([0xFE] * 160)  # 저에너지 (RMS ~2)
        result = gate.filter_audio(audio)
        assert all(b == 0xFF for b in result)
        assert len(result) == 160

    def test_high_rms_breaks_gate(self):
        """Echo window 중 고에너지 → gate break + 원본 전달."""
        gate, _, metrics = _make_echo_gate()
        gate.in_echo_window = True
        audio = bytes([0x10] * 160)  # 고에너지 (RMS ~3999)
        result = gate.filter_audio(audio)
        assert result == audio
        assert gate.in_echo_window is False
        assert metrics.echo_gate_breakthroughs == 1


class TestCooldown:
    """동적 cooldown 타이머 테스트."""

    @pytest.mark.asyncio
    async def test_cooldown_with_max_cap(self):
        """V2V: cooldown이 max_echo_window_s(1.2s)로 cap된다."""
        gate, session_b, _ = _make_echo_gate(echo_margin_s=0.3, max_echo_window_s=1.2)
        # 긴 TTS 시뮬레이션: 16000 bytes = 2.0s
        gate._tts_first_chunk_at = time.time()
        gate._tts_total_bytes = 16000
        gate._activate()

        gate.on_tts_done()
        # cooldown = min(2.0 + 0.3, 1.2) = 1.2s
        await asyncio.sleep(0.5)
        assert gate.in_echo_window is True  # 아직 cooldown 중
        await asyncio.sleep(1.0)
        assert gate.in_echo_window is False  # 1.2s 후 해제

    @pytest.mark.asyncio
    async def test_cooldown_without_max_cap(self):
        """T2V: max cap 없이 전체 cooldown 적용."""
        gate, session_b, _ = _make_echo_gate(echo_margin_s=0.5, max_echo_window_s=None)
        # 짧은 TTS: 800 bytes = 0.1s
        gate._tts_first_chunk_at = time.time()
        gate._tts_total_bytes = 800
        gate._activate()

        gate.on_tts_done()
        # cooldown = 0.1 + 0.5 = 0.6s (cap 없음)
        await asyncio.sleep(0.3)
        assert gate.in_echo_window is True
        await asyncio.sleep(0.6)
        assert gate.in_echo_window is False

    @pytest.mark.asyncio
    async def test_cooldown_clears_buffer_and_resets_vad(self):
        """Cooldown 완료 시 session_b.clear_input_buffer + local_vad.reset_state 호출."""
        local_vad = MagicMock()
        local_vad.reset_state = MagicMock()
        session_b = MagicMock()
        session_b.clear_input_buffer = AsyncMock()
        gate = EchoGateManager(
            session_b=session_b,
            local_vad=local_vad,
            call_metrics=_make_call_metrics(),
            echo_margin_s=0.1,
            max_echo_window_s=0.5,
        )
        gate._tts_first_chunk_at = time.time()
        gate._tts_total_bytes = 100
        gate._activate()

        gate.on_tts_done()
        await asyncio.sleep(0.8)

        assert gate.in_echo_window is False
        session_b.clear_input_buffer.assert_called_once()
        local_vad.reset_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_settling_after_cooldown(self):
        """Echo window 종료 후 settling 기간 동안 is_suppressing = True."""
        gate, _, _ = _make_echo_gate(echo_margin_s=0.1, max_echo_window_s=0.5)
        # 40000 bytes = 5s TTS → dynamic settling = max(0.5, min(5*0.3=1.5, 1.5)) = 1.5s
        gate._tts_first_chunk_at = time.time()
        gate._tts_total_bytes = 40000
        gate._activate()

        gate.on_tts_done()
        await asyncio.sleep(0.8)

        # Echo window는 닫혔지만 settling 중이므로 is_suppressing = True
        assert gate.in_echo_window is False
        assert gate.is_suppressing is True

    @pytest.mark.asyncio
    async def test_settling_expires(self):
        """Settling 만료 후 is_suppressing = False."""
        gate, _, _ = _make_echo_gate(echo_margin_s=0.1, max_echo_window_s=0.3)
        gate._tts_first_chunk_at = time.time()
        gate._tts_total_bytes = 100
        gate._activate()

        gate.on_tts_done()
        await asyncio.sleep(0.5)  # cooldown 완료
        assert gate.in_echo_window is False
        assert gate.is_suppressing is True  # settling 중

        await asyncio.sleep(0.7)  # settling 만료
        assert gate.is_suppressing is False

    @pytest.mark.asyncio
    async def test_recipient_speech_clears_settling(self):
        """수신자 발화 → settling 즉시 해제."""
        gate, _, _ = _make_echo_gate(echo_margin_s=0.1, max_echo_window_s=0.3)
        gate._tts_first_chunk_at = time.time()
        gate._tts_total_bytes = 100
        gate._activate()

        gate.on_tts_done()
        await asyncio.sleep(0.5)  # cooldown 완료, settling 중
        assert gate.in_echo_window is False
        assert gate.is_suppressing is True

        gate.on_recipient_speech()
        assert gate.is_suppressing is False
        assert gate._settling_until == 0.0


class TestRecipientSpeech:
    """수신자 발화 감지 테스트."""

    def test_on_recipient_speech_deactivates(self):
        """수신자 발화 → echo window 즉시 해제."""
        gate, _, _ = _make_echo_gate()
        gate._activate()
        assert gate.in_echo_window is True

        gate.on_recipient_speech()

        assert gate.in_echo_window is False
        assert gate._tts_first_chunk_at == 0.0
        assert gate._tts_total_bytes == 0

    def test_on_recipient_speech_cancels_cooldown(self):
        """수신자 발화 → 진행 중인 cooldown task 취소."""
        gate, _, _ = _make_echo_gate()
        task = MagicMock()
        task.done.return_value = False
        gate._echo_cooldown_task = task

        gate.on_recipient_speech()

        task.cancel.assert_called_once()
        assert gate._echo_cooldown_task is None


class TestStop:
    """stop() 리소스 정리 테스트."""

    @pytest.mark.asyncio
    async def test_stop_cancels_cooldown_task(self):
        """stop() → cooldown task 취소."""
        gate, _, _ = _make_echo_gate()
        gate._tts_first_chunk_at = time.time()
        gate._tts_total_bytes = 8000
        gate._activate()
        gate.on_tts_done()

        assert gate._echo_cooldown_task is not None

        await gate.stop()

        assert gate._echo_cooldown_task.cancelled()

    @pytest.mark.asyncio
    async def test_stop_noop_without_task(self):
        """cooldown task 없을 때 stop()은 안전하게 no-op."""
        gate, _, _ = _make_echo_gate()
        await gate.stop()  # 에러 없이 완료


class TestShouldProcessVad:
    """should_process_vad RMS pre-gate 테스트."""

    def test_echo_window_always_false(self):
        """Echo window 중 → RMS 무관 False."""
        gate, _, _ = _make_echo_gate()
        gate.in_echo_window = True
        assert gate.should_process_vad(1000.0) is False

    def test_settling_low_rms_false(self):
        """Settling 중 RMS 100 → False (배경 소음 차단)."""
        gate, _, _ = _make_echo_gate()
        gate._settling_until = time.time() + 10.0  # 10초 settling 강제
        assert gate.should_process_vad(100.0) is False

    def test_settling_high_rms_true(self):
        """Settling 중 RMS 600 → True (VAD 처리 허용)."""
        gate, _, _ = _make_echo_gate()
        gate._settling_until = time.time() + 10.0
        assert gate.should_process_vad(600.0) is True

    def test_settling_uses_lower_rms_threshold(self):
        """Settling 중 RMS 300이 VAD에 통과 (settling 전용 임계값 200 사용)."""
        gate, _, _ = _make_echo_gate()
        gate._settling_until = time.time() + 10.0
        assert gate.should_process_vad(300.0) is True

    def test_echo_window_uses_high_rms_threshold(self):
        """Echo window 중 RMS 300은 차단 (echo_energy_threshold_rms=500 사용)."""
        gate, _, _ = _make_echo_gate()
        gate.in_echo_window = True
        # Echo window에서는 should_process_vad가 항상 False
        assert gate.should_process_vad(300.0) is False
        # filter_audio는 echo_energy_threshold_rms(500) 기준으로 silence 처리
        audio = bytes([0xF0] * 160)  # RMS ~300 수준
        result = gate.filter_audio(audio)
        assert all(b == 0xFF for b in result)  # 500 미만이므로 silence

    def test_after_settling(self):
        """Settling 만료 → RMS 무관 True."""
        gate, _, _ = _make_echo_gate()
        gate._settling_until = time.time() - 1.0  # 이미 만료
        assert gate.should_process_vad(100.0) is True


class TestBreakSettling:
    """break_settling 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_clears_settling_with_grace(self):
        """break_settling() → 100ms grace period 후 is_suppressing=False."""
        local_vad = MagicMock()
        local_vad.force_speaking_state = MagicMock()
        session_b = MagicMock()
        session_b.clear_input_buffer = AsyncMock()
        gate = EchoGateManager(
            session_b=session_b,
            local_vad=local_vad,
            call_metrics=_make_call_metrics(),
            echo_margin_s=0.3,
            max_echo_window_s=1.2,
        )
        gate._settling_until = time.time() + 10.0
        gate._settling_started_at = time.time()
        assert gate.is_suppressing is True

        await gate.break_settling()

        # Grace period 중이므로 아직 suppressing
        assert gate.is_suppressing is True
        assert gate._settling_broken is True

        # 150ms 대기 → grace period 만료
        await asyncio.sleep(0.15)
        assert gate.is_suppressing is False

    @pytest.mark.asyncio
    async def test_counts_metric(self):
        """break_settling() → settling_breakthroughs 메트릭 증가."""
        gate, session_b, metrics = _make_echo_gate()
        metrics.settling_breakthroughs = 0
        gate._settling_until = time.time() + 10.0
        gate._settling_started_at = time.time()

        await gate.break_settling()

        assert metrics.settling_breakthroughs == 1

    @pytest.mark.asyncio
    async def test_noop_when_not_settling(self):
        """Settling 아닐 때 break_settling() → no-op."""
        gate, session_b, metrics = _make_echo_gate()
        metrics.settling_breakthroughs = 0
        gate._settling_until = 0.0

        await gate.break_settling()

        assert metrics.settling_breakthroughs == 0

    @pytest.mark.asyncio
    async def test_break_settling_clears_buffer(self):
        """break_settling() → session_b.clear_input_buffer() 호출."""
        session_b = MagicMock()
        session_b.clear_input_buffer = AsyncMock()
        gate = EchoGateManager(
            session_b=session_b,
            local_vad=None,
            call_metrics=_make_call_metrics(),
            echo_margin_s=0.3,
            max_echo_window_s=1.2,
        )
        gate._settling_until = time.time() + 10.0
        gate._settling_started_at = time.time()

        await gate.break_settling()

        session_b.clear_input_buffer.assert_called_once()

    @pytest.mark.asyncio
    async def test_break_settling_uses_force_speaking(self):
        """break_settling() → local_vad.force_speaking_state() 호출 (SPEAKING 유지)."""
        local_vad = MagicMock()
        local_vad.force_speaking_state = MagicMock()
        session_b = MagicMock()
        session_b.clear_input_buffer = AsyncMock()
        gate = EchoGateManager(
            session_b=session_b,
            local_vad=local_vad,
            call_metrics=_make_call_metrics(),
            echo_margin_s=0.3,
            max_echo_window_s=1.2,
        )
        gate._settling_until = time.time() + 10.0
        gate._settling_started_at = time.time()

        await gate.break_settling()

        local_vad.force_speaking_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_break_settling_grace_period(self):
        """break_settling() 후 100ms 동안 is_suppressing=True."""
        gate, session_b, _ = _make_echo_gate()
        gate._settling_until = time.time() + 10.0
        gate._settling_started_at = time.time()

        await gate.break_settling()

        # Grace period 중
        assert gate.is_suppressing is True
        await asyncio.sleep(0.05)
        assert gate.is_suppressing is True  # 50ms — 아직 grace 중

        await asyncio.sleep(0.1)
        assert gate.is_suppressing is False  # 150ms — grace 만료

    @pytest.mark.asyncio
    async def test_break_settling_no_reentry(self):
        """2번째 break_settling() 호출 → no-op (_settling_broken 플래그)."""
        local_vad = MagicMock()
        local_vad.force_speaking_state = MagicMock()
        session_b = MagicMock()
        session_b.clear_input_buffer = AsyncMock()
        gate = EchoGateManager(
            session_b=session_b,
            local_vad=local_vad,
            call_metrics=_make_call_metrics(),
            echo_margin_s=0.3,
            max_echo_window_s=1.2,
        )
        metrics = gate._call_metrics
        metrics.settling_breakthroughs = 0
        gate._settling_until = time.time() + 10.0
        gate._settling_started_at = time.time()

        await gate.break_settling()
        assert metrics.settling_breakthroughs == 1
        assert session_b.clear_input_buffer.call_count == 1

        # 2번째 호출 → no-op
        await gate.break_settling()
        assert metrics.settling_breakthroughs == 1  # 증가 없음
        assert session_b.clear_input_buffer.call_count == 1  # 추가 호출 없음

    def test_deactivate_resets_broken_flag(self):
        """_deactivate() → _settling_broken=False."""
        gate, _, _ = _make_echo_gate()
        gate._settling_broken = True
        gate._activate()

        gate._deactivate()

        assert gate._settling_broken is False


class TestDynamicSettling:
    """동적 settling 시간 테스트."""

    @pytest.mark.asyncio
    async def test_short_tts_min_settling(self):
        """짧은 TTS (0.5s) → settling = min (0.5s)."""
        gate, _, _ = _make_echo_gate(echo_margin_s=0.1, max_echo_window_s=0.3)
        # 0.5s TTS = 4000 bytes (g711_ulaw @ 8kHz)
        gate._tts_first_chunk_at = time.time()
        gate._tts_total_bytes = 4000
        gate._activate()

        gate.on_tts_done()
        await asyncio.sleep(0.5)  # cooldown 완료

        # settling = max(0.5, min(0.5*0.3=0.15, 1.5)) = 0.5
        assert gate.in_echo_window is False
        assert gate.is_suppressing is True
        # 0.5s settling이므로 0.7s 후 만료
        await asyncio.sleep(0.7)
        assert gate.is_suppressing is False

    @pytest.mark.asyncio
    async def test_long_tts_max_settling(self):
        """긴 TTS (5s) → settling = max (1.5s)."""
        gate, _, _ = _make_echo_gate(echo_margin_s=0.1, max_echo_window_s=0.3)
        # 5s TTS = 40000 bytes
        gate._tts_first_chunk_at = time.time()
        gate._tts_total_bytes = 40000
        gate._activate()

        gate.on_tts_done()
        await asyncio.sleep(0.5)  # cooldown 완료

        # settling = max(0.5, min(5.0*0.3=1.5, 1.5)) = 1.5
        assert gate.in_echo_window is False
        assert gate.is_suppressing is True
        # 1.5s settling이므로 1.0s 후에는 아직 suppressing
        await asyncio.sleep(1.0)
        assert gate.is_suppressing is True
        # 0.8s 더 대기하면 만료 (총 1.8s > 1.5s)
        await asyncio.sleep(0.8)
        assert gate.is_suppressing is False


class TestInEchoWindowProperty:
    """in_echo_window property setter 테스트."""

    def test_setter(self):
        """in_echo_window setter로 직접 상태 변경."""
        gate, _, _ = _make_echo_gate()
        gate.in_echo_window = True
        assert gate.in_echo_window is True
        assert gate.is_suppressing is True

        gate.in_echo_window = False
        assert gate.in_echo_window is False
        assert gate.is_suppressing is False
