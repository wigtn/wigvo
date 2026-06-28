"""CallManager 중앙 정리 로직 테스트."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.call_manager import CallManager
from src.types import ActiveCall, CallMode, CallStatus


@pytest.fixture
def cm() -> CallManager:
    """매 테스트마다 새 CallManager 인스턴스."""
    return CallManager()


@pytest.fixture
def sample_call() -> ActiveCall:
    return ActiveCall(
        call_id="test-001",
        call_sid="CA_test",
        mode=CallMode.RELAY,
        source_language="en",
        target_language="ko",
        status=CallStatus.CONNECTED,
    )


@pytest.fixture
def mock_dual_session() -> AsyncMock:
    session = AsyncMock()
    session.close = AsyncMock()
    return session


@pytest.fixture
def mock_router() -> AsyncMock:
    router = AsyncMock()
    router.stop = AsyncMock()
    return router


@pytest.fixture
def mock_app_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


class TestRegisterAndGet:
    def test_register_and_get_call(self, cm: CallManager, sample_call: ActiveCall):
        cm.register_call("test-001", sample_call)
        assert cm.get_call("test-001") is sample_call
        assert cm.get_call("nonexistent") is None

    def test_register_and_get_session(self, cm: CallManager, mock_dual_session: AsyncMock):
        cm.register_session("test-001", mock_dual_session)
        assert cm.get_session("test-001") is mock_dual_session
        assert cm.get_session("nonexistent") is None

    def test_register_and_get_router(self, cm: CallManager, mock_router: AsyncMock):
        cm.register_router("test-001", mock_router)
        assert cm.get_router("test-001") is mock_router

    def test_register_and_get_app_ws(self, cm: CallManager, mock_app_ws: AsyncMock):
        cm.register_app_ws("test-001", mock_app_ws)
        assert cm.get_app_ws("test-001") is mock_app_ws

    def test_active_call_count(self, cm: CallManager, sample_call: ActiveCall):
        assert cm.active_call_count == 0
        cm.register_call("test-001", sample_call)
        assert cm.active_call_count == 1


@pytest.mark.asyncio
class TestCleanupCall:
    async def test_cleanup_stops_router(
        self,
        cm: CallManager,
        sample_call: ActiveCall,
        mock_router: AsyncMock,
    ):
        cm.register_call("test-001", sample_call)
        cm.register_router("test-001", mock_router)

        with patch("src.db.pg_client.persist_call", new_callable=AsyncMock):
            await cm.cleanup_call("test-001", reason="test")

        mock_router.stop.assert_awaited_once()
        assert cm.get_router("test-001") is None

    async def test_cleanup_closes_session(
        self,
        cm: CallManager,
        sample_call: ActiveCall,
        mock_dual_session: AsyncMock,
    ):
        cm.register_call("test-001", sample_call)
        cm.register_session("test-001", mock_dual_session)

        with patch("src.db.pg_client.persist_call", new_callable=AsyncMock):
            await cm.cleanup_call("test-001", reason="test")

        mock_dual_session.close.assert_awaited_once()
        assert cm.get_session("test-001") is None

    async def test_cleanup_cancels_listen_task(self, cm: CallManager, sample_call: ActiveCall):
        cm.register_call("test-001", sample_call)

        # 완료되지 않는 태스크 시뮬레이션
        async def long_running():
            await asyncio.sleep(100)

        task = asyncio.create_task(long_running())
        cm.register_listen_task("test-001", task)

        with patch("src.db.pg_client.persist_call", new_callable=AsyncMock):
            await cm.cleanup_call("test-001", reason="test")

        assert task.cancelled()

    async def test_cleanup_notifies_and_closes_app_ws(
        self,
        cm: CallManager,
        sample_call: ActiveCall,
        mock_app_ws: AsyncMock,
    ):
        cm.register_call("test-001", sample_call)
        cm.register_app_ws("test-001", mock_app_ws)

        with patch("src.db.pg_client.persist_call", new_callable=AsyncMock):
            await cm.cleanup_call("test-001", reason="test")

        mock_app_ws.send_json.assert_awaited_once()
        sent_data = mock_app_ws.send_json.call_args[0][0]
        assert sent_data["data"]["status"] == "ended"
        assert sent_data["data"]["reason"] == "test"
        mock_app_ws.close.assert_awaited_once()

    async def test_cleanup_persists_call(self, cm: CallManager, sample_call: ActiveCall):
        cm.register_call("test-001", sample_call)

        with patch("src.db.pg_client.persist_call", new_callable=AsyncMock) as mock_persist:
            await cm.cleanup_call("test-001", reason="test")

        mock_persist.assert_awaited_once()
        persisted_call = mock_persist.call_args[0][0]
        assert persisted_call.status == CallStatus.ENDED

    async def test_cleanup_idempotent(
        self,
        cm: CallManager,
        sample_call: ActiveCall,
        mock_router: AsyncMock,
        mock_dual_session: AsyncMock,
    ):
        """cleanup_call을 2회 호출해도 안전하다."""
        cm.register_call("test-001", sample_call)
        cm.register_router("test-001", mock_router)
        cm.register_session("test-001", mock_dual_session)

        with patch("src.db.pg_client.persist_call", new_callable=AsyncMock):
            await cm.cleanup_call("test-001", reason="first")
            await cm.cleanup_call("test-001", reason="second")

        # 한 번만 호출됨
        mock_router.stop.assert_awaited_once()
        mock_dual_session.close.assert_awaited_once()
        assert cm.active_call_count == 0

    async def test_cleanup_nonexistent_call(self, cm: CallManager):
        """존재하지 않는 call_id로 cleanup해도 에러 없음."""
        await cm.cleanup_call("nonexistent", reason="test")


@pytest.mark.asyncio
class TestSendToApp:
    async def test_send_to_app_with_ws(self, cm: CallManager, mock_app_ws: AsyncMock):
        from src.types import WsMessage, WsMessageType

        cm.register_app_ws("test-001", mock_app_ws)
        msg = WsMessage(type=WsMessageType.CALL_STATUS, data={"status": "test"})
        await cm.send_to_app("test-001", msg)

        mock_app_ws.send_json.assert_awaited_once()

    async def test_send_to_app_without_ws(self, cm: CallManager):
        """App WS가 없으면 조용히 무시."""
        from src.types import WsMessage, WsMessageType

        msg = WsMessage(type=WsMessageType.CALL_STATUS, data={"status": "test"})
        await cm.send_to_app("nonexistent", msg)  # 에러 없음


@pytest.mark.asyncio
class TestShutdownAll:
    async def test_shutdown_all(self, cm: CallManager):
        call1 = ActiveCall(call_id="c1", status=CallStatus.CONNECTED)
        call2 = ActiveCall(call_id="c2", status=CallStatus.CONNECTED)
        cm.register_call("c1", call1)
        cm.register_call("c2", call2)

        with patch("src.db.pg_client.persist_call", new_callable=AsyncMock):
            await cm.shutdown_all()

        assert cm.active_call_count == 0
        assert cm.get_call("c1") is None
        assert cm.get_call("c2") is None
