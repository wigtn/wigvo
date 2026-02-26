"""ChatTranslator + Session B Chat API 경로 단위 테스트.

ChatTranslator: GPT-4o-mini 기반 텍스트 번역 (T2V/Agent 모드).
Session B Chat API path: STT→Chat API 번역→캡션 전송 파이프라인.
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
        call_id="test-call",
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
    returns_none: bool = False,
) -> AsyncMock:
    """ChatTranslator mock (translate() 결과 제어)."""
    mock = AsyncMock(spec=ChatTranslator)
    if returns_none:
        mock.translate.return_value = None
    else:
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
#  Part 1: ChatTranslator 단위 테스트
# ═══════════════════════════════════════════════════════════


class TestChatTranslatorTranslate:
    """ChatTranslator.translate() 기본 동작 검증."""

    @pytest.mark.asyncio
    async def test_returns_correct_result_fields(self):
        """translate()가 ChatTranslationResult의 모든 필드를 올바르게 반환한다."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "안녕하세요"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 15
        mock_response.usage.completion_tokens = 8

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.realtime.chat_translator.AsyncOpenAI", return_value=mock_client):
            translator = ChatTranslator(
                source_language="en",
                target_language="ko",
            )

        result = await translator.translate("Hello")
        assert result is not None
        assert result.translated_text == "안녕하세요"
        assert result.input_tokens == 15
        assert result.output_tokens == 8
        assert result.latency_ms > 0

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        """translate()가 타임아웃 시 None을 반환한다."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        with patch("src.realtime.chat_translator.AsyncOpenAI", return_value=mock_client):
            translator = ChatTranslator(
                source_language="en",
                target_language="ko",
                timeout_ms=100,
            )

        # asyncio.wait_for wraps the coroutine, so we need to simulate
        # the timeout at the wait_for level
        result = await translator.translate("Hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self):
        """translate()가 API 에러 시 None을 반환한다."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("API Error")
        )

        with patch("src.realtime.chat_translator.AsyncOpenAI", return_value=mock_client):
            translator = ChatTranslator(
                source_language="en",
                target_language="ko",
            )

        result = await translator.translate("Hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self):
        """translate()가 빈 응답 시 None을 반환한다."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 0

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.realtime.chat_translator.AsyncOpenAI", return_value=mock_client):
            translator = ChatTranslator(
                source_language="en",
                target_language="ko",
            )

        result = await translator.translate("Hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_includes_context_when_context_manager_provided(self):
        """context_manager가 있으면 대화 컨텍스트를 메시지에 포함한다."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "번역 결과"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 20
        mock_response.usage.completion_tokens = 5

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        mock_context_manager = MagicMock()
        mock_context_manager.format_context.return_value = "User: Hello\nRecipient: 안녕"

        with patch("src.realtime.chat_translator.AsyncOpenAI", return_value=mock_client):
            translator = ChatTranslator(
                source_language="en",
                target_language="ko",
                context_manager=mock_context_manager,
            )

        await translator.translate("감사합니다")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        # system prompt + context message + user message = 3
        assert len(messages) == 3
        assert "Previous conversation" in messages[1]["content"]
        assert "User: Hello" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_works_without_context_manager(self):
        """context_manager가 None이면 시스템+유저 메시지만 전송한다."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "번역 결과"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.realtime.chat_translator.AsyncOpenAI", return_value=mock_client):
            translator = ChatTranslator(
                source_language="en",
                target_language="ko",
                context_manager=None,
            )

        await translator.translate("감사합니다")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        # system prompt + user message = 2 (no context)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "감사합니다"

    @pytest.mark.asyncio
    async def test_skips_empty_context(self):
        """context_manager.format_context()가 빈 문자열이면 컨텍스트 메시지를 추가하지 않는다."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "번역 결과"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        mock_context_manager = MagicMock()
        mock_context_manager.format_context.return_value = ""

        with patch("src.realtime.chat_translator.AsyncOpenAI", return_value=mock_client):
            translator = ChatTranslator(
                source_language="en",
                target_language="ko",
                context_manager=mock_context_manager,
            )

        await translator.translate("테스트")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert len(messages) == 2  # system + user only


# ═══════════════════════════════════════════════════════════
#  Part 2: Session B Chat API 경로 테스트
# ═══════════════════════════════════════════════════════════


class TestTranslateViaChatApi:
    """SessionBHandler._translate_via_chat_api() 동작 검증."""

    @pytest.mark.asyncio
    async def test_sends_caption_and_saves_transcript_on_success(self):
        """Chat API 번역 성공 시 캡션 전송 + transcript 저장."""
        call = _make_call()
        chat_mock = _make_chat_translator_mock(translated_text="Hello there")
        handler = _make_handler(call=call, chat_translator=chat_mock)

        # STT 준비 시뮬레이션 (누적 방식)
        handler._stt_texts = ["안녕하세요"]
        handler._stt_ready_event.set()
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5

        await handler._translate_via_chat_api()

        # ChatTranslator.translate() 호출 확인
        chat_mock.translate.assert_awaited_once_with("안녕하세요")
        # 캡션 콜백 호출 확인
        handler._on_caption.assert_awaited_once_with("recipient", "Hello there")
        # transcript 저장 확인 (transcript_bilingual에 추가)
        assert len(call.transcript_bilingual) == 1
        assert call.transcript_bilingual[0].translated_text == "Hello there"

    @pytest.mark.asyncio
    async def test_skips_turn_on_empty_stt(self):
        """STT 텍스트가 비어 있으면 번역을 건너뛴다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock)

        handler._stt_texts = []
        handler._stt_ready_event.set()

        await handler._translate_via_chat_api()

        chat_mock.translate.assert_not_awaited()
        handler._on_caption.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_turn_on_stt_timeout(self):
        """STT 대기 시간 초과(10s) 시 번역을 건너뛴다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock)

        # _stt_ready_event를 set하지 않음 → 타임아웃
        # _translate_via_chat_api 내부에서 10s 대기하므로, 직접 패치
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            await handler._translate_via_chat_api()

        chat_mock.translate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_turn_when_translator_returns_none(self):
        """ChatTranslator가 None을 반환하면 번역을 건너뛴다."""
        call = _make_call()
        chat_mock = _make_chat_translator_mock(returns_none=True)
        handler = _make_handler(call=call, chat_translator=chat_mock)

        handler._stt_texts = ["테스트"]
        handler._stt_ready_event.set()

        await handler._translate_via_chat_api()

        chat_mock.translate.assert_awaited_once()
        handler._on_caption.assert_not_awaited()
        # transcript에도 추가되지 않아야 함
        assert len(call.transcript_bilingual) == 0

    @pytest.mark.asyncio
    async def test_records_chat_tokens_in_cost(self):
        """Chat API 토큰(chat_input/chat_output)이 CostTokens에 기록된다."""
        call = _make_call()
        chat_mock = _make_chat_translator_mock(
            translated_text="Translated",
            input_tokens=25,
            output_tokens=12,
        )
        handler = _make_handler(call=call, chat_translator=chat_mock)

        handler._stt_texts = ["원문 텍스트"]
        handler._stt_ready_event.set()
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5

        await handler._translate_via_chat_api()

        assert call.cost_tokens.chat_input == 25
        assert call.cost_tokens.chat_output == 12


