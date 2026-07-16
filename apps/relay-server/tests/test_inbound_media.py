"""WI-6 A inbound entry, pending media, bootstrap, and cleanup."""

from __future__ import annotations

import base64
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlencode
from uuid import UUID

from fastapi import Request, WebSocketDisconnect
import pytest
import pytest_asyncio
from twilio.request_validator import RequestValidator

from src.call_manager import call_manager
from src.capacity_manager import capacity_manager
from src.config import settings
import src.inbound.media as media_module
from src.inbound.media import (
    PendingCall,
    PendingMediaHandler,
    bootstrap_inbound_media,
    cleanup_inbound_media,
    pending_media_registry,
)
from src.inbound.models import DispatchRecord, DispatchState
from src.inbound.service import DispatchNotFound
from src.routes.stream import app_websocket
from src.routes.twilio_webhook import twilio_incoming

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
CALL_ID = UUID("50000000-0000-0000-0000-000000000005")
AUTH_TOKEN = "twilio-inbound-test-token"
PUBLIC_BASE = "https://relay.example.com"


class FakeWebSocket:
    def __init__(self, receives: list[str | BaseException] | None = None) -> None:
        self.receives = list(receives or [])
        self.sent: list[dict] = []
        self.accept_calls: list[str | None] = []
        self.close_calls: list[tuple[int, str | None]] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accept_calls.append(subprotocol)

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)

    async def receive_text(self) -> str:
        if not self.receives:
            raise WebSocketDisconnect()
        item = self.receives.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_calls.append((code, reason))


def make_dispatch() -> DispatchRecord:
    now = datetime.now(timezone.utc)
    return DispatchRecord(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        provider_call_sid="CA-inbound",
        state=DispatchState.RINGING,
        version=0,
        created_at=now,
        updated_at=now,
        languages=["ko", "en"],
    )


def make_pending() -> PendingCall:
    return PendingCall(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        languages=("ko", "en"),
        provider_call_sid="CA-inbound",
    )


def signed_request(path: str, form: dict[str, str]) -> Request:
    body = urlencode(form).encode()
    signature = RequestValidator(AUTH_TOKEN).compute_signature(
        f"{PUBLIC_BASE}{path}",
        form,
    )
    delivered = False

    async def receive() -> dict:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"x-twilio-signature", signature.encode()),
            ],
            "server": ("relay.example.com", 443),
            "client": ("127.0.0.1", 1234),
        },
        receive,
    )


@pytest_asyncio.fixture(autouse=True)
async def clean_global_inbound_state(monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)
    monkeypatch.setattr(settings, "public_callback_base_url", PUBLIC_BASE)
    monkeypatch.setattr(settings, "load_test_mode", True)
    for call_id in await pending_media_registry.call_ids():
        pending = await pending_media_registry.pop(call_id)
        if pending and pending.handler:
            await pending.handler.close()
    await capacity_manager.release(str(CALL_ID))
    await capacity_manager.finish(str(CALL_ID))
    call_manager._calls.pop(str(CALL_ID), None)
    call_manager._sessions.pop(str(CALL_ID), None)
    call_manager._routers.pop(str(CALL_ID), None)
    yield
    with patch("src.inbound.service.dispatch_service.finish", new=AsyncMock()):
        if await pending_media_registry.contains(str(CALL_ID)):
            await cleanup_inbound_media(str(CALL_ID), "test_cleanup")
    await capacity_manager.release(str(CALL_ID))
    await capacity_manager.finish(str(CALL_ID))


@pytest.mark.asyncio
async def test_incoming_creates_dispatch_and_stream_without_ai_or_capacity():
    form = {"CallSid": "CA-inbound", "To": "+12025550100"}
    dispatch = make_dispatch()
    with (
        patch(
            "src.routes.twilio_webhook.dispatch_service.resolve_tenant",
            new=AsyncMock(return_value=(TENANT_ID, ["ko", "en"])),
        ),
        patch(
            "src.routes.twilio_webhook.dispatch_service.create_ringing",
            new=AsyncMock(return_value=dispatch),
        ),
    ):
        response = await twilio_incoming(signed_request("/twilio/incoming", form))

    body = response.body.decode()
    assert response.status_code == 200
    assert f"wss://relay.example.com/twilio/media-stream/{CALL_ID}" in body
    assert (
        f'statusCallback="https://relay.example.com/twilio/status-callback/{CALL_ID}"'
        in body
    )
    assert 'statusCallbackMethod="POST"' in body
    assert await pending_media_registry.contains(str(CALL_ID))
    assert call_manager.get_session(str(CALL_ID)) is None
    snapshot = await capacity_manager.snapshot()
    assert snapshot.active == 0
    assert snapshot.reserved == 0


