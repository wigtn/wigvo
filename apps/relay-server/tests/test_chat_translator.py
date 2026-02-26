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

        # STT 준비 시뮬레이션
        handler._stt_text = "안녕하세요"
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

        handler._stt_text = ""
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

        handler._stt_text = "테스트"
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

        handler._stt_text = "원문 텍스트"
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
        assert handler._stt_text == "안녕하세요"

    @pytest.mark.asyncio
    async def test_sets_empty_text_and_event_on_blocklist_match(self):
        """블록리스트에 매칭되면 빈 텍스트 + event 설정 (번역 skip)."""
        chat_mock = _make_chat_translator_mock()
        call = _make_call()
        handler = _make_handler(call=call, chat_translator=chat_mock)

        await handler._handle_input_transcription_completed(
            {"transcript": "MBC 뉴스 이덕영입니다"}
        )

        assert handler._stt_ready_event.is_set()
        assert handler._stt_text == ""
        assert handler._stt_blocked is True
        assert call.call_metrics.hallucinations_blocked == 1

    @pytest.mark.asyncio
    async def test_sets_empty_text_and_event_on_noise_stt(self):
        """노이즈 STT(자음 스팸 등) 시 빈 텍스트 + event 설정."""
        chat_mock = _make_chat_translator_mock()
        call = _make_call()
        handler = _make_handler(call=call, chat_translator=chat_mock)

        # 자음만 (Korean Jamo consonants)
        await handler._handle_input_transcription_completed(
            {"transcript": "ㄱㄴㄷㄹ"}
        )

        assert handler._stt_ready_event.is_set()
        assert handler._stt_text == ""
        assert handler._stt_blocked is True
        assert call.call_metrics.hallucinations_blocked == 1

    @pytest.mark.asyncio
    async def test_sets_empty_text_on_empty_transcript(self):
        """빈 transcript 수신 시 빈 텍스트 + event 설정."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock)

        await handler._handle_input_transcription_completed({"transcript": ""})

        assert handler._stt_ready_event.is_set()
        assert handler._stt_text == ""


class TestNotifySpeechStartedChatApi:
    """Chat API 모드에서 notify_speech_started()의 STT 이벤트 리셋 검증."""

    @pytest.mark.asyncio
    async def test_clears_stt_event_on_new_speech(self):
        """새 발화 시작 시 _stt_ready_event가 클리어된다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock, use_local_vad=True)

        # 이전 턴에서 설정된 상태 시뮬레이션
        handler._stt_ready_event.set()
        handler._stt_text = "이전 텍스트"

        await handler.notify_speech_started()

        assert not handler._stt_ready_event.is_set()
        assert handler._stt_text == ""

    @pytest.mark.asyncio
    async def test_clears_stt_blocked_flag(self):
        """새 발화 시작 시 _stt_blocked 플래그가 초기화된다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock, use_local_vad=True)

        handler._stt_blocked = True

        await handler.notify_speech_started()

        assert handler._stt_blocked is False

    @pytest.mark.asyncio
    async def test_server_vad_clears_stt_event_on_speech_started(self):
        """Server VAD speech_started에서도 _stt_ready_event가 클리어된다."""
        chat_mock = _make_chat_translator_mock()
        handler = _make_handler(chat_translator=chat_mock, use_local_vad=False)

        handler._stt_ready_event.set()
        handler._stt_text = "이전 텍스트"

        await handler._handle_speech_started({})

        assert not handler._stt_ready_event.is_set()
        assert handler._stt_text == ""
