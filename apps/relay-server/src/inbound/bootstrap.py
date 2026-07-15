"""A/B integration contract for WI-6 inbound session creation.

Developer A registers the media implementation during application startup.
Developer B calls it only after tenant authorization and atomic dispatch claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol
from uuid import UUID


@dataclass(frozen=True)
class BootstrapResult:
    relay_ws_url: str
    source_language: str
    target_language: str
    role: str = "agent"
    communication_mode: str = "voice_to_voice"
    call_mode: str = "relay"


class BootstrapInboundSession(Protocol):
    async def __call__(self, call_id: str, tenant_id: UUID) -> BootstrapResult: ...


class CleanupInboundSession(Protocol):
    async def __call__(self, call_id: str, reason: str) -> None: ...


_bootstrapper: BootstrapInboundSession | None = None
_cleanup: CleanupInboundSession | None = None


def register_inbound_media_handlers(
    *,
    bootstrap: BootstrapInboundSession,
    cleanup: CleanupInboundSession,
) -> None:
    """Register A-owned media/session handlers exactly once per process."""
    global _bootstrapper, _cleanup
    _bootstrapper = bootstrap
    _cleanup = cleanup


def media_handlers_registered() -> bool:
    return _bootstrapper is not None and _cleanup is not None


async def bootstrap_inbound_session(call_id: str, tenant_id: UUID) -> BootstrapResult:
    if _bootstrapper is None:
        raise RuntimeError("Inbound media bootstrap is not registered")
    return await _bootstrapper(call_id, tenant_id)


async def cleanup_inbound_session(call_id: str, reason: str) -> None:
    if _cleanup is not None:
        await _cleanup(call_id, reason)