class TestHandleInputTranscriptionForChatApi:
    """Chat API 모드에서 _handle_input_transcription_completed 동작 검증."""

    @pytest.mark.asyncio
    async def test_sets_stt_ready_event_on_valid_stt(self):
        """유효한 STT 텍스트 수신 시 _stt_ready_event가 설정된다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock)

        assert not handler._stt_ready_event.is_set()

        await handler._handle_input_transcription_completed({"transcript": "안녕하세요"})

        assert handler._stt_ready_event.is_set()
        assert handler._stt_texts == ["안녕하세요"]

    @pytest.mark.asyncio
    async def test_accumulates_multiple_stt_segments(self):
        """연속 발화 시 STT 텍스트가 누적된다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock)

        # 2개의 연속 세그먼트 STT
        await handler._handle_input_transcription_completed({"transcript": "안녕하세요"})
        await handler._handle_input_transcription_completed({"transcript": "반갑습니다"})

        assert handler._stt_texts == ["안녕하세요", "반갑습니다"]

    @pytest.mark.asyncio
    async def test_sets_event_only_when_all_pending_commits_done(self):
        """pending_stt_count가 0이 될 때만 event가 설정된다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock)

        # 2개의 commit 대기 중
        handler._pending_stt_count = 2
        handler._stt_ready_event.clear()

        # 첫 번째 STT 도착 → 아직 1개 남음
        await handler._handle_input_transcription_completed({"transcript": "첫 번째"})
        assert not handler._stt_ready_event.is_set()
        assert handler._pending_stt_count == 1

        # 두 번째 STT 도착 → 모두 완료
        await handler._handle_input_transcription_completed({"transcript": "두 번째"})
        assert handler._stt_ready_event.is_set()
        assert handler._pending_stt_count == 0

    @pytest.mark.asyncio
    async def test_does_not_accumulate_blocked_stt(self):
        """블록리스트에 매칭된 STT는 누적하지 않되, 카운터는 감소한다."""
        chat_mock = _make_chat_translator_mock()
        call = _make_call()
        handler = _make_handler(call=call, chat_translator=chat_mock)

        handler._pending_stt_count = 1

        await handler._handle_input_transcription_completed(
            {"transcript": "MBC 뉴스 이덕영입니다"}
        )

        assert handler._stt_ready_event.is_set()
        assert handler._stt_texts == []  # blocked, not accumulated
        assert handler._stt_blocked is True
        assert handler._pending_stt_count == 0
        assert call.call_metrics.hallucinations_blocked == 1

    @pytest.mark.asyncio
    async def test_does_not_accumulate_noise_stt(self):
        """노이즈 STT(자음 스팸 등)는 누적하지 않되, 카운터는 감소한다."""
        chat_mock = _make_chat_translator_mock()
        call = _make_call()
        handler = _make_handler(call=call, chat_translator=chat_mock)

        handler._pending_stt_count = 1

        # 자음만 (Korean Jamo consonants)
        await handler._handle_input_transcription_completed(
            {"transcript": "ㄱㄴㄷㄹ"}
        )

        assert handler._stt_ready_event.is_set()
        assert handler._stt_texts == []  # noise, not accumulated
        assert handler._stt_blocked is True
        assert handler._pending_stt_count == 0
        assert call.call_metrics.hallucinations_blocked == 1

    @pytest.mark.asyncio
    async def test_decrements_counter_on_empty_transcript(self):
        """빈 transcript 수신 시 카운터만 감소하고 event 설정."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock)

        handler._pending_stt_count = 1

        await handler._handle_input_transcription_completed({"transcript": ""})

        assert handler._stt_ready_event.is_set()
        assert handler._stt_texts == []
        assert handler._pending_stt_count == 0


