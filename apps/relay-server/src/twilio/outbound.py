"""Twilio outbound call — REST API를 사용하여 전화 발신."""

import asyncio
import logging

from twilio.rest import Client

from src.config import settings

logger = logging.getLogger(__name__)


_twilio_client: Client | None = None


def get_twilio_client() -> Client:
    global _twilio_client
    if _twilio_client is None:
        _twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    return _twilio_client


def resolve_outbound_number(tenant_id: str | None = None) -> str:
    """발신번호 seam (WI-3). 지금은 현행 단일번호를 반환 → 동작 동일.

    WI-3에서 tenant_call_config 조회로 교체하고, 이후 「아웃바운드 번호 풀」 PRD가
    이 함수 뒤에 할당기를 꽂는다. 호출부(make_call)는 불변.
    """
    return settings.twilio_phone_number


def make_call(
    phone_number: str,
    call_id: str,
    tenant_id: str | None = None,
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
        from_=resolve_outbound_number(tenant_id),
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
    tenant_id: str | None = None,
) -> str:
    """make_call의 async 래퍼 — 이벤트 루프를 블로킹하지 않는다."""
    return await asyncio.to_thread(
        make_call, phone_number=phone_number, call_id=call_id, tenant_id=tenant_id
    )
