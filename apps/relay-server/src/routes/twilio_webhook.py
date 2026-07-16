"""Twilio webhook — TwiML 응답 + Media Stream 연결 + status callback."""

import asyncio
import json
import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse

from src.call_manager import call_manager
from src.logging_config import call_id_var, call_mode_var, tenant_id_var
from src.realtime.audio_router import AudioRouter
from src.twilio.media_stream import TwilioMediaStreamHandler
from src.twilio.signature import (
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
    """WI-6 인바운드 진입점의 서명 경계.

    실제 DID resolve/dispatch는 WI-6에서 연결한다. 그 전에는 검증된 요청도
    TwiML Reject로 안전하게 종료해 세션·비용을 만들지 않는다.
    """
    await validate_twilio_http_request(request)
    voice = VoiceResponse()
    voice.reject(reason="busy")
    return Response(content=str(voice), media_type="application/xml")


@router.post("/status-callback/{call_id}")
async def twilio_status_callback(
    call_id: str,
    request: Request,
):
    """Twilio 통화 상태 변경 콜백.

    수신자가 전화를 끊으면 completed/busy/no-answer 등이 오며,
    이때 cleanup_call()로 자동 정리한다.
    """
    form = await validate_twilio_http_request(request)
    call_status = str(form.get("CallStatus", ""))
    call_sid = str(form.get("CallSid", ""))
    call_duration = str(form.get("CallDuration", ""))

    call_id_var.set(call_id)
    logger.info(
        "Twilio status callback: call_id=%s, status=%s, sid=%s, duration=%s",
        call_id,
        call_status,
        call_sid,
        call_duration,
    )

    # 통화 종료 상태면 자동 정리
    terminal_statuses = {"completed", "failed", "busy", "no-answer", "canceled"}
    if call_status in terminal_statuses:
        await call_manager.cleanup_call(call_id, reason=f"twilio_{call_status}")

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
        logger.error("Twilio Media Stream: call %s not found", call_id)
        await ws.close()
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
