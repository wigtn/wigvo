"""중앙 집중화된 통화 라이프사이클 관리.

모든 통화 상태(active_calls, sessions, routers, app_ws)를 하나의 싱글톤에서 관리하고,
idempotent cleanup_call()로 모든 정리를 처리한다.

핵심 문제 해결:
  - 리소스 누수: 분산된 정리 로직 → 하나의 cleanup_call()
  - Race condition: asyncio.Lock으로 동시 호출 방지
  - 수신자 전화 끊기 미감지: Twilio status-callback에서 cleanup_call() 호출
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from src.observability import tracer
from src.types import ActiveCall, CallStatus, WsMessage, WsMessageType

if TYPE_CHECKING:
    from fastapi import WebSocket

    from src.realtime.audio_router import AudioRouter
    from src.realtime.sessions.session_manager import DualSessionManager

logger = logging.getLogger(__name__)


class CallManager:
    """통화 라이프사이클을 중앙에서 관리하는 싱글톤."""

    def __init__(self) -> None:
        self._calls: dict[str, ActiveCall] = {}
        self._sessions: dict[str, "DualSessionManager"] = {}
        self._routers: dict[str, "AudioRouter"] = {}
        self._app_ws: dict[str, "WebSocket"] = {}
        # 관전(read-only) WS: 통화당 N명. 발신자 소켓(_app_ws)과 분리하여
        # 관전자 연결/해제가 통화 생명주기(cleanup_call)에 영향을 주지 않게 한다.
        self._observers: dict[str, set["WebSocket"]] = {}
        self._listen_tasks: dict[str, asyncio.Task] = {}
        self._cleanup_locks: dict[str, asyncio.Lock] = {}

    # --- Register ---

    def register_call(self, call_id: str, call: ActiveCall) -> None:
        self._calls[call_id] = call
        tracer.start_call(call)  # Langfuse trace 시작 (키 없으면 no-op)

    def register_session(self, call_id: str, session: "DualSessionManager") -> None:
        self._sessions[call_id] = session

    def register_router(self, call_id: str, router: "AudioRouter") -> None:
        self._routers[call_id] = router

    def register_app_ws(self, call_id: str, ws: "WebSocket") -> None:
        self._app_ws[call_id] = ws

    def register_observer(self, call_id: str, ws: "WebSocket") -> None:
        """관전(read-only) WS 등록. 통화당 N명 가능."""
        self._observers.setdefault(call_id, set()).add(ws)

    def unregister_observer(self, call_id: str, ws: "WebSocket") -> None:
        """관전 WS 해제. 통화 생명주기(cleanup_call)는 트리거하지 않는다."""
        observers = self._observers.get(call_id)
        if observers:
            observers.discard(ws)
            if not observers:
                self._observers.pop(call_id, None)

    def observer_count(self, call_id: str) -> int:
        return len(self._observers.get(call_id, ()))

    def register_listen_task(self, call_id: str, task: asyncio.Task) -> None:
        self._listen_tasks[call_id] = task

    # --- Get (읽기 전용) ---

    def get_call(self, call_id: str) -> ActiveCall | None:
        return self._calls.get(call_id)

    def get_session(self, call_id: str) -> "DualSessionManager | None":
        return self._sessions.get(call_id)

    def get_router(self, call_id: str) -> "AudioRouter | None":
        return self._routers.get(call_id)

    def get_app_ws(self, call_id: str) -> "WebSocket | None":
        return self._app_ws.get(call_id)

    @property
    def active_call_count(self) -> int:
        return len(self._calls)

    # --- App WS 메시지 전송 ---

    async def send_to_app(self, call_id: str, msg: WsMessage) -> None:
        """App(발신자) WS + 모든 관전(observer) WS로 메시지를 broadcast한다."""
        payload = msg.model_dump()

        ws = self._app_ws.get(call_id)
        if ws:
            try:
                await ws.send_json(payload)
            except Exception:
                logger.warning("Failed to send message to App WS (call=%s)", call_id)

        # 관전자 broadcast (전송 실패한 소켓은 제거; 통화는 계속 진행)
        observers = self._observers.get(call_id)
        if observers:
            dead: list["WebSocket"] = []
            # 스냅샷 순회: await 중 register/unregister_observer가 set을 바꿔도
            # "Set changed size during iteration" 크래시가 나지 않도록.
            for obs in list(observers):
                try:
                    await obs.send_json(payload)
                except Exception:
                    dead.append(obs)
            for obs in dead:
                observers.discard(obs)
            if not observers:
                self._observers.pop(call_id, None)

    # --- 중앙 정리 (핵심) ---

    async def cleanup_call(self, call_id: str, reason: str = "unknown") -> None:
        """통화 리소스를 정리한다 (idempotent).

        어디서든 호출 가능:
          - stream.py finally (App WS 끊김)
          - twilio_webhook.py finally (Twilio 끊김)
          - twilio_status_callback (수신자 전화 끊기)
          - calls.py end_call (유저 수동 종료)
          - shutdown_all (서버 종료)

        순서:
          1. AudioRouter stop
          2. Listen task cancel
          3. DualSession close
          4. App WS 알림 + 닫기
          5. DB persist
          6. active_calls에서 제거
        """
        if call_id not in self._cleanup_locks:
            self._cleanup_locks[call_id] = asyncio.Lock()

        lock = self._cleanup_locks[call_id]

        async with lock:
            # Idempotent: 이미 정리된 경우
            if (
                call_id not in self._calls
                and call_id not in self._sessions
                and call_id not in self._routers
            ):
                return

            logger.info("Cleaning up call %s (reason: %s)", call_id, reason)

            # 0a. DB status를 먼저 COMPLETED로 업데이트 (fail-safe)
            # persist_call()이 실패하거나 Cloud Run이 종료되어도 status가 남도록
            call = self._calls.get(call_id)
            if call:
                try:
                    from src.db.pg_client import update_call

                    pre_result = "ERROR" if reason in ("error", "server_shutdown") else "SUCCESS"
                    await update_call(
                        call_id,
                        call.tenant_id,
                        status="COMPLETED",
                        result=pre_result,
                    )
                except Exception:
                    logger.warning("Failed to pre-persist status for call %s", call_id)

            # 0b. Twilio 통화 종료 (PSTN 전화 끊기)
            # asyncio.to_thread로 비동기화하여 이벤트 루프 블로킹 방지
            if call and call.call_sid:
                try:
                    from src.twilio.outbound import get_twilio_client

                    client = get_twilio_client()
                    await asyncio.to_thread(
                        client.calls(call.call_sid).update, status="completed"
                    )
                    logger.info("Twilio call terminated: %s", call.call_sid)
                except Exception as e:
                    logger.warning("Failed to terminate Twilio call %s: %s", call.call_sid, e)

            # 0c. Langfuse trace 마감 + flush (키 없으면 no-op)
            if call:
                tracer.end_call(call)

            # 1. AudioRouter stop
            router = self._routers.pop(call_id, None)
            if router:
                try:
                    await router.stop()
                except Exception as e:
                    logger.warning("Error stopping router (call=%s): %s", call_id, e)

            # 2. Listen task cancel
            listen_task = self._listen_tasks.pop(call_id, None)
            if listen_task:
                listen_task.cancel()
                try:
                    await listen_task
                except (asyncio.CancelledError, Exception):
                    pass

            # 3. DualSession close
            session = self._sessions.pop(call_id, None)
            if session:
                try:
                    await session.close()
                except Exception as e:
                    logger.warning("Error closing session (call=%s): %s", call_id, e)

            # 4. App WS + 관전(observer) WS 종료 알림 + 닫기
            ended_msg = WsMessage(
                type=WsMessageType.CALL_STATUS,
                data={"status": "ended", "reason": reason},
            ).model_dump()

            app_ws = self._app_ws.pop(call_id, None)
            if app_ws:
                try:
                    await app_ws.send_json(ended_msg)
                    await app_ws.close()
                except Exception:
                    pass

            observers = self._observers.pop(call_id, None)
            if observers:
                for obs in list(observers):
                    try:
                        await obs.send_json(ended_msg)
                        await obs.close()
                    except Exception:
                        pass

            # 5. DB persist + active_calls 제거
            call = self._calls.pop(call_id, None)
            if call:
                call.status = CallStatus.ENDED

                # 통화 요약 로그
                duration_s = round(time.time() - call.started_at, 1) if call.started_at > 0 else 0
                m = call.call_metrics
                avg_a = (
                    sum(m.session_a_latencies_ms) / len(m.session_a_latencies_ms)
                    if m.session_a_latencies_ms else 0
                )
                avg_b = (
                    sum(m.session_b_e2e_latencies_ms) / len(m.session_b_e2e_latencies_ms)
                    if m.session_b_e2e_latencies_ms else 0
                )
                logger.info(
                    "=== Call Summary ===\n"
                    "  call_id=%s  mode=%s  comm=%s\n"
                    "  duration=%.1fs  turns=%d  cost=$%.4f\n"
                    "  session_a: avg=%.0fms  samples=%d  %s\n"
                    "  session_b: avg_e2e=%.0fms  samples=%d  %s\n"
                    "  first_msg=%.0fms  echo=%d  echo_breakthroughs=%d  interrupts=%d\n"
                    "  guardrail: level2=%d  level3=%d  tokens=%d",
                    call.call_id, call.mode.value, call.communication_mode.value,
                    duration_s, m.turn_count, call.cost_tokens.cost_usd,
                    avg_a, len(m.session_a_latencies_ms),
                    [round(x) for x in m.session_a_latencies_ms],
                    avg_b, len(m.session_b_e2e_latencies_ms),
                    [round(x) for x in m.session_b_e2e_latencies_ms],
                    m.first_message_latency_ms, m.echo_suppressions,
                    m.echo_gate_breakthroughs, m.interrupt_count,
                    m.guardrail_level2_count, m.guardrail_level3_count,
                    call.cost_tokens.total,
                )

                # call_result_data에 메트릭 삽입 (기존 JSONB 컬럼 활용)
                call.call_result_data["metrics"] = m.model_dump()
                call.call_result_data["cost_usd"] = round(call.cost_tokens.cost_usd, 6)

                try:
                    from src.db.pg_client import persist_call

                    await persist_call(call)
                except Exception as e:
                    logger.warning("Failed to persist call %s: %s", call_id, e)

            logger.info("Cleanup complete for call %s", call_id)

        # Lock 정리 (async with 블록 밖에서 제거)
        self._cleanup_locks.pop(call_id, None)

    async def shutdown_all(self) -> None:
        """서버 종료 시 모든 활성 통화를 정리한다."""
        call_ids = list(self._calls.keys())
        logger.info("Shutting down %d active calls", len(call_ids))
        for call_id in call_ids:
            await self.cleanup_call(call_id, reason="server_shutdown")


call_manager = CallManager()
