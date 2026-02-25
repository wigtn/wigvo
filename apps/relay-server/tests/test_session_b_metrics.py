"""Session B 새 메트릭 필드 단위 테스트.

session_b_speech_durations_ms, session_b_processing_latencies_ms,
session_b_stt_after_stop_ms 기록 검증.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.realtime.sessions.session_b import (
    SessionBHandler,
    _normalize_for_blocklist,
    _STT_HALLUCINATION_BLOCKLIST,
    _MIN_E2E_MS,
)
from src.types import ActiveCall, CallMetrics, CallMode, CommunicationMode


def _make_call(**overrides) -> ActiveCall:
    defaults = dict(
        call_id="test-call-sb",
        mode=CallMode.RELAY,
        source_language="en",
        target_language="ko",
        communication_mode=CommunicationMode.VOICE_TO_VOICE,
    )
    defaults.update(overrides)
    return ActiveCall(**defaults)


def _make_session_mock():
    """RealtimeSession mock."""
    session = MagicMock()
    session.on = MagicMock()
    session.send_audio = AsyncMock()
    session.clear_input_buffer = AsyncMock()
    session.commit_audio_only = AsyncMock()
    session.create_response = AsyncMock()
    return session


def _make_handler(call=None, use_local_vad=False, **kwargs) -> SessionBHandler:
    """SessionBHandler 생성 (콜백 mock 포함)."""
    if call is None:
        call = _make_call()
    session = _make_session_mock()
    handler = SessionBHandler(
        session=session,
        call=call,
        on_translated_audio=AsyncMock(),
        on_caption=AsyncMock(),
        on_original_caption=AsyncMock(),
        on_recipient_speech_started=AsyncMock(),
        on_recipient_speech_stopped=AsyncMock(),
        on_transcript_complete=AsyncMock(),
        on_caption_done=AsyncMock(),
        use_local_vad=use_local_vad,
        **kwargs,
    )
    return handler


class TestNormalizeForBlocklist:
    """_normalize_for_blocklist 구두점 정규화 검증."""

    def test_strips_exclamation_mark(self):
        """느낌표가 제거되어 블록리스트에 매칭된다."""
        assert _normalize_for_blocklist("시청해주셔서 감사합니다!") == "시청해주셔서 감사합니다"
        assert _normalize_for_blocklist("시청해주셔서 감사합니다!") in _STT_HALLUCINATION_BLOCKLIST

    def test_strips_question_mark(self):
        """물음표가 제거된다."""
        assert _normalize_for_blocklist("MBC 뉴스 이덕영입니다?") == "MBC 뉴스 이덕영입니다"

    def test_strips_fullwidth_punctuation(self):
        """전각 구두점이 제거된다."""
        assert _normalize_for_blocklist("전해드립니다！") == "전해드립니다"
        assert _normalize_for_blocklist("밝혔습니다。") == "밝혔습니다"

    def test_preserves_ascii_period(self):
        """ASCII 마침표는 유지된다 (블록리스트에 . 포함 버전이 별도 등록)."""
        assert _normalize_for_blocklist("밝혔습니다.") == "밝혔습니다."

    def test_normal_text_unchanged(self):
        """일반 텍스트는 변경되지 않는다."""
        assert _normalize_for_blocklist("안녕하세요") == "안녕하세요"
        assert _normalize_for_blocklist("Hello, how are you?") == "Hello, how are you"

    def test_empty_and_whitespace(self):
        """빈 문자열/공백 처리."""
        assert _normalize_for_blocklist("") == ""
        assert _normalize_for_blocklist("  ") == ""


class TestSilenceTimeoutAntiHallucination:
    """Silence Timeout 경로의 anti-hallucination 수정 검증."""

    @pytest.mark.asyncio
    async def test_timeout_sets_speech_stopped_at(self):
        """Silence timeout이 _speech_stopped_at을 설정한다."""
        handler = _make_handler(use_local_vad=True)
        handler._speech_started_at = time.time() - 15.0
        handler._silence_timeout_s = 0.01  # 즉시 트리거

        await handler._silence_timeout_handler()

        assert handler._speech_stopped_at > 0

    @pytest.mark.asyncio
    async def test_timeout_clears_buffer_before_commit(self):
        """Silence timeout이 clear_input_buffer → commit_audio_only 순서로 호출한다."""
        handler = _make_handler(use_local_vad=True)
        handler._speech_started_at = time.time() - 15.0
        handler._silence_timeout_s = 0.01

        call_order = []
        handler.session.clear_input_buffer = AsyncMock(
            side_effect=lambda: call_order.append("clear")
        )
        handler.session.commit_audio_only = AsyncMock(
            side_effect=lambda: call_order.append("commit")
        )

        await handler._silence_timeout_handler()

        assert call_order == ["clear", "commit"]

    @pytest.mark.asyncio
    async def test_timeout_not_clear_buffer_for_server_vad(self):
        """Server VAD 모드에서는 clear_input_buffer를 호출하지 않는다."""
        handler = _make_handler(use_local_vad=False)
        handler._speech_started_at = time.time() - 15.0
        handler._silence_timeout_s = 0.01

        await handler._silence_timeout_handler()

        handler.session.clear_input_buffer.assert_not_called()
        handler.session.commit_audio_only.assert_not_called()


class TestSpeechStoppedTimestamp:
    """_speech_stopped_at 타임스탬프 기록 검증."""

    @pytest.mark.asyncio
    async def test_server_vad_sets_speech_stopped_at(self):
        """Server VAD speech_stopped에서 _speech_stopped_at가 설정된다."""
        handler = _make_handler()
        handler._speech_started_at = time.time() - 1.0  # 1초 전 시작
        handler._is_recipient_speaking = True

        await handler._handle_speech_stopped({})

        assert handler._speech_stopped_at > 0

    @pytest.mark.asyncio
    async def test_local_vad_sets_speech_stopped_at(self):
        """Local VAD notify_speech_stopped에서 _speech_stopped_at가 설정된다."""
        handler = _make_handler(use_local_vad=True)
        handler._speech_started_at = time.time() - 1.0
        handler._is_recipient_speaking = True

        await handler.notify_speech_stopped(peak_rms=1000.0)

        assert handler._speech_stopped_at > 0


class TestSpeechDurationsMs:
    """session_b_speech_durations_ms 기록 검증."""

    @pytest.mark.asyncio
    async def test_server_vad_records_speech_duration(self):
        """Server VAD에서 발화 길이가 기록된다."""
        call = _make_call()
        handler = _make_handler(call=call)
        handler._speech_started_at = time.time() - 1.5  # 1.5초 발화
        handler._is_recipient_speaking = True

        await handler._handle_speech_stopped({})

        assert len(call.call_metrics.session_b_speech_durations_ms) == 1
        duration = call.call_metrics.session_b_speech_durations_ms[0]
        assert 1400 < duration < 1600  # ~1500ms

    @pytest.mark.asyncio
    async def test_local_vad_records_speech_duration(self):
        """Local VAD에서 발화 길이가 기록된다."""
        call = _make_call()
        handler = _make_handler(call=call, use_local_vad=True)
        handler._speech_started_at = time.time() - 2.0
        handler._is_recipient_speaking = True

        await handler.notify_speech_stopped(peak_rms=1000.0)

        assert len(call.call_metrics.session_b_speech_durations_ms) == 1
        duration = call.call_metrics.session_b_speech_durations_ms[0]
        assert 1900 < duration < 2100

    @pytest.mark.asyncio
    async def test_short_speech_not_recorded(self):
        """최소 발화 길이 미만은 기록되지 않는다."""
        call = _make_call()
        handler = _make_handler(call=call)
        handler._speech_started_at = time.time() - 0.1  # 100ms (< min_speech_s)
        handler._is_recipient_speaking = True

        await handler._handle_speech_stopped({})

        assert len(call.call_metrics.session_b_speech_durations_ms) == 0


class TestProcessingLatenciesMs:
    """session_b_processing_latencies_ms 기록 검증."""

    @pytest.mark.asyncio
    async def test_processing_latency_recorded_on_translation_done(self):
        """번역 완료 시 processing latency가 기록된다."""
        call = _make_call()
        handler = _make_handler(call=call)
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5  # 0.5초 전 speech stopped

        await handler._save_transcript_and_notify("translated text")

        assert len(call.call_metrics.session_b_processing_latencies_ms) == 1
        proc = call.call_metrics.session_b_processing_latencies_ms[0]
        assert 400 < proc < 600  # ~500ms

    @pytest.mark.asyncio
    async def test_processing_latency_not_recorded_without_stop(self):
        """speech_stopped_at 없으면 processing latency 미기록."""
        call = _make_call()
        handler = _make_handler(call=call)
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = 0.0  # not set

        await handler._save_transcript_and_notify("translated text")

        assert len(call.call_metrics.session_b_processing_latencies_ms) == 0


class TestSttAfterStopMs:
    """session_b_stt_after_stop_ms 기록 검증."""

    @pytest.mark.asyncio
    async def test_stt_after_stop_recorded(self):
        """STT 완료가 speech_stopped 이후에 발생하면 지연이 기록된다."""
        call = _make_call()
        handler = _make_handler(call=call)
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.3  # 0.3초 전 stop

        await handler._handle_input_transcription_completed({"transcript": "테스트"})

        assert len(call.call_metrics.session_b_stt_after_stop_ms) == 1
        after = call.call_metrics.session_b_stt_after_stop_ms[0]
        assert 200 < after < 400  # ~300ms

    @pytest.mark.asyncio
    async def test_stt_after_stop_not_recorded_without_stop(self):
        """speech_stopped_at 없으면 stt_after_stop 미기록."""
        call = _make_call()
        handler = _make_handler(call=call)
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = 0.0

        await handler._handle_input_transcription_completed({"transcript": "테스트"})

        assert len(call.call_metrics.session_b_stt_after_stop_ms) == 0


class TestMinE2eFilter:
    """최소 E2E 임계값 필터 검증."""

    @pytest.mark.asyncio
    async def test_fast_response_blocked(self):
        """e2e < _MIN_E2E_MS인 응답은 할루시네이션으로 차단된다."""
        call = _make_call()
        handler = _make_handler(call=call)
        # 100ms 전 시작 → e2e ≈ 100ms < 500ms
        handler._committed_speech_started_at = time.time() - 0.1
        handler._committed_speech_stopped_at = time.time() - 0.05

        await handler._save_transcript_and_notify("hallucinated text")

        assert len(call.call_metrics.session_b_e2e_latencies_ms) == 0
        assert call.call_metrics.hallucinations_blocked == 1

    @pytest.mark.asyncio
    async def test_normal_response_passes(self):
        """e2e >= _MIN_E2E_MS인 응답은 정상 통과한다."""
        call = _make_call()
        handler = _make_handler(call=call)
        # 2초 전 시작 → e2e ≈ 2000ms > 500ms
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5

        await handler._save_transcript_and_notify("translated text")

        assert len(call.call_metrics.session_b_e2e_latencies_ms) == 1
        assert call.call_metrics.hallucinations_blocked == 0


class TestExistingMetricsUnchanged:
    """기존 session_b_e2e_latencies_ms, session_b_stt_latencies_ms 변경 없음."""

    @pytest.mark.asyncio
    async def test_e2e_still_recorded(self):
        """E2E latency는 여전히 기록된다."""
        call = _make_call()
        handler = _make_handler(call=call)
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5

        await handler._save_transcript_and_notify("translated text")

        assert len(call.call_metrics.session_b_e2e_latencies_ms) == 1

    @pytest.mark.asyncio
    async def test_stt_still_recorded(self):
        """STT latency는 여전히 기록된다."""
        call = _make_call()
        handler = _make_handler(call=call)
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.3

        await handler._handle_input_transcription_completed({"transcript": "테스트"})

        assert len(call.call_metrics.session_b_stt_latencies_ms) == 1


class TestTimestampReset:
    """번역 완료 후 타임스탬프 리셋 검증."""

    @pytest.mark.asyncio
    async def test_timestamps_reset_after_translation(self):
        """_save_transcript_and_notify 후 커밋 타임스탬프가 리셋된다."""
        call = _make_call()
        handler = _make_handler(call=call)
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5

        await handler._save_transcript_and_notify("translated text")

        assert handler._committed_speech_started_at == 0.0
        assert handler._committed_speech_stopped_at == 0.0


class TestCallMetricsFields:
    """CallMetrics 새 필드 존재 확인."""

    def test_new_fields_exist_in_model(self):
        """새 필드가 CallMetrics에 존재한다."""
        m = CallMetrics()
        assert hasattr(m, "session_b_speech_durations_ms")
        assert hasattr(m, "session_b_processing_latencies_ms")
        assert hasattr(m, "session_b_stt_after_stop_ms")
        assert m.session_b_speech_durations_ms == []
        assert m.session_b_processing_latencies_ms == []
        assert m.session_b_stt_after_stop_ms == []

    def test_new_fields_in_model_dump(self):
        """새 필드가 model_dump()에 포함된다."""
        m = CallMetrics()
        d = m.model_dump()
        assert "session_b_speech_durations_ms" in d
        assert "session_b_processing_latencies_ms" in d
        assert "session_b_stt_after_stop_ms" in d