@pytest.mark.asyncio
async def test_unmapped_incoming_did_is_rejected_fail_closed():
    form = {"CallSid": "CA-unmapped", "To": "+12025550999"}
    with patch(
        "src.routes.twilio_webhook.dispatch_service.resolve_tenant",
        new=AsyncMock(side_effect=DispatchNotFound("not mapped")),
    ):
        response = await twilio_incoming(signed_request("/twilio/incoming", form))

    assert '<Reject reason="rejected"' in response.body.decode()
    assert await pending_media_registry.call_ids() == []


@pytest.mark.asyncio
async def test_stream_start_marks_waiting_and_sends_static_ulaw_chime():
    ws = FakeWebSocket()
    handler = PendingMediaHandler(ws, make_pending())
    waiting = MagicMock()
    with patch(
        "src.inbound.service.dispatch_service.mark_waiting",
        new=AsyncMock(return_value=waiting),
    ) as mark_waiting:
        await handler.handle_message(
            json.dumps(
                {
                    "event": "start",
                    "streamSid": "MZ-inbound",
                    "start": {"streamSid": "MZ-inbound"},
                }
            )
        )
        await asyncio.sleep(0.05)

    mark_waiting.assert_awaited_once_with(CALL_ID, TENANT_ID)
    assert ws.sent and ws.sent[0]["event"] == "media"
    assert len(base64.b64decode(ws.sent[0]["media"]["payload"])) == 160
    assert call_manager.get_session(str(CALL_ID)) is None
    await handler.close()


@pytest.mark.asyncio
async def test_stream_start_plays_notice_before_hold_chime():
    ws = FakeWebSocket()
    handler = PendingMediaHandler(ws, make_pending())
    with patch(
        "src.inbound.service.dispatch_service.mark_waiting",
        new=AsyncMock(return_value=MagicMock()),
    ):
        await handler.handle_message(
            json.dumps(
                {
                    "event": "start",
                    "streamSid": "MZ-inbound",
                    "start": {"streamSid": "MZ-inbound"},
                }
            )
        )
        await asyncio.sleep(0.05)

    sent = b"".join(
        base64.b64decode(m["media"]["payload"])
        for m in ws.sent
        if m["event"] == "media"
    )
    # The AI-interpretation disclosure (착신 직후 고지) plays first: the opening
    # frames are the notice asset, not the hold chime (the two assets differ).
    assert sent, "no audio was streamed to the caller"
    assert media_module._notice_audio()[:160] != media_module._hold_audio()[:160]
    assert sent[:160] == media_module._notice_audio()[:160]
    assert media_module._notice_audio().startswith(sent)
    await handler.close()


@pytest.mark.asyncio
async def test_handoff_reuses_same_stream_and_switches_on_frame_boundary():
    ws = FakeWebSocket()
    handler = PendingMediaHandler(ws, make_pending())
    audio = b"\xff" * 160
    media = json.dumps(
        {
            "event": "media",
            "streamSid": "MZ-inbound",
            "media": {"payload": base64.b64encode(audio).decode()},
        }
    )
    router = MagicMock()
    router.start = AsyncMock()
    router.handle_twilio_audio = AsyncMock()

    await handler.handle_message(media)
    router.handle_twilio_audio.assert_not_awaited()
    await handler.handoff(router)
    await handler.handle_message(media)

    router.start.assert_awaited_once()
    router.handle_twilio_audio.assert_awaited_once_with(audio)
    assert handler.handed_off is True
    assert ws.close_calls == []
    await handler.close()


@pytest.mark.asyncio
async def test_bootstrap_reserves_connects_commits_and_hands_off(monkeypatch):
    await pending_media_registry.prepare(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        languages=["ko", "en"],
        provider_call_sid="CA-inbound",
    )
    handler = await pending_media_registry.attach(str(CALL_ID), FakeWebSocket())
    assert handler is not None

    dual = MagicMock()
    dual.session_a = SimpleNamespace(session_id="sess-a")
    dual.session_b = SimpleNamespace(session_id="sess-b")
    dual.connect = AsyncMock()
    dual.close = AsyncMock()
    dual.listen_all = AsyncMock()
    router = MagicMock()
    router.start = AsyncMock()
    router.stop = AsyncMock()
    router.handle_twilio_audio = AsyncMock()
    monkeypatch.setattr(media_module, "DualSessionManager", MagicMock(return_value=dual))
    monkeypatch.setattr(media_module, "AudioRouter", MagicMock(return_value=router))

    result = await bootstrap_inbound_media(str(CALL_ID), TENANT_ID)

    assert result.source_language == "ko"
    assert result.target_language == "en"
    assert result.role == "agent"
    assert handler.handed_off is True
    assert call_manager.get_session(str(CALL_ID)) is dual
    assert call_manager.get_router(str(CALL_ID)) is router
    snapshot = await capacity_manager.snapshot()
    assert snapshot.active == 1
    assert snapshot.reserved == 0
    with patch("src.inbound.service.dispatch_service.finish", new=AsyncMock()):
        await cleanup_inbound_media(str(CALL_ID), "test_end")
    final = await capacity_manager.snapshot()
    assert final.active == 0
    assert final.reserved == 0


