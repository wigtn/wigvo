"""Twilio HTTP callback and Media Stream handshake signature validation."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, Request, WebSocket
from starlette.datastructures import FormData
from twilio.request_validator import RequestValidator

from src.config import settings

logger = logging.getLogger(__name__)


def public_callback_base_url() -> str:
    """서명 계산과 callback 생성이 공유하는 외부 origin을 반환한다."""
    raw = (settings.public_callback_base_url or settings.relay_server_url).strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("PUBLIC_CALLBACK_BASE_URL must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise RuntimeError("PUBLIC_CALLBACK_BASE_URL cannot contain query or fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def public_http_url(path: str, query: str = "") -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{public_callback_base_url()}{normalized_path}" + (
        f"?{query}" if query else ""
    )


def public_websocket_url(path: str, query: str = "") -> str:
    http_url = public_http_url(path, query)
    if http_url.startswith("https://"):
        return f"wss://{http_url[len('https://') :]}"
    return f"ws://{http_url[len('http://') :]}"


def _validator() -> RequestValidator:
    if not settings.twilio_auth_token:
        raise RuntimeError("TWILIO_AUTH_TOKEN is required for signature validation")
    return RequestValidator(settings.twilio_auth_token)


async def validate_twilio_http_request(request: Request) -> FormData:
    """검증된 전체 form을 반환한다. 실패 시 상태 변경 전에 403/503."""
    try:
        validator = _validator()
        url = public_http_url(
            request.url.path,
            request.scope.get("query_string", b"").decode("latin-1"),
        )
    except RuntimeError as exc:
        logger.error("Twilio signature validation misconfigured: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Twilio signature validation is not configured",
        ) from exc

    form = await request.form()
    signature = request.headers.get("x-twilio-signature", "")
    if not signature or not validator.validate(url, form, signature):
        logger.warning("Rejected invalid Twilio HTTP signature path=%s", request.url.path)
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    return form


async def validate_twilio_websocket(ws: WebSocket) -> bool:
    """Media Stream upgrade를 accept하기 전에 검증한다.

    Twilio voice WSS는 환경에 따라 URL 끝 slash를 포함해 서명하는 사례가 있어
    공식 troubleshooting 지침대로 exact URL과 trailing-slash URL을 모두 시도한다.
    """
    if getattr(settings, "load_test_mode", False):
        return True

    try:
        validator = _validator()
        query = ws.scope.get("query_string", b"").decode("latin-1")
        url = public_websocket_url(ws.scope["path"], query)
    except (KeyError, RuntimeError) as exc:
        logger.error("Twilio WebSocket signature validation misconfigured: %s", exc)
        await ws.close(code=1011, reason="Twilio validation is not configured")
        return False

    signature = ws.headers.get("x-twilio-signature", "")
    candidates = [url]
    if not url.endswith("/"):
        candidates.append(f"{url}/")
    valid = bool(signature) and any(
        validator.validate(candidate, ws.query_params, signature)
        for candidate in candidates
    )
    if not valid:
        logger.warning("Rejected invalid Twilio WebSocket signature path=%s", ws.scope["path"])
        await ws.close(code=4403, reason="Invalid Twilio signature")
        return False
    return True
