"""FastAPI route-level WI-4a authentication behavior."""

from uuid import UUID

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

import src.auth as auth
from src.call_manager import call_manager
from src.main import app
from src.types import ActiveCall

TENANT_A = UUID("10000000-0000-0000-0000-000000000001")
TENANT_B = UUID("20000000-0000-0000-0000-000000000002")


def _valid_start_body() -> dict[str, str]:
    return {
        "call_id": "40000000-0000-0000-0000-000000000004",
        "tenant_id": str(TENANT_A),
        "phone_number": "+821012345678",
        "source_language": "ko",
        "target_language": "en",
    }


def test_call_start_rejects_before_allocating_resources(monkeypatch) -> None:
    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", True)
    with TestClient(app) as client:
        response = client.post("/relay/calls/start", json=_valid_start_body())

    assert response.status_code == 401
    assert call_manager.get_call(_valid_start_body()["call_id"]) is None


def test_call_end_rejects_cross_tenant_api_key(monkeypatch) -> None:
    raw_key = "wigvo_tenant_b_test_key"
    call = ActiveCall(call_id="call-tenant-a", tenant_id=TENANT_A)
    call_manager.register_call(call.call_id, call)
    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", True)
    monkeypatch.setattr(
        auth.settings,
        "tenant_api_key_hashes",
        {str(TENANT_B): [auth.hash_api_key(raw_key)]},
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/relay/calls/{call.call_id}/end",
                headers={"X-Wigvo-API-Key": raw_key},
                json={"call_id": call.call_id, "reason": "test"},
            )
            assert response.status_code == 403
            assert call_manager.get_call(call.call_id) is call
    finally:
        call_manager._calls.pop(call.call_id, None)
        call_manager._cleanup_locks.pop(call.call_id, None)


def test_twilio_webhook_is_not_gated_by_institution_auth(monkeypatch) -> None:
    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", True)
    monkeypatch.setattr(auth.settings, "twilio_auth_token", "twilio-test-token")
    monkeypatch.setattr(
        auth.settings,
        "public_callback_base_url",
        "https://relay.example.com",
    )
    signature = RequestValidator("twilio-test-token").compute_signature(
        "https://relay.example.com/twilio/webhook/twilio-test",
        {},
    )
    with TestClient(app) as client:
        response = client.post(
            "/twilio/webhook/twilio-test",
            headers={"X-Twilio-Signature": signature},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")


def test_health_exposes_safe_auth_rollout_state(monkeypatch) -> None:
    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", False)
    monkeypatch.setattr(
        auth.settings,
        "tenant_api_key_hashes",
        {str(TENANT_A): ["a" * 64]},
    )
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant_auth_enforced"] is False
    assert payload["tenant_api_key_tenants"] == 1
    assert "active_sessions" in payload
