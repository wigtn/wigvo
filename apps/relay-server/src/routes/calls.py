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

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from src.auth import AuthError, authenticate_http_request, authorize_tenant
from src.call_manager import call_manager
from src.capacity_manager import capacity_manager
from src.config import settings
from src.logging_config import call_id_var, call_mode_var, tenant_id_var
from src.observability import tracer
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
async def start_call(req: CallStartRequest, request: Request):
    """전화 발신을 시작하고 OpenAI Dual Session을 생성한다."""
    try:
        auth = await authenticate_http_request(request)
        tenant_id = authorize_tenant(auth, req.tenant_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    if call_manager.get_call(req.call_id):
        raise HTTPException(status_code=409, detail="Call already in progress")

    # 동시통화 하드캡 — CapacityManager가 active+reserved를 원자적으로 관리 (FR-5.5 seam).
    # 예약 실패 = 상한 초과 → 503 (기존 UX 유지). reserve~commit 사이 실패는 release.
    # (WI-5: 예상치 못한 예외 경로까지 try/finally 하드닝 + 인·아웃 혼합 동시성 테스트.)
    if not await capacity_manager.reserve(req.call_id):
        capacity = await capacity_manager.snapshot()
        logger.warning(
            "At capacity: %d/%d occupied (active=%d reserved=%d) — rejecting call %s",
            capacity.occupied,
            capacity.maximum,
            capacity.active,
            capacity.reserved,
            req.call_id,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "at_capacity",
                "active": capacity.active,
                "reserved": capacity.reserved,
                "occupied": capacity.occupied,
                "max": capacity.maximum,
                "message": "지금 통화가 가득 찼어요. 잠시 후 다시 시도해 주세요.",
            },
        )

    # 구조화 로깅 컨텍스트 설정 — 이후 모든 로그에 자동 주입
    call_id_var.set(req.call_id)
    call_mode_var.set(req.communication_mode.value)
    tenant_id_var.set(str(tenant_id))

    logger.info(
        "Starting call: id=%s, mode=%s, %s→%s",
        req.call_id,
        req.mode.value,
        req.source_language,
        req.target_language,
    )

    try:
        # 1. ActiveCall 생성
        call = ActiveCall(
            call_id=req.call_id,
            mode=req.mode,
            source_language=req.source_language,
            target_language=req.target_language,
            status=CallStatus.CALLING,
            collected_data=req.collected_data or {},
            communication_mode=req.communication_mode,
            tenant_id=tenant_id,
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
        tools_a = get_tools_for_mode(req.mode.value)
    except Exception as exc:
        logger.exception("Failed to prepare call %s", req.call_id)
        await capacity_manager.release(req.call_id)
        raise HTTPException(status_code=500, detail="Failed to prepare call") from exc

    # flow tracing seam 레퍼런스 (FR-5.1) — 제어 흐름만, PII attr 금지.
    # (root trace는 register_call 시점에 생성되므로 지금은 독립 스팬으로 잡힌다.)
    with tracer.flow_span(
        "calls.dual_session.connect", call_id=req.call_id, tenant_id=str(tenant_id)
    ):
        try:
            await dual_session.connect(prompt_a, prompt_b, tools_a=tools_a)
        except asyncio.CancelledError:
            await dual_session.close()
            await capacity_manager.release(req.call_id)
            raise
        except Exception as e:
            logger.error("Failed to create OpenAI sessions: %s", e)
            await capacity_manager.release(req.call_id)
            raise HTTPException(status_code=502, detail="Failed to create AI sessions")

    # 즉시 session 등록 (Twilio 실패 시 cleanup_call로 정리)
    call_manager.register_session(req.call_id, dual_session)

    call.session_a_id = dual_session.session_a.session_id
    call.session_b_id = dual_session.session_b.session_id

    # 4. Twilio 발신 (async)
    if settings.load_test_mode:
        # 부하테스트: 실제 Twilio 발신 없이 가짜 SID 부여. 미디어 스트림은
        # 부하 하니스가 /twilio/media-stream WS로 직접 연결해 오디오를 주입한다.
        call.call_sid = f"loadtest-{req.call_id}"
    else:
        try:
            call_sid = await make_call_async(
                phone_number=req.phone_number,
                call_id=req.call_id,
                tenant_id=tenant_id,
            )
            call.call_sid = call_sid
        except asyncio.CancelledError:
            await call_manager.cleanup_call(req.call_id, reason="start_cancelled")
            await capacity_manager.release(req.call_id)
            raise
        except Exception as e:
            logger.error("Failed to make Twilio call: %s", e)
            await call_manager.cleanup_call(req.call_id, reason="twilio_failed")
            await capacity_manager.release(req.call_id)
            raise HTTPException(status_code=502, detail="Failed to initiate phone call")

    # 5. Active call 등록 (active++). 예약을 active로 확정(commit).
    try:
        call_manager.register_call(req.call_id, call)
        if not await capacity_manager.commit(req.call_id):
            raise RuntimeError("capacity reservation disappeared before commit")
    except asyncio.CancelledError:
        await call_manager.cleanup_call(req.call_id, reason="start_cancelled")
        await capacity_manager.release(req.call_id)
        raise
    except Exception as exc:
        logger.exception("Failed to activate call %s", req.call_id)
        await call_manager.cleanup_call(req.call_id, reason="start_failed")
        await capacity_manager.release(req.call_id)
        raise HTTPException(status_code=500, detail="Failed to activate call") from exc

    # WebSocket URL 생성
    ws_base = settings.relay_server_url.replace("http", "ws")
    relay_ws_url = f"{ws_base}/relay/calls/{req.call_id}/stream"

    logger.info(
        "Call started: id=%s, sid=%s, ws=%s",
        req.call_id,
        call.call_sid,
        relay_ws_url,
    )

    return CallStartResponse(
        call_id=req.call_id,
        call_sid=call.call_sid,
        relay_ws_url=relay_ws_url,
        session_ids={
            "session_a": call.session_a_id,
            "session_b": call.session_b_id,
        },
    )


@router.post("/calls/{call_id}/end")
async def end_call(call_id: str, request: Request, req: CallEndRequest | None = None):
    """통화를 종료한다."""
    try:
        auth = await authenticate_http_request(request)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    call = call_manager.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    try:
        authorize_tenant(auth, call.tenant_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    # 구조화 로깅 컨텍스트 설정
    call_id_var.set(call_id)
    call_mode_var.set(call.communication_mode.value)
    tenant_id_var.set(str(call.tenant_id))

    reason = req.reason if req else "user_hangup"
    logger.info("Ending call: id=%s, reason=%s", call_id, reason)

    # 중앙 정리 (Twilio 종료 + DB persist 포함)
    await call_manager.cleanup_call(call_id, reason=reason)

    return {"status": "ended", "call_id": call_id, "reason": reason}
