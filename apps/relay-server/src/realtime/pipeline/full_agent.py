"""FullAgentPipeline — AI 자율 대화 파이프라인.

FULL_AGENT: AI가 수집된 정보 기반으로 자율 통화 + Function Calling

TextToVoicePipeline과의 차이:
  - Agent Mode 피드백 루프: Session B 번역 → Session A에 전달
  - Function Calling: Session A에 tools 등록 (get_tools_for_mode)
  - User 텍스트 입력: AI가 모르는 정보를 User가 보충할 때 사용

나머지는 TextToVoicePipeline과 동일:
  - Session B modalities=['text']
  - Dynamic Energy Threshold로 에코 필터링
  - Audio Energy Gate 유지
"""

import logging
from typing import Any, Callable, Coroutine

from src.realtime.pipeline.text_to_voice import TextToVoicePipeline
from src.realtime.sessions.session_manager import DualSessionManager
from src.twilio.media_stream import TwilioMediaStreamHandler
from src.types import ActiveCall, WsMessage

logger = logging.getLogger(__name__)


class FullAgentPipeline(TextToVoicePipeline):
    """AI 자율 대화 파이프라인 (TextToVoice 기반 + Agent 피드백 루프)."""

    def __init__(
        self,
        call: ActiveCall,
        dual_session: DualSessionManager,
        twilio_handler: TwilioMediaStreamHandler,
        app_ws_send: Callable[[WsMessage], Coroutine[Any, Any, None]],
        prompt_a: str = "",
        prompt_b: str = "",
    ):
        super().__init__(
            call=call,
            dual_session=dual_session,
            twilio_handler=twilio_handler,
            app_ws_send=app_ws_send,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
        )
        logger.info("FullAgentPipeline created for call %s", call.call_id)

    # --- Agent Mode 피드백 루프 ---

    async def _on_turn_complete(self, role: str, text: str) -> None:
        """번역 완료 시 컨텍스트 추가 + Agent 피드백 루프.

        수신자 발화가 번역되면 Session A에 전달하여
        AI가 대화를 이어갈 수 있도록 한다.
        """
        self.context_manager.add_turn(role, text)
        if role == "recipient":
            await self._forward_recipient_to_session_a(text)
        await self._send_metrics_snapshot()

    async def _forward_recipient_to_session_a(self, text: str) -> None:
        """수신자 번역 텍스트를 Session A에 전달한다."""
        self.call.transcript_history.append({"role": "recipient", "text": text})
        if self.session_a.is_generating:
            logger.debug("Waiting for Session A before forwarding recipient translation...")
            await self.session_a.wait_for_done(timeout=5.0)
        logger.info("Agent Mode: forwarding recipient translation to Session A: %s", text[:80])
        await self.session_a.send_user_text(f"[Recipient says]: {text}")
        # send_user_text 내부에서 response.create가 호출되므로 즉시 generating 상태 전환
        self.session_a.mark_generating()
