"""WI-4a authentication boundaries for HTTP and WebSocket entry points.

Institution API keys are server-to-server credentials. Browser/mobile clients
authenticate with a WIGTN-SSO Supabase JWT. WI-6 pickup tokens share this module
but are not accepted by a call WebSocket until dispatch-state revalidation is
wired by WI-6.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal
from uuid import UUID, uuid4

import jwt
from fastapi import Request, WebSocket
from jwt import InvalidTokenError, PyJWKClient

from src.config import settings
from src.db.pg_client import get_user_tenant_id

logger = logging.getLogger(__name__)

INSTITUTION_AUTH_HTTP_PATHS = frozenset(
    {"/relay/calls/start", "/relay/calls/{call_id}/end"}
)
USER_AUTH_HTTP_PATHS = frozenset(
    {
        "/relay/inbound/calls",
        "/relay/inbound/calls/{call_id}/pickup",
    }
)
USER_AUTH_WEBSOCKET_PATHS = frozenset(
    {"/relay/calls/{call_id}/stream", "/relay/calls/{call_id}/monitor"}
)
TWILIO_AUTH_EXEMPT_PATH_PREFIXES = frozenset(
    {
        "/twilio/webhook/",
        "/twilio/status-callback/",
        "/twilio/incoming",
        "/twilio/media-stream/",
    }
)

JWT_WS_PROTOCOL = "wigvo.jwt"
PICKUP_WS_PROTOCOL = "wigvo.pickup"
PICKUP_ISSUER = "wigvo-relay"
PICKUP_AUDIENCE = "wigvo-pickup"


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class AuthContext:
    verified: bool
    credential: Literal["api_key", "user_jwt", "pickup", "observe"]
    tenant_id: UUID | None = None
    user_id: UUID | None = None


@dataclass(frozen=True)
class PickupTokenClaims:
    call_id: str
    tenant_id: UUID
    user_id: UUID
    role: str
    expires_at: int
    token_id: str


def hash_api_key(api_key: str) -> str:
    """Return the digest operators place in TENANT_API_KEY_HASHES."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Generate a high-entropy institution key and its storable digest."""
    api_key = f"wigvo_{secrets.token_urlsafe(32)}"
    return api_key, hash_api_key(api_key)


def _resolve_api_key_tenant(api_key: str) -> UUID | None:
    candidate = hash_api_key(api_key)
    for raw_tenant_id, configured_hashes in settings.tenant_api_key_hashes.items():
        try:
            tenant_id = UUID(raw_tenant_id)
        except ValueError:
            logger.error("Ignoring invalid tenant UUID in TENANT_API_KEY_HASHES")
            continue
        if any(
            hmac.compare_digest(candidate, configured.lower())
            for configured in configured_hashes
        ):
            return tenant_id
    return None


def _observe_or_raise(status_code: int, detail: str, *, reason: str) -> AuthContext:
    if settings.tenant_auth_enforce:
        raise AuthError(status_code, detail)
    logger.warning("Auth observe-only pass: %s", reason)
    return AuthContext(verified=False, credential="observe")


def _supabase_issuer() -> str:
    if not settings.supabase_url:
        raise AuthError(401, "User authentication is not configured")
    return f"{settings.supabase_url.rstrip('/')}/auth/v1"


@lru_cache(maxsize=4)
def _get_jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_keys=True, lifespan=300)


async def _verify_user_jwt(token: str) -> AuthContext:
    issuer = _supabase_issuer()
    jwks_url = f"{issuer}/.well-known/jwks.json"

    try:
        signing_key = await asyncio.to_thread(
            _get_jwks_client(jwks_url).get_signing_key_from_jwt,
            token,
        )
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience=settings.supabase_jwt_audience,
            issuer=issuer,
            options={"require": ["sub", "exp", "iss", "aud"]},
        )
        if claims.get("role") != "authenticated":
            raise AuthError(401, "Authenticated user JWT required")
        user_id = UUID(str(claims["sub"]))
    except AuthError:
        raise
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise AuthError(401, "Invalid user token") from exc
    except Exception as exc:
        logger.warning("WIGTN-SSO JWT verification failed: %s", type(exc).__name__)
        raise AuthError(401, "Unable to verify user token") from exc

    tenant_id = await get_user_tenant_id(user_id)
    if tenant_id is None:
        raise AuthError(403, "User has no active WIGVO tenant membership")
    return AuthContext(
        verified=True,
        credential="user_jwt",
        tenant_id=tenant_id,
        user_id=user_id,
    )


async def authenticate_http_request(request: Request) -> AuthContext:
    api_key = request.headers.get("x-wigvo-api-key")
    if api_key:
        tenant_id = _resolve_api_key_tenant(api_key)
        if tenant_id is not None:
            return AuthContext(
                verified=True,
                credential="api_key",
                tenant_id=tenant_id,
            )
        return _observe_or_raise(401, "Invalid institution API key", reason="invalid_api_key")

    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        try:
            return await _verify_user_jwt(token)
        except AuthError as exc:
            return _observe_or_raise(
                exc.status_code,
                exc.detail,
                reason=f"user_jwt_{exc.status_code}",
            )

    return _observe_or_raise(401, "Authentication required", reason="missing_credential")


