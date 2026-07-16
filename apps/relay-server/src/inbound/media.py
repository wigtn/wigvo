"""WI-6 A: inbound waiting media, delayed session bootstrap, and handoff."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from functools import lru_cache
import logging
from pathlib import Path
import time
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect

from src.call_manager import call_manager
from src.capacity_manager import capacity_manager
from src.config import settings
from src.inbound.bootstrap import (
    BootstrapResult,
    BootstrapUnavailable,
    register_inbound_media_handlers,
)
from src.logging_config import call_id_var, call_mode_var, tenant_id_var
from src.prompt.generator_v3 import generate_session_a_prompt, generate_session_b_prompt
from src.realtime.audio_router import AudioRouter
from src.realtime.sessions.session_manager import DualSessionManager
from src.twilio.media_stream import TwilioMediaStreamHandler
from src.types import (
    ActiveCall,
    CallMode,
    CallStatus,
    CommunicationMode,
    VadMode,
    WsMessage,
)

logger = logging.getLogger(__name__)

_HOLD_ASSET = Path(__file__).resolve().parents[2] / "static/audio/inbound-hold.ulaw.b64"
_FRAME_BYTES = 160  # 20 ms of 8 kHz g711 µ-law
_HOLD_PAUSE_S = 1.75


@lru_cache(maxsize=1)
def _hold_audio() -> bytes:
    """Load the committed 200 ms µ-law waiting chime."""
    encoded = "".join(_HOLD_ASSET.read_text(encoding="ascii").split())
    audio = base64.b64decode(encoded, validate=True)
    if not audio or len(audio) % _FRAME_BYTES:
        raise RuntimeError("Inbound hold asset must contain complete 20 ms frames")
    return audio


@dataclass
class PendingCall:
    call_id: UUID
    tenant_id: UUID
    languages: tuple[str, str]
    provider_call_sid: str
    handler: "PendingMediaHandler | None" = None


class PendingMediaHandler:
    """Own one Twilio Stream from WAITING_FOR_AGENT through CONNECTED.

    This object is the only WebSocket reader. Handoff swaps the media consumer
    under ``_frame_lock`` so no second receive loop or Stream reconnect exists.
    """

    def __init__(self, ws: WebSocket, pending: PendingCall) -> None:
        self.ws = ws
        self.pending = pending
        self.call = ActiveCall(
            call_id=str(pending.call_id),
            call_sid=pending.provider_call_sid,
            tenant_id=pending.tenant_id,
            mode=CallMode.RELAY,
            source_language=pending.languages[0],
            target_language=pending.languages[1],
            status=CallStatus.PENDING,
            communication_mode=CommunicationMode.VOICE_TO_VOICE,
            started_at=time.time(),
        )
        self.twilio = TwilioMediaStreamHandler(ws=ws, call=self.call)
        self._frame_lock = asyncio.Lock()
        self._router: AudioRouter | None = None
        self._hold_task: asyncio.Task[None] | None = None
        self._waiting = False
        self._closed = False

    @property
    def handed_off(self) -> bool:
        return self._router is not None

    async def _hold_loop(self) -> None:
        frames = [
            _hold_audio()[offset : offset + _FRAME_BYTES]
            for offset in range(0, len(_hold_audio()), _FRAME_BYTES)
        ]
        try:
            while True:
                for frame in frames:
                    await self.twilio.send_audio(frame)
                    if self.twilio.is_closed:
                        return
                    await asyncio.sleep(0.02)
                await asyncio.sleep(_HOLD_PAUSE_S)
        except asyncio.CancelledError:
            return

    def _start_hold(self) -> None:
        if self._hold_task is None:
            self._hold_task = asyncio.create_task(self._hold_loop())

    async def _stop_hold(self) -> None:
        if self._hold_task is not None:
            self._hold_task.cancel()
            await self._hold_task
            self._hold_task = None

    async def handle_message(self, raw: str) -> None:
        event = await self.twilio.handle_message(raw)
        if event is None:
            return
        if event.event == "start" and not self._waiting:
            from src.inbound.service import dispatch_service

            waiting = await dispatch_service.mark_waiting(
                self.pending.call_id,
                self.pending.tenant_id,
            )
            if waiting is None:
                raise RuntimeError("Inbound dispatch cannot enter WAITING_FOR_AGENT")
            self._waiting = True
            self._start_hold()
            return
        if event.event == "media":
            audio = self.twilio.extract_audio(event)
            if not audio:
                return
            async with self._frame_lock:
                if self._router is not None:
                    await self._router.handle_twilio_audio(audio)

    async def handoff(self, router: AudioRouter) -> None:
        """Stop hold audio, start AudioRouter, then swap at a frame boundary."""
        async with self._frame_lock:
            if self._closed:
                raise RuntimeError("Inbound media disconnected during bootstrap")
            if self._router is not None:
                if self._router is router:
                    return
                raise RuntimeError("Inbound media already handed off")
            await self._stop_hold()
            await router.start()
            self._router = router

    async def run(self) -> None:
        reason = "twilio_disconnected"
        try:
            while not self._closed:
                raw = await self.ws.receive_text()
                await self.handle_message(raw)
                if self.twilio.is_closed:
                    reason = "twilio_stopped"
                    break
        except WebSocketDisconnect:
            logger.info("Inbound Twilio Stream disconnected (call=%s)", self.call.call_id)
        except Exception:
            reason = "twilio_media_error"
            logger.exception("Inbound Twilio Stream failed (call=%s)", self.call.call_id)
        finally:
            await cleanup_inbound_media(self.call.call_id, reason)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._stop_hold()
        await self.twilio.close()


class PendingMediaRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._calls: dict[str, PendingCall] = {}

    async def prepare(
        self,
        *,
        call_id: UUID,
        tenant_id: UUID,
        languages: list[str],
        provider_call_sid: str,
    ) -> PendingCall:
        if len(languages) < 2 or not languages[0] or not languages[1]:
            raise ValueError("Inbound tenant requires a two-language mapping")
        key = str(call_id)
        async with self._lock:
            existing = self._calls.get(key)
            if existing is not None:
                if (
                    existing.tenant_id != tenant_id
                    or existing.provider_call_sid != provider_call_sid
                ):
                    raise RuntimeError("Inbound call identity changed during retry")
                return existing
            pending = PendingCall(
                call_id=call_id,
                tenant_id=tenant_id,
                languages=(languages[0], languages[1]),
                provider_call_sid=provider_call_sid,
            )
            self._calls[key] = pending
            return pending

    async def attach(self, call_id: str, ws: WebSocket) -> PendingMediaHandler | None:
        async with self._lock:
            pending = self._calls.get(call_id)
            if pending is None:
                return None
            if pending.handler is not None:
                raise RuntimeError("Inbound call already has a Twilio Stream")
            handler = PendingMediaHandler(ws, pending)
            pending.handler = handler
            return handler

    async def get_handler(self, call_id: str) -> PendingMediaHandler | None:
        async with self._lock:
            pending = self._calls.get(call_id)
            return pending.handler if pending is not None else None

    async def contains(self, call_id: str) -> bool:
        async with self._lock:
            return call_id in self._calls

    async def pop(self, call_id: str) -> PendingCall | None:
        async with self._lock:
            return self._calls.pop(call_id, None)

    async def call_ids(self) -> list[str]:
        async with self._lock:
            return list(self._calls)


pending_media_registry = PendingMediaRegistry()


async def bootstrap_inbound_media(call_id: str, tenant_id: UUID) -> BootstrapResult:
    pending = await pending_media_registry.get_handler(call_id)
    if pending is None:
        raise BootstrapUnavailable("Inbound media stream is not connected")
    if pending.pending.tenant_id != tenant_id:
        raise PermissionError("Inbound media belongs to another tenant")
    if not await capacity_manager.reserve(call_id):
        raise BootstrapUnavailable("Relay is at call capacity")

    dual_session: DualSessionManager | None = None
    try:
        call = pending.call
        call.status = CallStatus.CONNECTED
        call.prompt_a = generate_session_a_prompt(
            mode=call.mode,
            source_language=call.source_language,
            target_language=call.target_language,
        )
        call.prompt_b = generate_session_b_prompt(
            source_language=call.source_language,
            target_language=call.target_language,
        )
        dual_session = DualSessionManager(
            mode=call.mode,
            source_language=call.source_language,
            target_language=call.target_language,
            vad_mode=VadMode.CLIENT,
            communication_mode=call.communication_mode,
        )
        await dual_session.connect(call.prompt_a, call.prompt_b)
        call.session_a_id = dual_session.session_a.session_id
        call.session_b_id = dual_session.session_b.session_id
        call_manager.register_session(call_id, dual_session)
        call_manager.register_call(call_id, call)
        if not await capacity_manager.commit(call_id):
            raise RuntimeError("Inbound capacity reservation disappeared before commit")

        async def send_to_app(message: WsMessage) -> None:
            await call_manager.send_to_app(call_id, message)

        router = AudioRouter(
            call=call,
            dual_session=dual_session,
            twilio_handler=pending.twilio,
            app_ws_send=send_to_app,
            prompt_a=call.prompt_a,
            prompt_b=call.prompt_b,
        )
        call_manager.register_router(call_id, router)
        listen_task = asyncio.create_task(dual_session.listen_all())
        call_manager.register_listen_task(call_id, listen_task)
        await pending.handoff(router)

        call_id_var.set(call_id)
        call_mode_var.set(call.communication_mode.value)
        tenant_id_var.set(str(tenant_id))
        ws_base = settings.relay_server_url.replace("https://", "wss://").replace(
            "http://", "ws://"
        )
        return BootstrapResult(
            relay_ws_url=f"{ws_base}/relay/calls/{call_id}/stream",
            source_language=call.source_language,
            target_language=call.target_language,
            communication_mode=call.communication_mode.value,
            call_mode=call.mode.value,
        )
    except BaseException:
        if dual_session is not None and call_manager.get_session(call_id) is None:
            await dual_session.close()
        await call_manager.cleanup_call(call_id, reason="inbound_bootstrap_failed")
        await capacity_manager.release(call_id)
        await capacity_manager.finish(call_id)
        raise


async def cleanup_inbound_media(call_id: str, reason: str) -> None:
    pending = await pending_media_registry.pop(call_id)
    if pending is not None and pending.handler is not None:
        await pending.handler.close()
    await call_manager.cleanup_call(call_id, reason=reason)
    await capacity_manager.release(call_id)
    await capacity_manager.finish(call_id)
    try:
        from src.inbound.service import dispatch_service

        await dispatch_service.finish(UUID(call_id), reason)
    except ValueError:
        return
    except Exception:
        logger.exception("Failed to finalize inbound media dispatch (call=%s)", call_id)


async def shutdown_inbound_media() -> None:
    for call_id in await pending_media_registry.call_ids():
        await cleanup_inbound_media(call_id, "server_shutdown")


def install_inbound_media_handlers() -> None:
    register_inbound_media_handlers(
        bootstrap=bootstrap_inbound_media,
        cleanup=cleanup_inbound_media,
    )
