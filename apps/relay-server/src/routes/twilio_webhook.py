"""Twilio webhook — TwiML 응답 + Media Stream 연결 + status callback."""

import asyncio
import json
import logging
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse

from src.call_manager import call_manager
from src.inbound.bootstrap import cleanup_inbound_session
from src.inbound.media import pending_media_registry
from src.inbound.service import (
    DispatchError,
    DispatchNotFound,
    DispatchUnavailable,
    dispatch_service,
)
from src.logging_config import call_id_var, call_mode_var, tenant_id_var
from src.realtime.audio_router import AudioRouter
from src.twilio.media_stream import TwilioMediaStreamHandler
from src.twilio.signature import (
    public_http_url,
    public_websocket_url,
    validate_twilio_http_request,
    validate_twilio_websocket,
)
from src.types import WsMessage, WsMessageType

router = APIRouter(tags=["twilio"])
logger = logging.getLogger(__name__)


@router.post("/webhook/{call_id}")
async def twilio_webhook(call_id: str, request: Request):
    """Twilio가 전화를 연결하면 호출하는 webhook.

    TwiML로 Media Stream을 연결하여 양방향 오디오 스트리밍을 시작한다.
    """
    await validate_twilio_http_request(request)
    stream_url = public_websocket_url(f"/twilio/media-stream/{call_id}")
    voice = VoiceResponse()
    stream = voice.connect().stream(url=stream_url)
    stream.parameter(name="call_id", value=call_id)

    call_id_var.set(call_id)
    logger.info("TwiML webhook for call_id=%s, stream_url=%s", call_id, stream_url)
    return Response(content=str(voice), media_type="application/xml")


@router.post("/incoming")
async def twilio_incoming(request: Request):
    """Resolve DID, create lightweight dispatch, and connect pending media."""
    form = await validate_twilio_http_request(request)
    voice = VoiceResponse()
    provider_call_sid = str(form.get("CallSid", "")).strip()
    inbound_number = str(form.get("To", "")).strip()
    if not provider_call_sid or not inbound_number:
        logger.warning("Rejected inbound call with missing CallSid/To")
        voice.reject(reason="rejected")
        return Response(content=str(voice), media_type="application/xml")

    try:
        tenant_id, languages = await dispatch_service.resolve_tenant(inbound_number)
    except DispatchNotFound:
        logger.warning("Rejected unmapped inbound DID=%s", inbound_number)
        voice.reject(reason="rejected")
        return Response(content=str(voice), media_type="application/xml")
    except Exception:
        logger.exception("Inbound DID resolution unavailable")
        voice.reject(reason="busy")
        return Response(content=str(voice), media_type="application/xml")
    if len(languages) < 2 or not languages[0] or not languages[1]:
        logger.error("Rejected inbound DID with invalid language mapping=%s", inbound_number)
        voice.reject(reason="rejected")
        return Response(content=str(voice), media_type="application/xml")

    deterministic_call_id = uuid5(NAMESPACE_URL, f"wigvo:twilio:{provider_call_sid}")
    dispatch = None
    try:
        dispatch = await dispatch_service.create_ringing(
            call_id=deterministic_call_id,
            tenant_id=tenant_id,
            provider_call_sid=provider_call_sid,
        )
        await pending_media_registry.prepare(
            call_id=dispatch.call_id,
            tenant_id=dispatch.tenant_id,
            languages=dispatch.languages or languages,
            provider_call_sid=provider_call_sid,
        )
    except DispatchUnavailable:
        logger.warning("Rejected inbound call because waiting queue is full")
        voice.reject(reason="busy")
        return Response(content=str(voice), media_type="application/xml")
    except (DispatchError, RuntimeError, ValueError):
        logger.exception("Failed to create inbound dispatch")
        if dispatch is not None:
            try:
                await dispatch_service.finish(dispatch.call_id, "media_prepare_failed")
            except Exception:
                logger.exception("Failed to roll back inbound dispatch")
        voice.reject(reason="rejected")
        return Response(content=str(voice), media_type="application/xml")

    stream_url = public_websocket_url(f"/twilio/media-stream/{dispatch.call_id}")
    status_callback_url = public_http_url(
        f"/twilio/status-callback/{dispatch.call_id}"
    )
    stream = voice.connect().stream(
        url=stream_url,
        status_callback=status_callback_url,
        status_callback_method="POST",
    )
    stream.parameter(name="call_id", value=str(dispatch.call_id))
    stream.parameter(name="direction", value="inbound")
    logger.info(
        "Inbound dispatch created call=%s tenant=%s did=%s",
        dispatch.call_id,
        dispatch.tenant_id,
        inbound_number,
    )
    return Response(content=str(voice), media_type="application/xml")