@pytest.mark.asyncio
async def test_bootstrap_connect_failure_releases_reservation_and_session(monkeypatch):
    await pending_media_registry.prepare(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        languages=["ko", "en"],
        provider_call_sid="CA-inbound",
    )
    handler = await pending_media_registry.attach(str(CALL_ID), FakeWebSocket())
    assert handler is not None

    dual = MagicMock()
    dual.connect = AsyncMock(side_effect=RuntimeError("OpenAI unavailable"))
    dual.close = AsyncMock()
    monkeypatch.setattr(media_module, "DualSessionManager", MagicMock(return_value=dual))

    with pytest.raises(RuntimeError, match="OpenAI unavailable"):
        await bootstrap_inbound_media(str(CALL_ID), TENANT_ID)

    dual.close.assert_awaited_once()
    assert call_manager.get_session(str(CALL_ID)) is None
    snapshot = await capacity_manager.snapshot()
    assert snapshot.active == 0
    assert snapshot.reserved == 0


@pytest.mark.asyncio
async def test_bootstrap_cancellation_releases_reservation(monkeypatch):
    await pending_media_registry.prepare(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        languages=["ko", "en"],
        provider_call_sid="CA-inbound",
    )
    handler = await pending_media_registry.attach(str(CALL_ID), FakeWebSocket())
    assert handler is not None

    entered_connect = asyncio.Event()
    hold_connect = asyncio.Event()

    async def connect(*_args):
        entered_connect.set()
        await hold_connect.wait()

    dual = MagicMock()
    dual.connect = AsyncMock(side_effect=connect)
    dual.close = AsyncMock()
    monkeypatch.setattr(media_module, "DualSessionManager", MagicMock(return_value=dual))

    task = asyncio.create_task(bootstrap_inbound_media(str(CALL_ID), TENANT_ID))
    await entered_connect.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    dual.close.assert_awaited_once()
    snapshot = await capacity_manager.snapshot()
    assert snapshot.active == 0
    assert snapshot.reserved == 0


@pytest.mark.asyncio
async def test_caller_disconnect_finishes_pending_dispatch():
    await pending_media_registry.prepare(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        languages=["ko", "en"],
        provider_call_sid="CA-inbound",
    )
    handler = await pending_media_registry.attach(
        str(CALL_ID),
        FakeWebSocket([WebSocketDisconnect()]),
    )
    assert handler is not None
    finish = AsyncMock()
    with patch("src.inbound.service.dispatch_service.finish", finish):
        await handler.run()

    finish.assert_awaited_with(CALL_ID, "twilio_disconnected")
    assert not await pending_media_registry.contains(str(CALL_ID))


@pytest.mark.asyncio
async def test_agent_explicit_end_uses_full_inbound_cleanup_seam(monkeypatch):
    monkeypatch.setattr(settings, "load_test_mode", False)
    call = PendingMediaHandler(FakeWebSocket(), make_pending()).call
    call_manager.register_call(str(CALL_ID), call)
    ws = FakeWebSocket([json.dumps({"type": "end_call", "data": {}})])
    cleanup = AsyncMock()
    auth_context = MagicMock(credential="pickup")

    try:
        with (
            patch(
                "src.routes.stream.authenticate_websocket",
                new=AsyncMock(return_value=(auth_context, "wigvo.pickup")),
            ),
            patch("src.routes.stream.authorize_tenant"),
            patch(
                "src.routes.stream.dispatch_service.is_inbound",
                new=AsyncMock(return_value=True),
            ),
            patch("src.routes.stream.cleanup_inbound_session", cleanup),
        ):
            await app_websocket(ws, str(CALL_ID))
    finally:
        call_manager._calls.pop(str(CALL_ID), None)

    cleanup.assert_awaited_once_with(str(CALL_ID), "user_hangup")
    assert ws.accept_calls == ["wigvo.pickup"]


@pytest.mark.asyncio
async def test_inbound_pending_forces_fixed_direction_and_suppresses_greeting():
    """인바운드는 언어 방향(받는사람=ko/거는사람=en) 고정 + 아웃바운드식
    AI 고지(first message)를 쓰지 않는다. tenant가 반대로 줘도 방향은 고정."""
    reversed_pending = PendingCall(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        languages=("en", "ko"),  # 일부러 뒤집어 전달
        provider_call_sid="CA-inbound",
    )
    handler = PendingMediaHandler(FakeWebSocket(), reversed_pending)

    # 받는사람(상담원/앱/Session A)=한국어=source, 거는사람(외국인/Twilio)=영어=target
    assert handler.call.source_language == "ko"
    assert handler.call.target_language == "en"
    # first_message_sent 선점 → 강제 그리팅·수신자 첫 발화 그리팅·pre-greeting 게이트 무력화
    assert handler.call.first_message_sent is True
