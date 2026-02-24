"""오디오 라우터 — CommunicationMode별 Pipeline에 위임하는 얇은 라우터.

핵심 오디오 흐름 (PRD 3.2):
  User App   <->  Relay Server  <->  Twilio (수신자)
                      |
               OpenAI Realtime
               (Session A + B)

Mode → Pipeline 매핑:
  VOICE_TO_VOICE → VoiceToVoicePipeline
  TEXT_TO_VOICE  → TextToVoicePipeline
  FULL_AGENT     → FullAgentPipeline
"""

import logging
from typing import Any, Callable, Coroutine

from src.realtime.pipeline.base import BasePipeline
from src.realtime.pipeline.full_agent import FullAgentPipeline
from src.realtime.pipeline.text_to_voice import TextToVoicePipeline
from src.realtime.pipeline.voice_to_voice import VoiceToVoicePipeline
from src.realtime.sessions.session_manager import DualSessionManager
from src.twilio.media_stream import TwilioMediaStreamHandler
from src.types import ActiveCall, CommunicationMode, WsMessage

logger = logging.getLogger(__name__)


class AudioRouter:
    """CommunicationMode에 따라 적절한 Pipeline에 위임하는 라우터.

    외부 인터페이스(start/stop/handle_*)는 기존과 동일하게 유지하여
    twilio_webhook.py, stream.py, call_manager.py에서의 사용 패턴을 보존한다.
    """

    # AudioRouter 자체가 소유하는 속성 (Pipeline으로 프록시하지 않음)
    _OWN_ATTRS = frozenset({"call", "_pipeline"})

    def __init__(
        self,
        call: ActiveCall,
        dual_session: DualSessionManager,
        twilio_handler: TwilioMediaStreamHandler,
        app_ws_send: Callable[[WsMessage], Coroutine[Any, Any, None]],
        prompt_a: str = "",
        prompt_b: str = "",
    ):
        object.__setattr__(self, "call", call)
        object.__setattr__(
            self,
            "_pipeline",
            self._create_pipeline(
                call=call,
                dual_session=dual_session,
                twilio_handler=twilio_handler,
                app_ws_send=app_ws_send,
                prompt_a=prompt_a,
                prompt_b=prompt_b,
            ),
        )
        logger.info(
            "AudioRouter created pipeline=%s for call=%s mode=%s",
            type(self._pipeline).__name__,
            call.call_id,
            call.communication_mode.value,
        )

    def _create_pipeline(
        self,
        call: ActiveCall,
        dual_session: DualSessionManager,
        twilio_handler: TwilioMediaStreamHandler,
        app_ws_send: Callable[[WsMessage], Coroutine[Any, Any, None]],
        prompt_a: str,
        prompt_b: str,
    ) -> BasePipeline:
        """CommunicationMode에 따라 Pipeline 구현체를 생성한다."""
        match call.communication_mode:
            case CommunicationMode.VOICE_TO_VOICE:
                return VoiceToVoicePipeline(
                    call=call,
                    dual_session=dual_session,
                    twilio_handler=twilio_handler,
                    app_ws_send=app_ws_send,
                    prompt_a=prompt_a,
                    prompt_b=prompt_b,
                )
            case CommunicationMode.TEXT_TO_VOICE:
                return TextToVoicePipeline(
                    call=call,
                    dual_session=dual_session,
                    twilio_handler=twilio_handler,
                    app_ws_send=app_ws_send,
                    prompt_a=prompt_a,
                    prompt_b=prompt_b,
                )
            case CommunicationMode.FULL_AGENT:
                return FullAgentPipeline(
                    call=call,
                    dual_session=dual_session,
                    twilio_handler=twilio_handler,
                    app_ws_send=app_ws_send,
                    prompt_a=prompt_a,
                    prompt_b=prompt_b,
                )
            case _:
                raise ValueError(f"Unknown communication mode: {call.communication_mode}")

    # --- Pipeline 위임 메서드 ---

    async def start(self) -> None:
        await self._pipeline.start()

    async def stop(self) -> None:
        await self._pipeline.stop()

    async def handle_user_audio(self, audio_b64: str) -> None:
        await self._pipeline.handle_user_audio(audio_b64)

    async def handle_user_audio_commit(self) -> None:
        await self._pipeline.handle_user_audio_commit()

    async def handle_user_text(self, text: str) -> None:
        await self._pipeline.handle_user_text(text)

    async def handle_twilio_audio(self, audio_bytes: bytes) -> None:
        await self._pipeline.handle_twilio_audio(audio_bytes)

    async def handle_typing_started(self) -> None:
        await self._pipeline.handle_typing_started()

    # --- 투명 프록시: Pipeline 내부 속성 접근 (테스트 호환성) ---

    def __getattr__(self, name: str) -> Any:
        """Pipeline의 내부 속성을 읽는다.

        기존 테스트에서 router.session_b 등
        내부 속성에 직접 접근하므로 호환성을 위해 프록시한다.
        """
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._pipeline, name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Pipeline의 내부 속성을 쓴다.

        테스트에서 router.first_message = MagicMock() 등
        내부 속성에 직접 쓰기하므로 호환성을 위해 프록시한다.
        """
        if name in AudioRouter._OWN_ATTRS:
            object.__setattr__(self, name, value)
        else:
            setattr(self._pipeline, name, value)
