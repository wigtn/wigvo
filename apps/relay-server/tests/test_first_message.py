"""FirstMessageHandler 단위 테스트 — 인사말 고정 (exact utterance).

핵심 검증 사항:
  - exact utterance 모드에서 인사말이 수신자 언어(target_language) 고정 문구로 전송된다
  - FIRST_MESSAGE_TEMPLATES가 각 언어의 네이티브 문구로 현지화되어 있다
  - first_message_sent 플래그로 중복 전송이 차단된다
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.prompt.templates import FIRST_MESSAGE_TEMPLATES
from src.realtime.first_message import FirstMessageHandler
from src.types import ActiveCall, CallMode, CommunicationMode


def _make_call(**overrides) -> ActiveCall:
    defaults = dict(
        call_id="test-call-fm",
        user_id="u1",
        mode=CallMode.RELAY,
        source_language="en",
        target_language="ko",
        target_phone="+821012345678",
        twilio_call_sid="CA_test",
        communication_mode=CommunicationMode.VOICE_TO_VOICE,
    )
    defaults.update(overrides)
    return ActiveCall(**defaults)


def _make_session_a() -> MagicMock:
    session_a = MagicMock()
    session_a.is_generating = False
    session_a.send_user_text = AsyncMock()
    return session_a


class TestExactUtteranceGreeting:
    """exact utterance 모드: 모델 재해석 없이 고정 문구만 발화."""

    @pytest.mark.asyncio
    async def test_greeting_wrapped_as_exact_utterance(self):
        """인사말이 'Say exactly this sentence' 지시로 전송된다."""
        session_a = _make_session_a()
        handler = FirstMessageHandler(
            call=_make_call(),
            session_a=session_a,
            on_notify_app=AsyncMock(),
            use_exact_utterance=True,
        )

        await handler.on_recipient_speech_detected()

        session_a.send_user_text.assert_awaited_once()
        sent = session_a.send_user_text.await_args.args[0]
        expected = FIRST_MESSAGE_TEMPLATES["ko"]
        assert sent == f'Say exactly this sentence and nothing else: "{expected}"'

    @pytest.mark.asyncio
    async def test_greeting_is_in_recipient_language(self):
        """ko 수신자에게는 한국어 고정 인사말이 나간다 (영어 템플릿 금지)."""
        session_a = _make_session_a()
        handler = FirstMessageHandler(
            call=_make_call(target_language="ko"),
            session_a=session_a,
            on_notify_app=AsyncMock(),
            use_exact_utterance=True,
        )

        await handler.on_recipient_speech_detected()

        sent = session_a.send_user_text.await_args.args[0]
        assert "안녕하세요" in sent

    @pytest.mark.asyncio
    async def test_exact_utterance_is_default(self):
        """플래그 생략 시에도 exact 모드 — 레거시 재번역 경로가 기본값이면 안 된다."""
        session_a = _make_session_a()
        handler = FirstMessageHandler(
            call=_make_call(),
            session_a=session_a,
            on_notify_app=AsyncMock(),
        )

        await handler.on_recipient_speech_detected()

        sent = session_a.send_user_text.await_args.args[0]
        assert sent.startswith("Say exactly this sentence and nothing else:")

    @pytest.mark.asyncio
    async def test_unknown_language_falls_back_to_english(self):
        """템플릿에 없는 언어는 영어 인사말로 fallback."""
        session_a = _make_session_a()
        handler = FirstMessageHandler(
            call=_make_call(target_language="fr"),
            session_a=session_a,
            on_notify_app=AsyncMock(),
        )

        await handler.on_recipient_speech_detected()

        sent = session_a.send_user_text.await_args.args[0]
        assert FIRST_MESSAGE_TEMPLATES["en"] in sent

    @pytest.mark.asyncio
    async def test_duplicate_detection_sends_once(self):
        """first_message_sent 플래그로 두 번째 감지에는 전송하지 않는다."""
        session_a = _make_session_a()
        handler = FirstMessageHandler(
            call=_make_call(),
            session_a=session_a,
            on_notify_app=AsyncMock(),
            use_exact_utterance=True,
        )

        await handler.on_recipient_speech_detected()
        await handler.on_recipient_speech_detected()

        session_a.send_user_text.assert_awaited_once()


class TestTemplateLocalization:
    """FIRST_MESSAGE_TEMPLATES는 exact 발화용이므로 각 언어의 네이티브 문구여야 한다."""

    def test_ko_template_is_korean(self):
        assert any("가" <= ch <= "힣" for ch in FIRST_MESSAGE_TEMPLATES["ko"])

    def test_ja_template_is_japanese(self):
        assert any(
            "぀" <= ch <= "ヿ" for ch in FIRST_MESSAGE_TEMPLATES["ja"]
        )  # 히라가나/가타카나

    def test_zh_template_is_chinese(self):
        assert any("一" <= ch <= "鿿" for ch in FIRST_MESSAGE_TEMPLATES["zh"])

    def test_vi_template_is_vietnamese(self):
        assert "Xin chào" in FIRST_MESSAGE_TEMPLATES["vi"]

    def test_en_template_is_english(self):
        assert "on behalf of a customer" in FIRST_MESSAGE_TEMPLATES["en"]
