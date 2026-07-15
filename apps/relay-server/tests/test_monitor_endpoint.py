"""관전(monitor) WebSocket 엔드포인트 통합 테스트.

라우트가 앱에 실제 등록됐는지 + 연결 시점 동작(call-not-found, 상태 스냅샷)을
in-process TestClient로 검증한다. broadcast/cleanup 로직은 test_call_manager.py가 덮는다.
"""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.auth as auth
from src.call_manager import call_manager
from src.main import app
from src.types import ActiveCall, CallMode, CallStatus, CommunicationMode

TENANT_A = UUID("10000000-0000-0000-0000-000000000001")
TENANT_B = UUID("20000000-0000-0000-0000-000000000002")
USER_A = UUID("30000000-0000-0000-0000-000000000003")


def test_monitor_unknown_call_returns_error() -> None:
    """등록되지 않은 call_id로 관전 연결 시 ERROR를 받고 닫힌다."""
    with TestClient(app) as client:
        with client.websocket_connect("/relay/calls/nope/monitor") as ws:
            msg = ws.receive_json()

    assert msg["type"] == "error"
    assert "not found" in msg["data"]["message"].lower()


def test_monitor_existing_call_receives_status_snapshot() -> None:
    """진행 중인 통화에 관전 연결 시 현재 상태 스냅샷(언어쌍/모드)을 받는다."""
    call = ActiveCall(
        call_id="mon-001",
        call_sid="CA_mon",
        mode=CallMode.RELAY,
        source_language="ko",
        target_language="en",
        communication_mode=CommunicationMode.VOICE_TO_VOICE,
        status=CallStatus.CONNECTED,
    )
    call_manager.register_call("mon-001", call)
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/relay/calls/mon-001/monitor") as ws:
                msg = ws.receive_json()

                assert msg["type"] == "call_status"
                # router가 없으므로 'waiting' 스냅샷
                assert msg["data"]["status"] == "waiting"
                assert msg["data"]["source_language"] == "ko"
                assert msg["data"]["target_language"] == "en"
                assert msg["data"]["communication_mode"] == "voice_to_voice"

            # 관전 연결이 끊겨도 통화는 살아있다 (격리; cleanup 미트리거)
            assert call_manager.get_call("mon-001") is call

            # lifespan shutdown(shutdown_all)이 DB 없이 persist 시도하지 않도록 먼저 제거
            call_manager._calls.pop("mon-001", None)
    finally:
        call_manager._calls.pop("mon-001", None)
        call_manager._cleanup_locks.pop("mon-001", None)


@pytest.mark.parametrize("endpoint", ["stream", "monitor"])
def test_call_websockets_reject_missing_auth_when_enforced(
    monkeypatch, endpoint: str
) -> None:
    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", True)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/relay/calls/nope/{endpoint}"):
                pass
    assert exc_info.value.code == 4401


@pytest.mark.parametrize("endpoint", ["stream", "monitor"])
def test_call_websockets_reject_cross_tenant_user(
    monkeypatch, endpoint: str
) -> None:
    call = ActiveCall(call_id=f"cross-{endpoint}", tenant_id=TENANT_A)
    call_manager.register_call(call.call_id, call)

    async def fake_verify_user_jwt(_token: str) -> auth.AuthContext:
        return auth.AuthContext(
            verified=True,
            credential="user_jwt",
            tenant_id=TENANT_B,
            user_id=USER_A,
        )

    monkeypatch.setattr(auth.settings, "tenant_auth_enforce", True)
    monkeypatch.setattr(auth, "_verify_user_jwt", fake_verify_user_jwt)
    try:
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(
                    f"/relay/calls/{call.call_id}/{endpoint}",
                    subprotocols=[auth.JWT_WS_PROTOCOL, "test-token"],
                ):
                    pass
        assert exc_info.value.code == 4403
    finally:
        call_manager._calls.pop(call.call_id, None)
        call_manager._cleanup_locks.pop(call.call_id, None)
