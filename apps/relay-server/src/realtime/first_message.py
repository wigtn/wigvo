"""First Message Strategy — AI 고지 + 수신자 인사 대기.

PRD 3.4:
  1. Twilio가 전화를 건다
  2. 수신자가 전화를 받는다 ("여보세요")
  3. Session B가 수신자 첫 발화를 감지 (Server VAD)
  4. 자동 AI 고지 (Session A → Twilio → 수신자)
  5. AI 고지 완료 후:
     - Relay Mode: User 앱에 "상대방이 응답했습니다" 알림
     - Agent Mode: AI가 바로 용건 시작
"""

import logging
from typing import Callable, Coroutine

from src.prompt.templates import FIRST_MESSAGE_TEMPLATES
from src.realtime.sessions.session_a import SessionAHandler
from src.types import ActiveCall, CallMode, CallStatus, WsMessage, WsMessageType

logger = logging.getLogger(__name__)


class FirstMessageHandler:
    """수신자의 첫 발화를 감지하고 AI 고지를 트리거한다."""

    def __init__(
        self,
        call: ActiveCall,
        session_a: SessionAHandler,
        on_notify_app: Callable[[WsMessage], Coroutine],
        use_exact_utterance: bool = True,
    ):
        self.call = call
        self.session_a = session_a
        self._on_notify_app = on_notify_app
        self._use_exact_utterance = use_exact_utterance

    async def on_recipient_speech_detected(self) -> None:
        """수신자의 첫 발화가 감지되면 AI 고지를 전송한다."""
        if self.call.first_message_sent:
            return

        self.call.first_message_sent = True
        self.call.status = CallStatus.CONNECTED

        logger.info("Recipient answered — sending AI greeting (call=%s)", self.call.call_id)

        # Session A가 이미 응답 중이면 대기 (conversation_already_has_active_response 방지)
        if self.session_a.is_generating:
            logger.debug("Waiting for Session A to finish before sending greeting...")
            await self.session_a.wait_for_done(timeout=3.0)

        # 시스템 생성 메시지 플래그 — transcript에 role="assistant"로 저장
        self.session_a._system_message_pending = True

        if self.call.mode == CallMode.AGENT:
            # Agent mode: AI가 자연스럽게 대화를 시작하도록 지시
            instruction = (
                "The recipient has answered the phone. "
                "Begin the conversation by greeting them and stating the purpose of your call."
            )
            await self.session_a.send_user_text(instruction)
        else:
            greeting = FIRST_MESSAGE_TEMPLATES.get(
                self.call.target_language,
                FIRST_MESSAGE_TEMPLATES["en"],
            )

            if self._use_exact_utterance:
                # AI 확장/재해석 방지 — 정확히 이 문장만 발화 (hskim 이식)
                # 템플릿이 target_language 네이티브 문구이므로 이 경로가 표준.
                wrapped = f'Say exactly this sentence and nothing else: "{greeting}"'
            else:
                # 레거시 번역 지시 형식. FIRST_MESSAGE_TEMPLATES가 target_language로
                # 현지화된 이후로는 재번역이 일어나므로 사용하지 말 것.
                wrapped = f"[User says in {self.call.source_language}]: {greeting}"

            await self.session_a.send_user_text(wrapped)

        # App에 통화 연결 알림
        if self.call.mode == CallMode.RELAY:
            await self._on_notify_app(
                WsMessage(
                    type=WsMessageType.CALL_STATUS,
                    data={
                        "status": "connected",
                        "message": "상대방이 응답했습니다. 말씀하세요.",
                    },
                )
            )
        else:
            await self._on_notify_app(
                WsMessage(
                    type=WsMessageType.CALL_STATUS,
                    data={
                        "status": "connected",
                        "message": "상대방이 응답했습니다. AI가 대화를 시작합니다.",
                    },
                )
            )
