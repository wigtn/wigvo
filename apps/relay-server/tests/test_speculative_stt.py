"""Speculative STT 단위 테스트.

발화 중 조기 commit으로 Whisper STT를 선행 시작하는 기능 검증.
T2V/Agent Chat API 경로에서만 동작, V2V에서는 비활성.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.realtime.chat_translator import ChatTranslationResult, ChatTranslator
from src.realtime.sessions.session_b import SessionBHandler
from src.types import ActiveCall, CallMode, CommunicationMode


# ───────────────────────── helpers ─────────────────────────


def _make_call(**overrides) -> ActiveCall:
    defaults = dict(
        call_id="test-call-spec",
        mode=CallMode.RELAY,
        source_language="en",
        target_language="ko",
        communication_mode=CommunicationMode.TEXT_TO_VOICE,
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
    session.delete_item = AsyncMock()
    return session


def _make_chat_translator_mock(
    translated_text: str = "Hello",
    input_tokens: int = 10,
    output_tokens: int = 5,
    latency_ms: float = 120.0,
) -> AsyncMock:
    """ChatTranslator mock."""
    mock = AsyncMock(spec=ChatTranslator)
    mock.translate.return_value = ChatTranslationResult(
        translated_text=translated_text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )
    return mock


def _make_handler(
    call=None,
    use_local_vad: bool = False,
    chat_translator=None,
    **kwargs,
) -> SessionBHandler:
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
        chat_translator=chat_translator,
        **kwargs,
    )
    return handler


# ═══════════════════════════════════════════════════════════
#  Speculative STT 초기 상태
# ═══════════════════════════════════════════════════════════


class TestSpeculativeInitState:
    """초기 상태 변수 검증."""

    def test_task_is_none(self):
        handler = _make_handler(chat_translator=_make_chat_translator_mock())
        assert handler._speculative_stt_task is None

    def test_committed_is_false(self):
        handler = _make_handler(chat_translator=_make_chat_translator_mock())
        assert handler._speculative_committed is False


# ═══════════════════════════════════════════════════════════
#  Timer 시작/취소 (Server VAD)
# ═══════════════════════════════════════════════════════════


class TestSpeculativeTimerServerVAD:
    """Server VAD 경로에서 speculative timer 시작/취소 검증."""

    @pytest.mark.asyncio
    async def test_speech_started_creates_task(self):
        """speech_started 시 Chat API 경로에서 speculative task가 생성된다."""
        handler = _make_handler(chat_translator=_make_chat_translator_mock())
        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_enabled = True
            mock_settings.speculative_stt_delay_s = 100.0  # 발동 방지
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler._handle_speech_started({})
            assert handler._speculative_stt_task is not None
            assert not handler._speculative_stt_task.done()

            # cleanup
            handler._cancel_speculative_stt()

    @pytest.mark.asyncio
    async def test_speech_started_resets_committed_flag(self):
        """speech_started 시 _speculative_committed가 False로 리셋된다."""
        handler = _make_handler(chat_translator=_make_chat_translator_mock())
        handler._speculative_committed = True

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_enabled = True
            mock_settings.speculative_stt_delay_s = 100.0
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler._handle_speech_started({})
            assert handler._speculative_committed is False

            handler._cancel_speculative_stt()

    @pytest.mark.asyncio
    async def test_speech_stopped_cancels_task(self):
        """speech_stopped 시 speculative task가 취소된다."""
        handler = _make_handler(chat_translator=_make_chat_translator_mock())
        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_enabled = True
            mock_settings.speculative_stt_delay_s = 100.0
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler._handle_speech_started({})
            task = handler._speculative_stt_task
            assert task is not None

            # speech_stopped: 충분한 발화 길이
            handler._speech_started_at = time.time() - 2.0
            await handler._handle_speech_stopped({})

            await asyncio.sleep(0)  # 이벤트 루프 양보하여 취소 처리
            assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_disabled_setting_no_task(self):
        """speculative_stt_enabled=False이면 task가 생성되지 않는다."""
        handler = _make_handler(chat_translator=_make_chat_translator_mock())
        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_enabled = False
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler._handle_speech_started({})
            assert handler._speculative_stt_task is None


# ═══════════════════════════════════════════════════════════
#  Timer 시작/취소 (Local VAD)
# ═══════════════════════════════════════════════════════════


class TestSpeculativeTimerLocalVAD:
    """Local VAD 경로에서 speculative timer 시작/취소 검증."""

    @pytest.mark.asyncio
    async def test_notify_speech_started_creates_task(self):
        """notify_speech_started 시 speculative task가 생성된다."""
        handler = _make_handler(
            use_local_vad=True,
            chat_translator=_make_chat_translator_mock(),
        )
        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_enabled = True
            mock_settings.speculative_stt_delay_s = 100.0
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler.notify_speech_started()
            assert handler._speculative_stt_task is not None

            handler._cancel_speculative_stt()

    @pytest.mark.asyncio
    async def test_notify_speech_stopped_cancels_task(self):
        """notify_speech_stopped 시 speculative task가 취소된다."""
        handler = _make_handler(
            use_local_vad=True,
            chat_translator=_make_chat_translator_mock(),
        )
        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_enabled = True
            mock_settings.speculative_stt_delay_s = 100.0
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler.notify_speech_started()
            task = handler._speculative_stt_task

            handler._speech_started_at = time.time() - 2.0
            await handler.notify_speech_stopped(peak_rms=500.0)

            await asyncio.sleep(0)  # 이벤트 루프 양보하여 취소 처리
            assert task.cancelled() or task.done()


# ═══════════════════════════════════════════════════════════
#  V2V 모드 비활성 검증
# ═══════════════════════════════════════════════════════════


class TestSpeculativeV2VExcluded:
    """V2V 모드에서는 speculative STT가 비활성이다."""

    @pytest.mark.asyncio
    async def test_no_task_without_chat_translator(self):
        """chat_translator=None (V2V)이면 speculative task가 생성되지 않는다."""
        handler = _make_handler(chat_translator=None)
        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_enabled = True
            mock_settings.speculative_stt_delay_s = 100.0
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler._handle_speech_started({})
            assert handler._speculative_stt_task is None


# ═══════════════════════════════════════════════════════════
#  Speculative STT Handler 로직
# ═══════════════════════════════════════════════════════════


class TestSpeculativeHandler:
    """_speculative_stt_handler 핵심 동작 검증."""

    @pytest.mark.asyncio
    async def test_fires_commit_when_speaking(self):
        """발화 중이면 commit_audio_only가 호출되고 상태가 업데이트된다."""
        handler = _make_handler(chat_translator=_make_chat_translator_mock())
        handler._is_recipient_speaking = True

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_delay_s = 0.0  # 즉시 발동

            await handler._speculative_stt_handler()

        handler.session.commit_audio_only.assert_awaited_once()
        assert handler._speculative_committed is True
        assert handler._pending_stt_count == 1

    @pytest.mark.asyncio
    async def test_skips_when_not_speaking(self):
        """발화 종료 상태면 commit하지 않는다."""
        handler = _make_handler(chat_translator=_make_chat_translator_mock())
        handler._is_recipient_speaking = False

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_delay_s = 0.0

            await handler._speculative_stt_handler()

        handler.session.commit_audio_only.assert_not_awaited()
        assert handler._speculative_committed is False

    @pytest.mark.asyncio
    async def test_increments_metric(self):
        """speculative_stt_count 메트릭이 증가한다."""
        call = _make_call()
        handler = _make_handler(call=call, chat_translator=_make_chat_translator_mock())
        handler._is_recipient_speaking = True

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_delay_s = 0.0

            await handler._speculative_stt_handler()

        assert call.call_metrics.speculative_stt_count == 1

    @pytest.mark.asyncio
    async def test_skips_without_chat_translator(self):
        """chat_translator가 None이면 commit하지 않는다 (guard 동작)."""
        handler = _make_handler(chat_translator=None)
        handler._is_recipient_speaking = True
        # handler._chat_translator는 None

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_delay_s = 0.0

            await handler._speculative_stt_handler()

        handler.session.commit_audio_only.assert_not_awaited()


# ═══════════════════════════════════════════════════════════
#  Debounce 분기 로직 (speculative_committed 플래그)
# ═══════════════════════════════════════════════════════════


class TestDebouncedResponseSpeculative:
    """_debounced_create_response의 speculative 분기 검증."""

    @pytest.mark.asyncio
    async def test_speculative_committed_local_vad_commits_remaining(self):
        """speculative_committed=True + Local VAD: 나머지 오디오를 수동 commit한다."""
        handler = _make_handler(
            use_local_vad=True,
            chat_translator=_make_chat_translator_mock(),
        )
        handler._speculative_committed = True
        handler._speech_started_at = time.time() - 2.0
        handler._speech_stopped_at = time.time()
        handler._committed_speech_started_at = handler._speech_started_at
        handler._committed_speech_stopped_at = handler._speech_stopped_at

        # STT를 미리 준비하여 _translate_via_chat_api가 블록되지 않게
        handler._stt_texts = ["앞부분"]
        handler._pending_stt_count = 1  # speculative commit에서 이미 1
        handler._stt_ready_event.clear()

        # _translate_via_chat_api를 mock하여 전체 번역 흐름 bypass
        handler._translate_via_chat_api = AsyncMock()

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_delay_s = 1.5

            await handler._debounced_create_response()

        # Local VAD이므로 commit_audio_only 호출 (나머지 오디오)
        handler.session.commit_audio_only.assert_awaited_once()
        # pending 카운트가 1 증가 (speculative 1 + final 1 = 2)
        assert handler._pending_stt_count == 2
        handler._translate_via_chat_api.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_speculative_committed_server_vad_no_manual_commit(self):
        """speculative_committed=True + Server VAD: debounce에서 추가 증가 없음.

        Server VAD는 speech_stopped에서 auto-commit + count 사전 증가됨.
        """
        handler = _make_handler(
            use_local_vad=False,
            chat_translator=_make_chat_translator_mock(),
        )
        handler._speculative_committed = True
        handler._speech_started_at = time.time() - 2.0
        handler._speech_stopped_at = time.time()
        handler._committed_speech_started_at = handler._speech_started_at
        handler._committed_speech_stopped_at = handler._speech_stopped_at

        handler._stt_texts = ["앞부분"]
        # speculative=1, speech_stopped 사전 증가=1 → 이미 2
        handler._pending_stt_count = 2
        handler._stt_ready_event.clear()

        handler._translate_via_chat_api = AsyncMock()

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_delay_s = 1.5

            await handler._debounced_create_response()

        # Server VAD: commit 없음, 카운터 증가 없음 (speech_stopped에서 처리됨)
        handler.session.commit_audio_only.assert_not_awaited()
        assert handler._pending_stt_count == 2
        handler._translate_via_chat_api.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_short_speech_no_speculative_local_vad(self):
        """짧은 발화 (speculative 미발동) + Local VAD: 기존 경로 동작."""
        handler = _make_handler(
            use_local_vad=True,
            chat_translator=_make_chat_translator_mock(),
        )
        handler._speculative_committed = False  # speculative 미발동
        handler._speech_started_at = time.time() - 1.0
        handler._speech_stopped_at = time.time()
        handler._committed_speech_started_at = handler._speech_started_at
        handler._committed_speech_stopped_at = handler._speech_stopped_at

        handler._translate_via_chat_api = AsyncMock()

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_delay_s = 1.5

            await handler._debounced_create_response()

        # 기존 Local VAD 경로: commit 1회 + pending 1
        handler.session.commit_audio_only.assert_awaited_once()
        assert handler._pending_stt_count == 1
        handler._translate_via_chat_api.assert_awaited_once()


# ═══════════════════════════════════════════════════════════
#  stop() 정리
# ═══════════════════════════════════════════════════════════


class TestSpeculativeCleanup:
    """stop() 호출 시 speculative task 정리 검증."""

    @pytest.mark.asyncio
    async def test_stop_cancels_speculative_task(self):
        """stop() 호출 시 실행 중인 speculative task가 취소된다."""
        handler = _make_handler(chat_translator=_make_chat_translator_mock())

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_enabled = True
            mock_settings.speculative_stt_delay_s = 100.0
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler._handle_speech_started({})
            task = handler._speculative_stt_task
            assert task is not None

            handler.stop()
            await asyncio.sleep(0)  # 이벤트 루프 양보하여 취소 처리
            assert task.cancelled() or task.done()
            assert handler._speculative_stt_task is None


# ═══════════════════════════════════════════════════════════
#  Race Condition 방지: Server VAD speech_stopped 사전 카운터 증가
# ═══════════════════════════════════════════════════════════


class TestServerVADPreIncrement:
    """Server VAD speech_stopped에서 _pending_stt_count 사전 증가 검증.

    Server VAD auto-commit의 STT가 debounce(300ms) 전에 도착하는
    race condition을 방지하기 위해 speech_stopped 시점에 즉시 카운터를 증가한다.
    """

    @pytest.mark.asyncio
    async def test_speech_stopped_increments_count_when_speculative(self):
        """speculative committed + Server VAD: speech_stopped에서 즉시 count 증가."""
        handler = _make_handler(
            use_local_vad=False,
            chat_translator=_make_chat_translator_mock(),
        )
        handler._speculative_committed = True
        handler._pending_stt_count = 1  # speculative commit에서 이미 1
        handler._speech_started_at = time.time() - 2.0

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler._handle_speech_stopped({})

        # speech_stopped에서 즉시 count 증가 (debounce 전)
        assert handler._pending_stt_count == 2

    @pytest.mark.asyncio
    async def test_speech_stopped_no_increment_without_speculative(self):
        """speculative 미발동: speech_stopped에서 count 증가 없음."""
        handler = _make_handler(
            use_local_vad=False,
            chat_translator=_make_chat_translator_mock(),
        )
        handler._speculative_committed = False
        handler._pending_stt_count = 0
        handler._speech_started_at = time.time() - 2.0

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler._handle_speech_stopped({})

        assert handler._pending_stt_count == 0

    @pytest.mark.asyncio
    async def test_speech_stopped_no_increment_for_local_vad(self):
        """Local VAD: speech_stopped에서 count 증가 없음 (수동 commit은 debounce에서)."""
        handler = _make_handler(
            use_local_vad=True,
            chat_translator=_make_chat_translator_mock(),
        )
        handler._speculative_committed = True
        handler._pending_stt_count = 1
        handler._speech_started_at = time.time() - 2.0

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.session_b_min_speech_ms = 250
            mock_settings.session_b_min_peak_rms = 300

            await handler.notify_speech_stopped(peak_rms=500.0)

        # Local VAD: notify_speech_stopped에서는 auto-commit 없으므로 증가 없음
        # (debounce에서 수동 commit 시 증가)
        assert handler._pending_stt_count == 1


# ═══════════════════════════════════════════════════════════
#  Silence Timeout + Speculative 상호작용
# ═══════════════════════════════════════════════════════════


class TestSilenceTimeoutSpeculative:
    """Silence timeout 발동 시 speculative committed 상태 처리 검증."""

    @pytest.mark.asyncio
    async def test_timeout_commits_remaining_for_server_vad_speculative(self):
        """Server VAD + speculative committed: timeout이 나머지 오디오를 강제 commit."""
        handler = _make_handler(
            use_local_vad=False,
            chat_translator=_make_chat_translator_mock(),
        )
        handler._speculative_committed = True
        handler._pending_stt_count = 1  # speculative에서 이미 1
        handler._speech_started_at = time.time() - 2.0
        handler._silence_timeout_s = 0.0  # 즉시 발동

        handler._translate_via_chat_api = AsyncMock()

        await handler._silence_timeout_handler()

        # Server VAD + speculative: 강제 commit으로 나머지 오디오 처리
        handler.session.commit_audio_only.assert_awaited_once()
        assert handler._pending_stt_count == 2
        handler._translate_via_chat_api.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_preserves_audio_for_local_vad_speculative(self):
        """Local VAD + speculative committed: timeout이 Part 2 오디오를 보존한다.

        speculative 미발동 시에는 clear_input_buffer로 노이즈 제거하지만,
        speculative 발동 시에는 Part 2 오디오가 버퍼에 있으므로 clear 하지 않는다.
        """
        handler = _make_handler(
            use_local_vad=True,
            chat_translator=_make_chat_translator_mock(),
        )
        handler._speculative_committed = True
        handler._pending_stt_count = 1
        handler._speech_started_at = time.time() - 2.0
        handler._silence_timeout_s = 0.0

        handler._translate_via_chat_api = AsyncMock()

        await handler._silence_timeout_handler()

        # clear_input_buffer 미호출 (Part 2 오디오 보존)
        handler.session.clear_input_buffer.assert_not_awaited()
        # commit은 호출 (Part 2 STT 트리거)
        handler.session.commit_audio_only.assert_awaited_once()
        assert handler._pending_stt_count == 2

    @pytest.mark.asyncio
    async def test_timeout_clears_buffer_for_local_vad_no_speculative(self):
        """Local VAD + speculative 미발동: timeout이 노이즈 버퍼를 정리한다."""
        handler = _make_handler(
            use_local_vad=True,
            chat_translator=_make_chat_translator_mock(),
        )
        handler._speculative_committed = False
        handler._speech_started_at = time.time() - 2.0
        handler._silence_timeout_s = 0.0

        handler._translate_via_chat_api = AsyncMock()

        await handler._silence_timeout_handler()

        # 노이즈 제거 → commit
        handler.session.clear_input_buffer.assert_awaited_once()
        handler.session.commit_audio_only.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_no_extra_commit_for_server_vad_no_speculative(self):
        """Server VAD + speculative 미발동: timeout이 추가 commit 없이 번역."""
        handler = _make_handler(
            use_local_vad=False,
            chat_translator=_make_chat_translator_mock(),
        )
        handler._speculative_committed = False
        handler._speech_started_at = time.time() - 2.0
        handler._silence_timeout_s = 0.0

        handler._translate_via_chat_api = AsyncMock()

        await handler._silence_timeout_handler()

        # speculative 미발동 + Server VAD: 기존 동작 (commit 없음, 기존 STT 대기)
        handler.session.commit_audio_only.assert_not_awaited()
        handler._translate_via_chat_api.assert_awaited_once()


# ═══════════════════════════════════════════════════════════
#  카운터 프로토콜 정합성 (_pending_stt_count lifecycle)
# ═══════════════════════════════════════════════════════════


class TestPendingCountProtocol:
    """_pending_stt_count의 증감 프로토콜 검증.

    speculative commit(Part 1) + final commit(Part 2) → 두 STT 모두 도착 후
    count가 0이 되어 _stt_ready_event가 set되는 전체 흐름.
    """

    @pytest.mark.asyncio
    async def test_decrement_after_speculative_commit(self):
        """speculative commit 후 STT 도착 시 count가 정상 감소한다."""
        handler = _make_handler(chat_translator=_make_chat_translator_mock())
        handler._is_recipient_speaking = True
        handler._pending_stt_count = 0

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_delay_s = 0.0

            await handler._speculative_stt_handler()

        assert handler._pending_stt_count == 1
        assert not handler._stt_ready_event.is_set()

        # STT Part 1 도착 시뮬레이션
        handler._decrement_pending_stt()
        assert handler._pending_stt_count == 0
        assert handler._stt_ready_event.is_set()

    @pytest.mark.asyncio
    async def test_full_two_part_counter_lifecycle(self):
        """Part 1 (speculative) + Part 2 (final) → count 0까지 전체 라이프사이클."""
        handler = _make_handler(
            use_local_vad=True,
            chat_translator=_make_chat_translator_mock(),
        )

        # Step 1: Speculative commit → count=1
        handler._is_recipient_speaking = True
        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_delay_s = 0.0
            await handler._speculative_stt_handler()

        assert handler._pending_stt_count == 1
        assert handler._speculative_committed is True

        # Step 2: Part 1 STT 도착 → count=0, event set
        handler._decrement_pending_stt()
        assert handler._pending_stt_count == 0
        assert handler._stt_ready_event.is_set()

        # Step 3: debounce에서 Local VAD final commit → count=1, event clear
        handler._speculative_committed = True
        handler._speech_started_at = time.time() - 2.0
        handler._speech_stopped_at = time.time()
        handler._committed_speech_started_at = handler._speech_started_at
        handler._committed_speech_stopped_at = handler._speech_stopped_at
        handler._translate_via_chat_api = AsyncMock()

        with patch("src.realtime.sessions.session_b.settings") as mock_settings:
            mock_settings.speculative_stt_delay_s = 1.5
            await handler._debounced_create_response()

        assert handler._pending_stt_count == 1
        assert not handler._stt_ready_event.is_set()

        # Step 4: Part 2 STT 도착 → count=0
        handler._decrement_pending_stt()
        assert handler._pending_stt_count == 0
        assert handler._stt_ready_event.is_set()
