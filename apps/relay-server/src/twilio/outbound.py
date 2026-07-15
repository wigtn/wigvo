"""Twilio outbound call — REST API를 사용하여 전화 발신."""

import asyncio
import logging
from uuid import UUID

from twilio.rest import Client

from src.config import settings

logger = logging.getLogger(__name__)


_twilio_client: Client | None = None


def get_twilio_client() -> Client:
    global _twilio_client
    if _twilio_client is None:
        _twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    return _twilio_client


async def resolve_outbound_number(tenant_id: UUID | str) -> str:
    """tenant_call_config에서 발신번호를 fail-closed로 조회한다."""
    from src.db.pg_client import get_tenant_outbound_number

    return await get_tenant_outbound_number(tenant_id)


def make_call(
    phone_number: str,
    call_id: str,
    outbound_number: str,
) -> str:
    """Twilio REST API로 아웃바운드 콜을 발신하고 call_sid를 반환한다.

    통화 시작 시퀀스 (PRD 3.1):
      1. App → Relay Server: POST /relay/calls/start
      2. Relay Server: Twilio REST API로 발신  ← 여기
      3. Twilio → Relay Server: webhook (TwiML 응답)
      4. Twilio → Relay Server: Media Stream WebSocket
    """
    client = get_twilio_client()

    webhook_url = f"{settings.relay_server_url}/twilio/webhook/{call_id}"
    status_callback_url = (
        f"{settings.relay_server_url}/twilio/status-callback/{call_id}"
    )

    logger.info("Making outbound call to %s (call_id=%s)", phone_number, call_id)

    call = client.calls.create(
        to=phone_number,
        from_=outbound_number,
        url=webhook_url,
        status_callback=status_callback_url,
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        timeout=settings.recipient_answer_timeout_s,
    )

    logger.info("Twilio call created: sid=%s", call.sid)
    return call.sid


async def make_call_async(
    phone_number: str,
    call_id: str,
    tenant_id: UUID | str,
) -> str:
    """make_call의 async 래퍼 — 이벤트 루프를 블로킹하지 않는다."""
    outbound_number = await resolve_outbound_number(tenant_id)
    return await asyncio.to_thread(
        make_call,
        phone_number=phone_number,
        call_id=call_id,
        outbound_number=outbound_number,
    )
