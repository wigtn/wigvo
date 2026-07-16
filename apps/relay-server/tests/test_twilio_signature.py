"""WI-4b Twilio HTTP callback and Media Stream signature tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import QueryParams
from twilio.request_validator import RequestValidator

from src.config import settings
from src.main import app
from src.twilio.outbound import make_call
from src.twilio.signature import (
    public_http_url,
    public_websocket_url,
    validate_twilio_websocket,
)

AUTH_TOKEN = "twilio-test-auth-token"
PUBLIC_BASE = "https://relay.example.com"


@pytest.fixture(autouse=True)
def configure_twilio_signature(monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)
    monkeypatch.setattr(settings, "public_callback_base_url", PUBLIC_BASE)


def _signature(path: str, params: dict[str, str] | None = None) -> str:
    return RequestValidator(AUTH_TOKEN).compute_signature(
        f"{PUBLIC_BASE}{path}",
        params or {},
    )


def test_valid_webhook_signature_returns_public_wss_twiml():
    path = "/twilio/webhook/call-123"
    with TestClient(app) as client:
        response = client.post(
            path,
            headers={"X-Twilio-Signature": _signature(path)},
        )

    assert response.status_code == 200
    assert "wss://relay.example.com/twilio/media-stream/call-123" in response.text


def test_invalid_webhook_signature_is_rejected_before_twiml():
    with TestClient(app) as client:
        response = client.post(
            "/twilio/webhook/call-123",
            headers={"X-Twilio-Signature": "invalid"},
        )

    assert response.status_code == 403


def test_missing_auth_token_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    with TestClient(app) as client:
        response = client.post("/twilio/webhook/call-123")

    assert response.status_code == 503


def test_valid_terminal_status_callback_reaches_single_cleanup_path():
    path = "/twilio/status-callback/call-123"
    form = {
        "CallStatus": "completed",
        "CallSid": "CA123",
        "CallDuration": "7",
    }
    with (
        patch(
            "src.routes.twilio_webhook.call_manager.cleanup_call",
            new_callable=AsyncMock,
        ) as cleanup,
        TestClient(app) as client,
    ):
        response = client.post(
            path,
            data=form,
            headers={"X-Twilio-Signature": _signature(path, form)},
        )

    assert response.status_code == 200
    cleanup.assert_awaited_once_with("call-123", reason="twilio_completed")


def test_invalid_status_callback_cannot_mutate_call_state():
    with (
        patch(
            "src.routes.twilio_webhook.call_manager.cleanup_call",
            new_callable=AsyncMock,
        ) as cleanup,
        TestClient(app) as client,
    ):
        response = client.post(
            "/twilio/status-callback/call-123",
            data={"CallStatus": "completed"},
            headers={"X-Twilio-Signature": "invalid"},
        )

    assert response.status_code == 403
    cleanup.assert_not_awaited()


def test_incoming_signature_boundary_rejects_call_without_allocating_session():
    path = "/twilio/incoming"
    form = {"CallSid": "CA-inbound", "To": "+12025550100"}
    with TestClient(app) as client:
        response = client.post(
            path,
            data=form,
            headers={"X-Twilio-Signature": _signature(path, form)},
        )

    assert response.status_code == 200
    assert '<Reject reason="busy"' in response.text


def _websocket(signature: str) -> MagicMock:
    ws = MagicMock()
    ws.scope = {
        "path": "/twilio/media-stream/call-123",
        "query_string": b"",
    }
    ws.headers = {"x-twilio-signature": signature}
    ws.query_params = QueryParams("")
    ws.close = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_valid_media_stream_handshake_signature_passes():
    signature = RequestValidator(AUTH_TOKEN).compute_signature(
        "wss://relay.example.com/twilio/media-stream/call-123",
        {},
    )
    ws = _websocket(signature)

    assert await validate_twilio_websocket(ws) is True
    ws.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_trailing_slash_media_stream_signature_passes():
    signature = RequestValidator(AUTH_TOKEN).compute_signature(
        "wss://relay.example.com/twilio/media-stream/call-123/",
        {},
    )
    ws = _websocket(signature)

    assert await validate_twilio_websocket(ws) is True


@pytest.mark.asyncio
async def test_invalid_media_stream_signature_is_denied_before_accept():
    ws = _websocket("invalid")

    assert await validate_twilio_websocket(ws) is False
    ws.close.assert_awaited_once_with(code=4403, reason="Invalid Twilio signature")


def test_callback_url_builders_share_one_public_base():
    assert public_http_url("/twilio/incoming") == (
        "https://relay.example.com/twilio/incoming"
    )
    assert public_websocket_url("/twilio/media-stream/call-123") == (
        "wss://relay.example.com/twilio/media-stream/call-123"
    )


def test_outbound_call_registers_the_same_public_callback_urls():
    client = MagicMock()
    client.calls.create.return_value = SimpleNamespace(sid="CA-created")
    with patch("src.twilio.outbound.get_twilio_client", return_value=client):
        sid = make_call(
            phone_number="+12025550123",
            call_id="call-123",
            outbound_number="+12025550100",
        )

    assert sid == "CA-created"
    kwargs = client.calls.create.call_args.kwargs
    assert kwargs["url"] == "https://relay.example.com/twilio/webhook/call-123"
    assert kwargs["status_callback"] == (
        "https://relay.example.com/twilio/status-callback/call-123"
    )
