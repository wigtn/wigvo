"""Turn Overlap / Interrupt 처리.

PRD 3.6 — Interrupt 우선순위:
  1 (최고): 수신자 발화 — 수신자를 기다리게 하면 안 됨
  2: User 발화 — User가 의도적으로 말하고 있으므로 존중
  3 (최저): AI 생성 (TTS/필러) — 언제든 중단하고 재생성 가능

Case 1: Session A TTS 재생 중 수신자가 끼어들기
  → Session A에 response.cancel + Twilio clear
Case 2: User가 말하는 중 수신자가 끼어들기
  → App에 "상대방이 말하고 있습니다" 알림, User 오디오는 버퍼링 유지
Case 3: Session A/B 동시 출력
  → 독립 경로이므로 병렬 허용
Case 4: Agent Mode에서 수신자 끼어들기
  → Session A response.cancel 후 수신자 발화 처리
"""

import asyncio
import logging
import time
from typing import Callable, Coroutine

from src.realtime.sessions.session_a import SessionAHandler
from src.twilio.media_stream import TwilioMediaStreamHandler
from src.types import ActiveCall, WsMessage, WsMessageType

logger = logging.getLogger(__name__)

# 수신자 발화 종료 후 쿨다운 (초).
# 수신자가 잠깐 쉬었다가 이어 말하는 경우를 위한 보호 구간.
RECIPIENT_SPEECH_COOLDOWN_S = 1.5


class InterruptHandler:
    """실시간 통화의 Turn Overlap / Interrupt를 처리한다."""

    def __init__(
        self,
        session_a: SessionAHandler,
        twilio_handler: TwilioMediaStreamHandler,
        on_notify_app: Callable[[WsMessage], Coroutine],
        call: ActiveCall | None = None,
    ):
        self.session_a = session_a
        self.twilio_handler = twilio_handler
        self._on_notify_app = on_notify_app
        self._call = call
        self._recipient_speaking = False
        self._last_speech_stopped_at: float = 0.0
        self._recipient_done_event = asyncio.Event()
        self._recipient_done_event.set()  # 초기 상태: 발화 중 아님

    async def on_recipient_speech_started(self, allow_interrupt: bool = True) -> None:
        """수신자가 말하기 시작했을 때.

        Session A가 TTS를 생성 중이면 즉시 중단한다 (Case 1, 4).
        이전에 보낸 TTS가 Twilio에서 재생 중일 수 있으므로, 항상 Twilio 버퍼를 클리어한다.

        allow_interrupt=False면 발화 상태 추적만 하고 cancel/clear는 하지 않는다
        (AI 인사말 보호막 — 안내문 재생 중 수신자 발화로 안내문이 잘리는 것 방지).
        """
        self._recipient_speaking = True
        self._last_speech_stopped_at = 0.0  # 쿨다운 리셋
        self._recipient_done_event.clear()

        if not allow_interrupt:
            return

        if self.session_a.is_generating:
            logger.info("Interrupt: recipient speech while Session A generating — cancelling")
            if self._call:
                self._call.call_metrics.interrupt_count += 1
            await self.session_a.cancel()

        # 항상 Twilio 버퍼 클리어 (이미 전송된 TTS가 재생 중일 수 있음)
        await self.twilio_handler.send_clear()

        # App에 알림 (Case 2)
        await self._on_notify_app(
            WsMessage(
                type=WsMessageType.INTERRUPT_ALERT,
                data={"speaking": "recipient"},
            )
        )

    async def on_recipient_speech_stopped(self) -> None:
        """수신자가 말을 멈췄을 때.

        즉시 False로 바꾸지 않고 타임스탬프만 기록한다.
        is_recipient_speaking 프로퍼티가 쿨다운을 적용한다.
        """
        self._recipient_speaking = False
        self._last_speech_stopped_at = time.monotonic()
        self._recipient_done_event.set()

    async def wait_for_recipient_done(self, timeout: float = 10.0) -> bool:
        """수신자 발화 종료를 대기한다. 발화 중이 아니면 즉시 반환."""
        if not self.is_recipient_speaking:
            return True
        try:
            await asyncio.wait_for(self._recipient_done_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def is_recipient_speaking(self) -> bool:
        """수신자가 현재 말하고 있거나, 발화 종료 후 쿨다운 중인지 반환한다.

        수신자가 잠깐 쉬었다가 이어 말하는 경우를 방지하기 위해,
        speech_stopped 후 일정 시간 동안은 여전히 '말하는 중'으로 간주한다.
        """
        if self._recipient_speaking:
            return True
        if self._last_speech_stopped_at > 0:
            elapsed = time.monotonic() - self._last_speech_stopped_at
            if elapsed < RECIPIENT_SPEECH_COOLDOWN_S:
                return True
        return False