@router.post("/status-callback/{call_id}")
async def twilio_status_callback(
    call_id: str,
    request: Request,
):
    """Twilio 통화/Media Stream 상태 변경 콜백.

    통화 종료 상태나 stream-stopped/stream-error를 받으면 인바운드는
    media cleanup seam, 아웃바운드는 call_manager로 자동 정리한다.
    """
    form = await validate_twilio_http_request(request)
    call_status = str(form.get("CallStatus", ""))
    stream_event = str(form.get("StreamEvent", ""))
    call_sid = str(form.get("CallSid", ""))
    call_duration = str(form.get("CallDuration", ""))

    call_id_var.set(call_id)
    logger.info(
        "Twilio status callback: call_id=%s, status=%s, stream_event=%s, "
        "sid=%s, duration=%s",
        call_id,
        call_status,
        stream_event,
        call_sid,
        call_duration,
    )

    # 통화 종료 상태면 자동 정리
    terminal_statuses = {"completed", "failed", "busy", "no-answer", "canceled"}
    terminal_stream_events = {"stream-stopped", "stream-error"}
    if call_status in terminal_statuses or stream_event in terminal_stream_events:
        reason = (
            f"twilio_{call_status}"
            if call_status in terminal_statuses
            else f"twilio_{stream_event.replace('-', '_')}"
        )
        if await pending_media_registry.contains(call_id):
            await cleanup_inbound_session(call_id, reason)
        else:
            await call_manager.cleanup_call(call_id, reason=reason)

    return {"status": "ok"}


@router.websocket("/media-stream/{call_id}")
async def twilio_media_stream(ws: WebSocket, call_id: str):
    """Twilio Media Stream WebSocket.

    TwiML <Stream>이 연결하는 엔드포인트.
    수신자 오디오 → Session B, Session A TTS → Twilio.

    DualSession은 calls.py start_call()에서 이미 생성되어 있으므로
    call_manager에서 가져와 재사용한다.
    """
    if not await validate_twilio_websocket(ws):
        return
    await ws.accept()
    logger.info("Twilio Media Stream connected (call=%s)", call_id)

    call = call_manager.get_call(call_id)
    if not call:
        try:
            pending_handler = await pending_media_registry.attach(call_id, ws)
        except RuntimeError:
            logger.warning("Duplicate inbound Twilio Stream rejected (call=%s)", call_id)
            await ws.close(code=4409, reason="Twilio Stream already connected")
            return
        if pending_handler is None:
            logger.error("Twilio Media Stream: call %s not found", call_id)
            await ws.close()
            return
        call_id_var.set(call_id)
        call_mode_var.set("voice_to_voice")
        tenant_id_var.set(str(pending_handler.pending.tenant_id))
        await pending_handler.run()
        return

    # 구조화 로깅 컨텍스트 설정 — 이후 모든 하위 태스크에 자동 전파
    call_id_var.set(call_id)
    call_mode_var.set(call.communication_mode.value)
    tenant_id_var.set(str(call.tenant_id))

    # DualSession은 start_call()에서 이미 생성됨 — 재사용
    dual_session = call_manager.get_session(call_id)
    if not dual_session:
        logger.error("Twilio Media Stream: session for call %s not found", call_id)
        await ws.close()
        return

    # Twilio handler 생성
    twilio_handler = TwilioMediaStreamHandler(ws=ws, call=call)

    # App WS로 메시지 전송 — call_manager를 통해 직접 전송
    async def send_to_app(msg: WsMessage) -> None:
        await call_manager.send_to_app(call_id, msg)

    # AudioRouter 생성 + 등록
    audio_router = AudioRouter(
        call=call,
        dual_session=dual_session,
        twilio_handler=twilio_handler,
        app_ws_send=send_to_app,
        prompt_a=call.prompt_a,
        prompt_b=call.prompt_b,
    )
    call_manager.register_router(call_id, audio_router)
    await audio_router.start()

    # OpenAI 세션 리스닝 시작 (백그라운드) + 등록
    listen_task = asyncio.create_task(dual_session.listen_all())
    call_manager.register_listen_task(call_id, listen_task)

    # 수신자 픽업 알림: Twilio 미디어 스트림 연결 = 통화 응답됨.
    # 첫 발화를 기다리지 않고 이 시점에 connected를 알려 앱/관전 화면이 즉시 연결 상태가 된다.
    # (first_message가 첫 발화 시 다시 connected를 보내지만 동일 상태라 무해)
    await send_to_app(
        WsMessage(
            type=WsMessageType.CALL_STATUS,
            data={"status": "connected", "message": "통화가 연결되었습니다."},
        )
    )

    try:
        while True:
            raw = await ws.receive_text()

            try:
                parsed = await twilio_handler.handle_message(raw)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from Twilio (call=%s)", call_id)
                continue

            if parsed and parsed.event == "media":
                audio = twilio_handler.extract_audio(parsed)
                if audio:
                    await audio_router.handle_twilio_audio(audio)

            if twilio_handler.is_closed:
                break

    except WebSocketDisconnect:
        logger.info("Twilio Media Stream disconnected (call=%s)", call_id)
    except Exception as e:
        logger.error("Twilio Media Stream error (call=%s): %s", call_id, e)
    finally:
        await call_manager.cleanup_call(call_id, reason="twilio_disconnected")
