from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class DispatchState(StrEnum):
    RINGING = "RINGING"
    WAITING_FOR_AGENT = "WAITING_FOR_AGENT"
    CLAIMED = "CLAIMED"
    SESSION_STARTING = "SESSION_STARTING"
    CONNECTED = "CONNECTED"
    ENDED = "ENDED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    REJECTED = "REJECTED"


TERMINAL_STATES = frozenset(
    {
        DispatchState.ENDED,
        DispatchState.CANCELLED,
        DispatchState.TIMEOUT,
        DispatchState.REJECTED,
    }
)

ALLOWED_TRANSITIONS: dict[DispatchState, frozenset[DispatchState]] = {
    DispatchState.RINGING: frozenset(
        {DispatchState.WAITING_FOR_AGENT, DispatchState.CANCELLED, DispatchState.REJECTED}
    ),
    DispatchState.WAITING_FOR_AGENT: frozenset(
        {DispatchState.CLAIMED, DispatchState.CANCELLED, DispatchState.TIMEOUT, DispatchState.REJECTED}
    ),
    DispatchState.CLAIMED: frozenset(
        {DispatchState.WAITING_FOR_AGENT, DispatchState.SESSION_STARTING, DispatchState.CANCELLED}
    ),
    DispatchState.SESSION_STARTING: frozenset(
        {DispatchState.CONNECTED, DispatchState.CANCELLED}
    ),
    DispatchState.CONNECTED: frozenset({DispatchState.ENDED}),
    DispatchState.ENDED: frozenset(),
    DispatchState.CANCELLED: frozenset(),
    DispatchState.TIMEOUT: frozenset(),
    DispatchState.REJECTED: frozenset(),
}


class DispatchRecord(BaseModel):
    call_id: UUID
    tenant_id: UUID
    provider_call_sid: str | None = None
    state: DispatchState
    claimed_by: UUID | None = None
    claim_expires_at: datetime | None = None
    connected_at: datetime | None = None
    ended_at: datetime | None = None
    end_reason: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    languages: list[str] = Field(default_factory=list)


class InboundCallListResponse(BaseModel):
    calls: list[DispatchRecord]


class PickupResponse(BaseModel):
    call_id: UUID
    state: DispatchState
    relay_ws_url: str
    pickup_token: str
    role: str = "agent"
    source_language: str = "ko"
    target_language: str = "en"
    communication_mode: str = "voice_to_voice"
    call_mode: str = "relay"
