"""통화 시작/종료 API 엔드포인트.

PRD 8.2:
  POST /relay/calls/start — 전화 발신 + Realtime Session 시작
  POST /relay/calls/{call_id}/end — 통화 종료

통화 시작 시퀀스 (PRD 3.1):
  1. App → Relay Server: POST /relay/calls/start
  2. Relay Server: Twilio 발신 + OpenAI Dual Session 생성
  3. Relay Server → Supabase: call 상태를 CALLING으로 업데이트
  4. Relay Server → App: { relayWsUrl, callSid, sessionIds }
  5. App → Relay Server: WebSocket 연결
"""

import logging

from fastapi import APIRouter, HTTPException

from src.call_manager import call_manager
from src.config import settings
from src.logging_config import call_id_var, call_mode_var
from src.prompt.generator_v3 import generate_session_a_prompt, generate_session_b_prompt
from src.realtime.sessions.session_manager import DualSessionManager
from src.tools.definitions import get_tools_for_mode
from src.twilio.outbound import make_call_async
from src.types import (
    ActiveCall,
    CallEndRequest,
    CallStartRequest,
    CallStartResponse,
    CallStatus,
)

router = APIRouter(tags=["calls"])
logger = logging.getLogger(__name__)


@router.post("/calls/start", response_model=CallStartResponse)
async def start_call(req: CallStartRequest):
    """전화 발신을 시작하고 OpenAI Dual Session을 생성한다."""
    if call_manager.get_call(req.call_id):
        raise HTTPException(status_code=409, detail="Call already in progress")

    # 구조화 로깅 컨텍스트 설정 — 이후 모든 로그에 자동 주입
    call_id_var.set(req.call_id)
    call_mode_var.set(req.communication_mode.value)

    logger.info(
        "Starting call: id=%s, mode=%s, %s→%s",
        req.call_id,
        req.mode.value,
        req.source_language,
        req.target_language,
    )

    # 1. ActiveCall 생성
    call = ActiveCall(
        call_id=req.call_id,
        mode=req.mode,
        source_language=req.source_language,
        target_language=req.target_language,
        status=CallStatus.CALLING,
        collected_data=req.collected_data or {},
        communication_mode=req.communication_mode,
    )

    # 2. System Prompt 생성
    if req.system_prompt_override:
        prompt_a = req.system_prompt_override
    else:
        prompt_a = generate_session_a_prompt(
            mode=req.mode,
            source_language=req.source_language,
            target_language=req.target_language,
            collected_data=req.collected_data,
        )
    prompt_b = generate_session_b_prompt(
        source_language=req.source_language,
        target_language=req.target_language,
    )

    # Prompt를 ActiveCall에 저장 (AudioRouter에서 사용)
    call.prompt_a = prompt_a
    call.prompt_b = prompt_b

    # 3. OpenAI Dual Session 생성 (vad_mode + communication_mode 전달 — PRD 4.2)
    dual_session = DualSessionManager(
        mode=req.mode,
        source_language=req.source_language,
        target_language=req.target_language,
        vad_mode=req.vad_mode,
        communication_mode=req.communication_mode,
    )

    # Agent Mode: Function Calling 도구 설정
    tools_a = get_tools_for_mode(req.mode.value)

    try:
        await dual_session.connect(prompt_a, prompt_b, tools_a=tools_a)
    except Exception as e:
        logger.error("Failed to create OpenAI sessions: %s", e)
        raise HTTPException(status_code=502, detail="Failed to create AI sessions")

    # 즉시 session 등록 (Twilio 실패 시 cleanup_call로 정리)
    call_manager.register_session(req.call_id, dual_session)

    call.session_a_id = dual_session.session_a.session_id
    call.session_b_id = dual_session.session_b.session_id

    # 4. Twilio 발신 (async)
    try:
        call_sid = await make_call_async(
            phone_number=req.phone_number,
            call_id=req.call_id,
        )
        call.call_sid = call_sid
    except Exception as e:
        logger.error("Failed to make Twilio call: %s", e)
        await call_manager.cleanup_call(req.call_id, reason="twilio_failed")
        raise HTTPException(status_code=502, detail="Failed to initiate phone call")

    # 5. Active call 등록
    call_manager.register_call(req.call_id, call)

    # WebSocket URL 생성
    ws_base = settings.relay_server_url.replace("http", "ws")
    relay_ws_url = f"{ws_base}/relay/calls/{req.call_id}/stream"

    logger.info(
        "Call started: id=%s, sid=%s, ws=%s",
        req.call_id,
        call_sid,
        relay_ws_url,
    )

    return CallStartResponse(
        call_id=req.call_id,
        call_sid=call_sid,
        relay_ws_url=relay_ws_url,
        session_ids={
            "session_a": call.session_a_id,
            "session_b": call.session_b_id,
        },
    )


@router.post("/calls/{call_id}/end")
async def end_call(call_id: str, req: CallEndRequest | None = None):
    """통화를 종료한다."""
    call = call_manager.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    # 구조화 로깅 컨텍스트 설정
    call_id_var.set(call_id)
    call_mode_var.set(call.communication_mode.value)

    reason = req.reason if req else "user_hangup"
    logger.info("Ending call: id=%s, reason=%s", call_id, reason)

    # 중앙 정리 (Twilio 종료 + DB persist 포함)
    await call_manager.cleanup_call(call_id, reason=reason)

    return {"status": "ended", "call_id": call_id, "reason": reason}
