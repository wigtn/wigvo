"""Twilio webhook — TwiML 응답 + Media Stream 연결 + status callback."""

import asyncio
import json
import logging

from fastapi import APIRouter, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from src.call_manager import call_manager
from src.config import settings
from src.logging_config import call_id_var, call_mode_var
from src.realtime.audio_router import AudioRouter
from src.twilio.media_stream import TwilioMediaStreamHandler
from src.types import WsMessage, WsMessageType

router = APIRouter(tags=["twilio"])
logger = logging.getLogger(__name__)


@router.post("/webhook/{call_id}")
async def twilio_webhook(call_id: str):
    """Twilio가 전화를 연결하면 호출하는 webhook.

    TwiML로 Media Stream을 연결하여 양방향 오디오 스트리밍을 시작한다.
    """
    ws_url = settings.relay_server_url.replace("http", "ws")
    stream_url = f"{ws_url}/twilio/media-stream/{call_id}"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}">
            <Parameter name="call_id" value="{call_id}" />
        </Stream>
    </Connect>
</Response>"""

    call_id_var.set(call_id)
    logger.info("TwiML webhook for call_id=%s, stream_url=%s", call_id, stream_url)
    return Response(content=twiml, media_type="application/xml")


@router.post("/status-callback/{call_id}")
async def twilio_status_callback(
    call_id: str,
    request: Request,
    CallStatus: str = Form(""),
    CallSid: str = Form(""),
    CallDuration: str = Form(""),
):
    """Twilio 통화 상태 변경 콜백.

    수신자가 전화를 끊으면 completed/busy/no-answer 등이 오며,
    이때 cleanup_call()로 자동 정리한다.
    """
    call_id_var.set(call_id)
    logger.info(
        "Twilio status callback: call_id=%s, status=%s, sid=%s, duration=%s",
        call_id,
        CallStatus,
        CallSid,
        CallDuration,
    )

    # 통화 종료 상태면 자동 정리
    terminal_statuses = {"completed", "failed", "busy", "no-answer", "canceled"}
    if CallStatus in terminal_statuses:
        await call_manager.cleanup_call(call_id, reason=f"twilio_{CallStatus}")

    return {"status": "ok"}


@router.websocket("/media-stream/{call_id}")
async def twilio_media_stream(ws: WebSocket, call_id: str):
    """Twilio Media Stream WebSocket.

    TwiML <Stream>이 연결하는 엔드포인트.
    수신자 오디오 → Session B, Session A TTS → Twilio.

    DualSession은 calls.py start_call()에서 이미 생성되어 있으므로
    call_manager에서 가져와 재사용한다.
    """
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