class TestNotifySpeechStartedChatApi:
    """Chat API 모드에서 notify_speech_started()의 STT 누적 동작 검증."""

    @pytest.mark.asyncio
    async def test_preserves_accumulated_stt_on_new_speech(self):
        """연속 발화 시 이전 STT 텍스트가 보존된다 (누적 방식)."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock, use_local_vad=True)

        # 이전 세그먼트에서 누적된 STT
        handler._stt_texts = ["이전 텍스트"]

        await handler.notify_speech_started()

        # 누적 데이터는 보존됨 (연속 발화 지원)
        assert handler._stt_texts == ["이전 텍스트"]

    @pytest.mark.asyncio
    async def test_clears_stt_blocked_flag(self):
        """새 발화 시작 시 _stt_blocked 플래그가 초기화된다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock, use_local_vad=True)

        handler._stt_blocked = True

        await handler.notify_speech_started()

        assert handler._stt_blocked is False

    @pytest.mark.asyncio
    async def test_server_vad_preserves_accumulated_stt(self):
        """Server VAD speech_started에서도 누적 STT가 보존된다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock, use_local_vad=False)

        handler._stt_texts = ["이전 텍스트"]

        await handler._handle_speech_started({})

        # 연속 발화 누적 지원: 텍스트 보존
        assert handler._stt_texts == ["이전 텍스트"]


class TestContinuousSpeechAccumulation:
    """연속 발화 시 STT 누적 + 결합 번역 시나리오 검증."""

    @pytest.mark.asyncio
    async def test_two_segments_accumulated_and_translated(self):
        """2개의 연속 세그먼트가 누적되어 결합 문장으로 번역된다."""
        call = _make_call()
        chat_mock = _make_chat_translator_mock(translated_text="Hello nice to meet you")
        handler = _make_handler(call=call, chat_translator=chat_mock, use_local_vad=True)

        # Seg1: STT 도착 (이전 translate가 취소된 후)
        handler._stt_texts = ["안녕하세요"]

        # Seg2: commit + STT 도착
        handler._pending_stt_count = 1
        handler._stt_ready_event.clear()

        await handler._handle_input_transcription_completed({"transcript": "반갑습니다"})

        # 모든 pending commit 완료 → event 설정
        assert handler._stt_ready_event.is_set()
        assert handler._stt_texts == ["안녕하세요", "반갑습니다"]

        # 번역 실행
        handler._committed_speech_started_at = time.time() - 3.0
        handler._committed_speech_stopped_at = time.time() - 0.5
        await handler._translate_via_chat_api()

        # 결합된 텍스트로 번역
        chat_mock.translate.assert_awaited_once_with("안녕하세요 반갑습니다")
        # 번역 후 누적 데이터 초기화
        assert handler._stt_texts == []
        assert handler._pending_stt_count == 0

    @pytest.mark.asyncio
    async def test_three_segments_with_pending_counter(self):
        """3개의 세그먼트: 카운터가 0이 될 때만 event가 설정된다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock, use_local_vad=True)

        # 3개의 commit 대기 중
        handler._pending_stt_count = 3
        handler._stt_ready_event.clear()

        # STT 1
        await handler._handle_input_transcription_completed({"transcript": "or can you"})
        assert not handler._stt_ready_event.is_set()
        assert handler._pending_stt_count == 2

        # STT 2
        await handler._handle_input_transcription_completed({"transcript": "tell me what kind"})
        assert not handler._stt_ready_event.is_set()
        assert handler._pending_stt_count == 1

        # STT 3
        await handler._handle_input_transcription_completed({"transcript": "of bag you had"})
        assert handler._stt_ready_event.is_set()
        assert handler._pending_stt_count == 0
        assert handler._stt_texts == ["or can you", "tell me what kind", "of bag you had"]

    @pytest.mark.asyncio
    async def test_mixed_blocked_and_valid_segments(self):
        """blocked STT + 정상 STT 혼합 시 정상 텍스트만 누적된다."""
        call = _make_call()
        chat_mock = _make_chat_translator_mock(translated_text="예약 가능합니다")
        handler = _make_handler(call=call, chat_translator=chat_mock, use_local_vad=True)

        handler._pending_stt_count = 2
        handler._stt_ready_event.clear()

        # Seg1: 노이즈 (blocked)
        await handler._handle_input_transcription_completed({"transcript": "ㅋ"})
        assert not handler._stt_ready_event.is_set()
        assert handler._stt_texts == []  # 노이즈는 누적 안됨

        # Seg2: 정상 텍스트
        await handler._handle_input_transcription_completed({"transcript": "Yes, it is available"})
        assert handler._stt_ready_event.is_set()
        assert handler._stt_texts == ["Yes, it is available"]

    @pytest.mark.asyncio
    async def test_translate_clears_accumulator_on_success(self):
        """번역 성공 후 누적 데이터가 초기화된다."""
        call = _make_call()
        chat_mock = _make_chat_translator_mock(translated_text="OK")
        handler = _make_handler(call=call, chat_translator=chat_mock, use_local_vad=True)

        handler._stt_texts = ["확인"]
        handler._stt_ready_event.set()
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5

        await handler._translate_via_chat_api()

        assert handler._stt_texts == []
        assert handler._pending_stt_count == 0

    @pytest.mark.asyncio
    async def test_translate_clears_accumulator_on_timeout(self):
        """STT 타임아웃 시에도 누적 데이터가 초기화된다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock, use_local_vad=True)

        handler._stt_texts = ["stale text"]
        handler._pending_stt_count = 1

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            await handler._translate_via_chat_api()

        assert handler._stt_texts == []
        assert handler._pending_stt_count == 0


# ═══════════════════════════════════════════════════════════
#  Part 3: P1 Race Condition 검증
# ═══════════════════════════════════════════════════════════


class TestRaceConditionFix:
    """P1: _stt_texts.clear()가 translate() 완료 후에 호출되는지 검증."""

    @pytest.mark.asyncio
    async def test_stt_texts_not_cleared_before_translate(self):
        """translate() 호출 시점에 _stt_texts가 아직 clear되지 않았는지 확인."""
        call = _make_call()
        captured_texts: list[list[str]] = []

        async def capture_translate(text: str):
            # translate() 호출 시점의 _stt_texts 상태 캡처
            captured_texts.append(list(handler._stt_texts))
            return ChatTranslationResult(
                translated_text="translated",
                input_tokens=10,
                output_tokens=5,
                latency_ms=100.0,
            )

        chat_mock = AsyncMock(spec=ChatTranslator)
        chat_mock.translate = AsyncMock(side_effect=capture_translate)
        handler = _make_handler(call=call, chat_translator=chat_mock, use_local_vad=True)

        handler._stt_texts = ["original text"]
        handler._stt_ready_event.set()
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5

        await handler._translate_via_chat_api()

        # translate() 호출 시점에 _stt_texts가 아직 남아있어야 함 (clear 전)
        assert captured_texts == [["original text"]]
        # translate() 완료 후에 clear
        assert handler._stt_texts == []

    @pytest.mark.asyncio
    async def test_stt_texts_preserved_on_translate_failure(self):
        """translate()가 None을 반환해도 _stt_texts는 clear된다 (정상 완료)."""
        call = _make_call()
        chat_mock = _make_chat_translator_mock(returns_none=True)
        handler = _make_handler(call=call, chat_translator=chat_mock, use_local_vad=True)

        handler._stt_texts = ["test text"]
        handler._stt_ready_event.set()

        await handler._translate_via_chat_api()

        # translate 실패해도 clear (정상 완료 경로)
        assert handler._stt_texts == []
        assert handler._pending_stt_count == 0


# ═══════════════════════════════════════════════════════════
#  Part 4: P2 영어 할루시네이션 필터 검증
# ═══════════════════════════════════════════════════════════


class TestEnglishHallucinationBlocklist:
    """_STT_HALLUCINATION_BLOCKLIST_EN 필터링 검증.

    영어 STT 할루시네이션 필터는 수신자가 영어가 아닐 때(target_language != "en")만 적용:
    - target_language="ko": 수신자가 한국어 → 영어 STT는 Whisper 할루시네이션 → 차단
    - target_language="en": 수신자가 영어 → 영어 STT는 정상 발화 → 통과
    """

    @pytest.mark.asyncio
    async def test_en_blocklist_blocks_when_target_is_ko(self):
        """target_language=ko일 때 'Thank you.'가 EN 블록리스트에 의해 차단된다."""
        call = _make_call(target_language="ko")
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(call=call, chat_translator=chat_mock)

        handler._pending_stt_count = 1
        await handler._handle_input_transcription_completed(
            {"transcript": "Thank you."}
        )

        assert handler._stt_texts == []
        assert handler._stt_blocked is True
        assert call.call_metrics.hallucinations_blocked == 1

    @pytest.mark.asyncio
    async def test_en_blocklist_blocks_thanks_for_watching_when_target_ko(self):
        """target_language=ko일 때 'Thanks for watching'이 차단된다."""
        call = _make_call(target_language="ko")
        handler = _make_handler(call=call)

        await handler._handle_input_transcription_completed(
            {"transcript": "Thanks for watching"}
        )

        assert handler._stt_blocked is True
        assert call.call_metrics.hallucinations_blocked == 1

    @pytest.mark.asyncio
    async def test_en_blocklist_not_applied_for_en_target(self):
        """target_language=en이면 EN 블록리스트가 적용되지 않는다 (수신자가 영어)."""
        call = _make_call(target_language="en")
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(call=call, chat_translator=chat_mock)

        handler._pending_stt_count = 1
        await handler._handle_input_transcription_completed(
            {"transcript": "Thank you."}
        )

        # EN target에서는 EN blocklist 미적용 → 통과 (수신자의 정상 영어 발화)
        assert handler._stt_texts == ["Thank you."]
        assert handler._stt_blocked is False

    @pytest.mark.asyncio
    async def test_en_blocklist_blocks_single_word_you_when_target_ko(self):
        """target_language=ko일 때 단일 단어 'you'가 차단된다."""
        call = _make_call(target_language="ko")
        handler = _make_handler(call=call)

        await handler._handle_input_transcription_completed(
            {"transcript": "you"}
        )

        assert handler._stt_blocked is True
        assert call.call_metrics.hallucinations_blocked == 1


class TestEnglishShortHallucination:
    """_is_english_short_hallucination() 검증.

    수신자가 영어가 아닐 때(target_language != "en")만 적용:
    - target_language="ko": 짧은 영어 표현은 할루시네이션 → 차단
    - target_language="en": 짧은 영어 표현은 정상 발화 → 통과
    """

    @pytest.mark.asyncio
    async def test_hi_name_pattern_blocked_when_target_ko(self):
        """target_language=ko일 때 'Hi, Tammy' 패턴이 차단된다."""
        call = _make_call(target_language="ko")
        handler = _make_handler(call=call)

        await handler._handle_input_transcription_completed(
            {"transcript": "Hi, Tammy"}
        )

        assert handler._stt_blocked is True
        assert call.call_metrics.hallucinations_blocked == 1

    @pytest.mark.asyncio
    async def test_hi_name_with_period_blocked_when_target_ko(self):
        """target_language=ko일 때 'Hi, Tammy.' (마침표 포함)도 차단된다."""
        call = _make_call(target_language="ko")
        handler = _make_handler(call=call)

        await handler._handle_input_transcription_completed(
            {"transcript": "Hi, Tammy."}
        )

        assert handler._stt_blocked is True

    @pytest.mark.asyncio
    async def test_short_polite_blocked_when_target_ko(self):
        """target_language=ko일 때 짧은 공손 표현 'Okay'가 차단된다."""
        call = _make_call(target_language="ko")
        handler = _make_handler(call=call)

        await handler._handle_input_transcription_completed(
            {"transcript": "Okay"}
        )

        assert handler._stt_blocked is True

    @pytest.mark.asyncio
    async def test_longer_sentence_passes(self):
        """4단어 이상 문장은 통과한다."""
        call = _make_call(target_language="en")
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(call=call, chat_translator=chat_mock)

        handler._pending_stt_count = 1
        await handler._handle_input_transcription_completed(
            {"transcript": "I would like to order"}
        )

        assert handler._stt_texts == ["I would like to order"]
        assert handler._stt_blocked is False

    @pytest.mark.asyncio
    async def test_not_applied_for_en_target(self):
        """target_language=en이면 EN short hallucination이 적용되지 않는다 (수신자가 영어)."""
        call = _make_call(target_language="en")
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(call=call, chat_translator=chat_mock)

        handler._pending_stt_count = 1
        await handler._handle_input_transcription_completed(
            {"transcript": "Hi, Tammy"}
        )

        # EN target → EN 필터 미적용 (수신자의 정상 영어 발화)
        assert handler._stt_texts == ["Hi, Tammy"]


class TestEnglishStage2Filter:
    """Stage 2 (번역 출력)에서 EN 필터 검증."""

    @pytest.mark.asyncio
    async def test_en_blocklist_blocks_in_stage2(self):
        """번역 출력이 EN 블록리스트에 의해 Stage 2에서 차단된다."""
        call = _make_call(target_language="en")
        handler = _make_handler(call=call)
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5

        await handler._save_transcript_and_notify("Thank you.")

        assert len(call.transcript_bilingual) == 0
        assert call.call_metrics.hallucinations_blocked == 1

    @pytest.mark.asyncio
    async def test_en_short_hallucination_blocks_in_stage2(self):
        """번역 출력이 EN short hallucination으로 Stage 2에서 차단된다."""
        call = _make_call(target_language="en")
        handler = _make_handler(call=call)
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5

        await handler._save_transcript_and_notify("Okay")

        assert len(call.transcript_bilingual) == 0
        assert call.call_metrics.hallucinations_blocked == 1

    @pytest.mark.asyncio
    async def test_normal_translation_passes_stage2(self):
        """정상 번역 출력은 Stage 2를 통과한다."""
        call = _make_call(target_language="en")
        handler = _make_handler(call=call)
        handler._committed_speech_started_at = time.time() - 2.0
        handler._committed_speech_stopped_at = time.time() - 0.5

        await handler._save_transcript_and_notify("I would like a table for two please")

        assert len(call.transcript_bilingual) == 1
