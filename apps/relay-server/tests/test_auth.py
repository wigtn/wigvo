"""WI-4a institution, user JWT, tenant, and pickup-token contracts."""

import time
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.requests import Request

import src.auth as auth

TENANT_A = UUID("10000000-0000-0000-0000-000000000001")
TENANT_B = UUID("20000000-0000-0000-0000-000000000002")
USER_A = UUID("30000000-0000-0000-0000-000000000003")


def _request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/relay/calls/start",
            "headers": raw_headers,
        }
    )


@pytest.mark.asyncio
async def test_institution_api_key_resolves_tenant(monkeypatch):
    raw_key = "wigvo_test_key_with_enough_entropy"
    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", True)
    monkeypatch.setattr(
        auth.settings,
        "tenant_api_key_hashes",
        {str(TENANT_A): [auth.hash_api_key(raw_key)]},
    )

    context = await auth.authenticate_http_request(
        _request({"X-Wigvo-API-Key": raw_key})
    )

    assert context.verified is True
    assert context.credential == "api_key"
    assert context.tenant_id == TENANT_A


def test_generated_api_key_matches_returned_digest():
    raw_key, digest = auth.generate_api_key()
    assert raw_key.startswith("wigvo_")
    assert len(digest) == 64
    assert auth.hash_api_key(raw_key) == digest


@pytest.mark.asyncio
async def test_missing_credential_observe_then_enforce(monkeypatch):
    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", False)
    observed = await auth.authenticate_http_request(_request())
    assert observed.verified is False
    assert auth.authorize_tenant(observed, TENANT_A) == TENANT_A

    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", True)
    with pytest.raises(auth.AuthError) as exc_info:
        await auth.authenticate_http_request(_request())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_wigtn_sso_es256_jwt_resolves_user_membership(monkeypatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    issuer = "https://sso.example.supabase.co/auth/v1"
    token = jwt.encode(
        {
            "sub": str(USER_A),
            "role": "authenticated",
            "iss": issuer,
            "aud": "authenticated",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )

    class _SigningKey:
        key = private_key.public_key()

    class _JwksClient:
        def get_signing_key_from_jwt(self, _token: str):
            return _SigningKey()

    async def fake_membership(user_id: UUID) -> UUID | None:
        assert user_id == USER_A
        return TENANT_A

    monkeypatch.setattr(auth.settings, "supabase_url", "https://sso.example.supabase.co")
    monkeypatch.setattr(auth, "_get_jwks_client", lambda _url: _JwksClient())
    monkeypatch.setattr(auth, "get_user_tenant_id", fake_membership)

    context = await auth._verify_user_jwt(token)

    assert context.credential == "user_jwt"
    assert context.user_id == USER_A
    assert context.tenant_id == TENANT_A


def test_cross_tenant_access_is_denied_when_enforced(monkeypatch):
    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", True)
    context = auth.AuthContext(
        verified=True,
        credential="api_key",
        tenant_id=TENANT_A,
    )

    with pytest.raises(auth.AuthError) as exc_info:
        auth.authorize_tenant(context, TENANT_B)
    assert exc_info.value.status_code == 403


def test_verified_user_can_supply_missing_tenant(monkeypatch):
    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", True)
    context = auth.AuthContext(
        verified=True,
        credential="user_jwt",
        tenant_id=TENANT_A,
        user_id=USER_A,
    )
    assert auth.authorize_tenant(context, None) == TENANT_A


def test_pickup_token_round_trip_has_required_claims(monkeypatch):
    monkeypatch.setattr(
        auth.settings,
        "pickup_token_secret",
        "test-only-secret-that-is-longer-than-32-bytes",
    )
    token = auth.issue_pickup_token(
        call_id="call-123",
        tenant_id=TENANT_A,
        user_id=USER_A,
        role="agent",
        ttl_s=60,
    )

    claims = auth.verify_pickup_token(token)

    assert claims.call_id == "call-123"
    assert claims.tenant_id == TENANT_A
    assert claims.user_id == USER_A
    assert claims.role == "agent"
    assert claims.expires_at > 0
    assert claims.token_id


def test_pickup_token_is_bound_to_configured_secret(monkeypatch):
    monkeypatch.setattr(
        auth.settings,
        "pickup_token_secret",
        "first-test-secret-that-is-longer-than-32-bytes",
    )
    token = auth.issue_pickup_token(
        call_id="call-123",
        tenant_id=TENANT_A,
        user_id=USER_A,
        role="agent",
        ttl_s=60,
    )
    monkeypatch.setattr(
        auth.settings,
        "pickup_token_secret",
        "second-test-secret-that-is-longer-than-32-bytes",
    )

    with pytest.raises(auth.AuthError) as exc_info:
        auth.verify_pickup_token(token)
    assert exc_info.value.status_code == 401


def test_auth_path_whitelists_keep_twilio_separate():
    assert "/relay/calls/start" in auth.INSTITUTION_AUTH_HTTP_PATHS
    assert "/relay/calls/{call_id}/monitor" in auth.USER_AUTH_WEBSOCKET_PATHS
    assert "/twilio/media-stream/" in auth.TWILIO_AUTH_EXEMPT_PATH_PREFIXES
    assert all(
        not path.startswith("/twilio")
        for path in auth.INSTITUTION_AUTH_HTTP_PATHS
        | auth.USER_AUTH_WEBSOCKET_PATHS
    )
