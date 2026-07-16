"""Twilio Media Stream WebSocket 핸들러.

Twilio Media Stream 이벤트를 수신하고,
수신자 오디오를 Session B로 전달하며,
Session A의 TTS 오디오를 Twilio로 전송한다.
"""

import base64
import json
import logging

from fastapi import WebSocket

from src.types import ActiveCall, TwilioMediaEvent

logger = logging.getLogger(__name__)


class TwilioMediaStreamHandler:
    """Twilio Media Stream WebSocket 연결을 관리한다."""

    def __init__(self, ws: WebSocket, call: ActiveCall):
        self.ws = ws
        self.call = call
        self.stream_sid: str = ""
        self._closed = False
        self._ws_closed = False

    async def handle_message(self, raw: str) -> TwilioMediaEvent | None:
        """Twilio Media Stream 메시지를 파싱한다."""
        try:
            data = json.loads(raw)
            event = TwilioMediaEvent(**data)
        except Exception:
            logger.warning("Failed to parse Twilio media event: %s", raw[:200])
            return None

        match event.event:
            case "connected":
                logger.info("Twilio media stream connected (call=%s)", self.call.call_id)
            case "start":
                self.stream_sid = event.stream_sid or ""
                self.call.stream_sid = self.stream_sid
                logger.info(
                    "Twilio media stream started: stream_sid=%s", self.stream_sid
                )
            case "media":
                pass  # 오디오 페이로드 — 호출자가 Session B로 전달
            case "stop":
                logger.info("Twilio media stream stopped (call=%s)", self.call.call_id)
                self._closed = True

        return event

    def extract_audio(self, event: TwilioMediaEvent) -> bytes | None:
        """Twilio media 이벤트에서 g711_ulaw 오디오 바이트를 추출한다."""
        if event.media and event.media.get("payload"):
            return base64.b64decode(event.media["payload"])
        return None

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Session A의 TTS 오디오를 Twilio로 전송한다 (g711_ulaw base64)."""
        if self._closed:
            return

        payload = base64.b64encode(audio_bytes).decode("ascii")
        msg = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": payload},
        }
        try:
            await self.ws.send_json(msg)
        except Exception:
            logger.warning("Failed to send audio to Twilio (call=%s)", self.call.call_id)
            self._closed = True

    async def send_clear(self) -> None:
        """Twilio의 오디오 버퍼를 비운다 (interrupt 시 사용)."""
        if self._closed:
            return
        msg = {"event": "clear", "streamSid": self.stream_sid}
        try:
            await self.ws.send_json(msg)
        except Exception:
            pass

    async def close(self) -> None:
        """Stop future sends and close the underlying Stream idempotently."""
        self._closed = True
        if self._ws_closed:
            return
        self._ws_closed = True
        try:
            await self.ws.close()
        except Exception:
            pass

    @property
    def is_closed(self) -> bool:
        return self._closed
