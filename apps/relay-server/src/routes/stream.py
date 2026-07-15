"""WebSocket 스트리밍 엔드포인트.

App ↔ Relay Server: /relay/calls/{call_id}/stream
  - App에서 User 오디오/텍스트를 수신
  - App으로 자막/번역 오디오/상태 알림을 전송
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.call_manager import call_manager
from src.logging_config import call_id_var, call_mode_var, tenant_id_var
from src.types import WsMessage, WsMessageType

router = APIRouter(tags=["stream"])
logger = logging.getLogger(__name__)


@router.websocket("/calls/{call_id}/stream")
async def app_websocket(ws: WebSocket, call_id: str):
    """App ↔ Relay Server WebSocket 연결.

    User의 오디오/텍스트를 받아 Session A로 전달하고,
    Session B의 번역 결과를 App으로 전달한다.
    """
    await ws.accept()
    logger.info("App WebSocket connected (call=%s)", call_id)

    call = call_manager.get_call(call_id)
    if not call:
        # call 없으면 contextvar 설정 불가 — 에러 후 종료
        await ws.send_json(
            WsMessage(
                type=WsMessageType.ERROR,
                data={"message": "Call not found"},
            ).model_dump()
        )
        await ws.close()
        return

    # 구조화 로깅 컨텍스트 설정
    call_id_var.set(call_id)
    call_mode_var.set(call.communication_mode.value)
    tenant_id_var.set(str(call.tenant_id))

    # App WS를 call_manager에 등록 (AudioRouter가 이 WS로 메시지 전송)
    call_manager.register_app_ws(call_id, ws)

    # AudioRouter가 아직 없으면 Twilio 연결 대기 중
    if not call_manager.get_router(call_id):
        try:
            await ws.send_json(
                WsMessage(
                    type=WsMessageType.CALL_STATUS,
                    data={"status": "waiting", "message": "전화 연결 중..."},
                ).model_dump()
            )
        except Exception:
            pass

    try:
        while True:
            raw = await ws.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from App WS (call=%s)", call_id)
                continue

            msg_type = msg.get("type", "")

            audio_router = call_manager.get_router(call_id)
            if not audio_router:
                continue

            match msg_type:
                case "audio_chunk":
                    audio_b64 = msg.get("data", {}).get("audio", "")
                    if audio_b64:
                        await audio_router.handle_user_audio(audio_b64)
                case "vad_state":
                    state = msg.get("data", {}).get("state", "")
                    if state == "committed":
                        await audio_router.handle_user_audio_commit()
                case "text_input":
                    text = msg.get("data", {}).get("text", "")
                    if text:
                        await audio_router.handle_user_text(text)
                case "typing_state":
                    await audio_router.handle_typing_started()
                case "end_call":
                    logger.info("User ended call via WebSocket (call=%s)", call_id)
                    break

    except WebSocketDisconnect:
        logger.info("App WebSocket disconnected (call=%s)", call_id)
    except Exception as e:
        logger.error("App WebSocket error (call=%s): %s", call_id, e)
    finally:
        await call_manager.cleanup_call(call_id, reason="app_disconnected")


@router.websocket("/calls/{call_id}/monitor")
async def monitor_websocket(ws: WebSocket, call_id: str):
    """관전(read-only) WebSocket — 부스 시연용 모니터 화면.

    발신자가 진행 중인 통화의 자막/파이프라인/상태 이벤트를 그대로 수신만 한다.
    인바운드 오디오/텍스트는 처리하지 않으며(발신자 통화 오염 방지),
    이 소켓이 끊겨도 cleanup_call을 트리거하지 않는다(통화 계속 진행).
    """
    await ws.accept()
    logger.info("Monitor WebSocket connected (call=%s)", call_id)

    call = call_manager.get_call(call_id)
    if not call:
        await ws.send_json(
            WsMessage(
                type=WsMessageType.ERROR,
                data={"message": "Call not found"},
            ).model_dump()
        )
        await ws.close()
        return

    call_id_var.set(call_id)
    call_mode_var.set(call.communication_mode.value)
    tenant_id_var.set(str(call.tenant_id))

    call_manager.register_observer(call_id, ws)

    # 연결 직후 현재 통화 상태 스냅샷 전송 (화면이 빈 채로 시작하지 않도록)
    snapshot_status = "connected" if call_manager.get_router(call_id) else "waiting"
    try:
        await ws.send_json(
            WsMessage(
                type=WsMessageType.CALL_STATUS,
                data={
                    "status": snapshot_status,
                    "source_language": call.source_language,
                    "target_language": call.target_language,
                    "communication_mode": call.communication_mode.value,
                    "call_mode": call.mode.value,
                },
            ).model_dump()
        )
    except Exception:
        pass

    try:
        # 관전자는 송신하지 않는다 — receive는 오직 연결 해제 감지용.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        logger.info("Monitor WebSocket disconnected (call=%s)", call_id)
    except Exception as e:
        logger.error("Monitor WebSocket error (call=%s): %s", call_id, e)
    finally:
        call_manager.unregister_observer(call_id, ws)