def authorize_tenant(context: AuthContext, requested_tenant_id: UUID | None) -> UUID:
    if requested_tenant_id is None:
        if context.verified and context.tenant_id is not None:
            return context.tenant_id
        raise AuthError(403, "Tenant could not be resolved")

    if not context.verified:
        return requested_tenant_id
    if context.tenant_id == requested_tenant_id:
        return requested_tenant_id
    if settings.tenant_auth_enforce:
        raise AuthError(403, "Cross-tenant access denied")
    logger.warning("Auth observe-only pass: tenant_mismatch")
    return requested_tenant_id


def _extract_ws_token(ws: WebSocket) -> tuple[str | None, str | None]:
    offered = [
        item.strip()
        for item in ws.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    ]
    for marker in (JWT_WS_PROTOCOL, PICKUP_WS_PROTOCOL):
        if marker in offered:
            index = offered.index(marker)
            token = offered[index + 1] if index + 1 < len(offered) else None
            return marker, token
    return None, None


async def authenticate_websocket(ws: WebSocket) -> tuple[AuthContext, str | None]:
    marker, token = _extract_ws_token(ws)
    if marker == JWT_WS_PROTOCOL and token:
        try:
            return await _verify_user_jwt(token), JWT_WS_PROTOCOL
        except AuthError as exc:
            return (
                _observe_or_raise(
                    exc.status_code,
                    exc.detail,
                    reason=f"ws_user_jwt_{exc.status_code}",
                ),
                None,
            )
    if marker == PICKUP_WS_PROTOCOL and token:
        claims = verify_pickup_token(token)
        path_call_id = str(ws.path_params.get("call_id", ""))
        if claims.call_id != path_call_id:
            raise AuthError(403, "Pickup token is bound to another call")
        if claims.role != "agent":
            raise AuthError(403, "Pickup role is not permitted")

        from src.inbound.service import dispatch_service

        try:
            call_uuid = UUID(claims.call_id)
        except ValueError as exc:
            raise AuthError(401, "Invalid pickup call identifier") from exc
        if not await dispatch_service.authorize_pickup(
            call_id=call_uuid,
            tenant_id=claims.tenant_id,
            user_id=claims.user_id,
        ):
            raise AuthError(403, "Pickup claim is no longer active")
        return (
            AuthContext(
                verified=True,
                credential="pickup",
                tenant_id=claims.tenant_id,
                user_id=claims.user_id,
            ),
            PICKUP_WS_PROTOCOL,
        )
    if marker == PICKUP_WS_PROTOCOL:
        raise AuthError(401, "Pickup token is required")
    return (
        _observe_or_raise(401, "WebSocket authentication required", reason="ws_missing_credential"),
        None,
    )


async def reject_websocket(ws: WebSocket, error: AuthError) -> None:
    code = 4403 if error.status_code == 403 else 4401
    await ws.close(code=code, reason=error.detail)


def issue_pickup_token(
    *,
    call_id: str,
    tenant_id: UUID,
    user_id: UUID,
    role: str,
    ttl_s: int | None = None,
) -> str:
    if not settings.pickup_token_secret:
        raise RuntimeError("PICKUP_TOKEN_SECRET is not configured")
    ttl = ttl_s if ttl_s is not None else settings.pickup_token_ttl_s
    if not 60 <= ttl <= 300:
        raise ValueError("Pickup token TTL must be between 60 and 300 seconds")
    if not role:
        raise ValueError("Pickup role is required")

    now = int(time.time())
    payload = {
        "iss": PICKUP_ISSUER,
        "aud": PICKUP_AUDIENCE,
        "iat": now,
        "exp": now + ttl,
        "jti": str(uuid4()),
        "call_id": call_id,
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "role": role,
    }
    return jwt.encode(payload, settings.pickup_token_secret, algorithm="HS256")


def verify_pickup_token(token: str) -> PickupTokenClaims:
    if not settings.pickup_token_secret:
        raise AuthError(401, "Pickup token verification is not configured")
    try:
        claims = jwt.decode(
            token,
            settings.pickup_token_secret,
            algorithms=["HS256"],
            audience=PICKUP_AUDIENCE,
            issuer=PICKUP_ISSUER,
            options={
                "require": [
                    "call_id",
                    "tenant_id",
                    "user_id",
                    "role",
                    "exp",
                    "iat",
                    "jti",
                ]
            },
        )
        return PickupTokenClaims(
            call_id=str(claims["call_id"]),
            tenant_id=UUID(str(claims["tenant_id"])),
            user_id=UUID(str(claims["user_id"])),
            role=str(claims["role"]),
            expires_at=int(claims["exp"]),
            token_id=str(claims["jti"]),
        )
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise AuthError(401, "Invalid or expired pickup token") from exc
