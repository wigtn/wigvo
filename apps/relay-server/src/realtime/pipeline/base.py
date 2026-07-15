"""BasePipeline ABC — 모든 파이프라인의 공통 인터페이스.

AudioRouter가 CommunicationMode에 따라 적절한 Pipeline 구현체에 위임한다.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine

from src.types import ActiveCall, WsMessage, WsMessageType

logger = logging.getLogger(__name__)


class BasePipeline(ABC):
    """파이프라인 공통 인터페이스 (Strategy 패턴).

    모든 파이프라인은 동일한 메서드 시그니처를 제공하여
    AudioRouter가 모드에 상관없이 동일한 인터페이스로 호출할 수 있다.
    """

    _DB_SAVE_DEBOUNCE_S: float = 5.0

    def __init__(self, call: ActiveCall):
        self.call = call
        self._app_ws_send: Callable[[WsMessage], Coroutine[Any, Any, None]] | None = None
        # Incremental metrics persistence
        self._last_db_save_at: float = 0.0
        self._db_save_task: asyncio.Task | None = None

    async def _send_metrics_snapshot(self) -> None:
        """현재 CallMetrics 스냅샷을 App에 전송 + DB에 incremental 저장."""
        if self._app_ws_send:
            await self._app_ws_send(WsMessage(
                type=WsMessageType.METRICS,
                data=self.call.call_metrics.model_dump(),
            ))
        # DB incremental save (debounced, fire-and-forget)
        await self._maybe_save_metrics_to_db()

    async def _maybe_save_metrics_to_db(self) -> None:
        """Debounce 판정 후 즉시 or 지연 DB 저장."""
        now = time.time()
        elapsed = now - self._last_db_save_at

        if elapsed >= self._DB_SAVE_DEBOUNCE_S:
            # 충분한 시간 경과 → 즉시 저장
            self._last_db_save_at = now
            # 대기 중인 deferred task 취소
            if self._db_save_task and not self._db_save_task.done():
                self._db_save_task.cancel()
                self._db_save_task = None
            asyncio.create_task(self._persist_metrics_snapshot())
        else:
            # debounce 구간 → 기존 deferred task 없으면 예약
            if self._db_save_task is None or self._db_save_task.done():
                delay = self._DB_SAVE_DEBOUNCE_S - elapsed
                self._db_save_task = asyncio.create_task(self._deferred_persist(delay))

    async def _deferred_persist(self, delay_s: float) -> None:
        """지연 후 DB 저장 (CancelledError 안전)."""
        try:
            await asyncio.sleep(delay_s)
            self._last_db_save_at = time.time()
            await self._persist_metrics_snapshot()
        except asyncio.CancelledError:
            pass

    async def _persist_metrics_snapshot(self) -> None:
        """실제 DB write — cleanup_call과 동일 포맷. 에러 격리."""
        call = self.call
        if not call.call_id:
            return
        try:
            from src.db.pg_client import update_call

            m = call.call_metrics
            data: dict[str, object] = {
                "call_result_data": {
                    **call.call_result_data,
                    "metrics": m.model_dump(),
                    "cost_usd": round(call.cost_tokens.cost_usd, 6),
                },
                "transcript_bilingual": [t.model_dump() if hasattr(t, "model_dump") else t for t in call.transcript_bilingual],
                "cost_tokens": call.cost_tokens.model_dump(),
                "total_tokens": call.cost_tokens.total,
            }
            # cleanup_call 미실행 대비: call_sid, duration, communication_mode도 저장
            if call.call_sid:
                data["call_sid"] = call.call_sid
            if call.communication_mode:
                data["communication_mode"] = call.communication_mode.value
            if call.started_at > 0:
                data["duration_s"] = round(time.time() - call.started_at, 1)
            await update_call(call.call_id, call.tenant_id, **data)
            logger.debug("Incremental metrics saved for call %s", call.call_id)
        except Exception:
            logger.warning("Failed to save incremental metrics for call %s", call.call_id, exc_info=True)

    def _cancel_db_save_task(self) -> None:
        """대기 중인 deferred DB save task를 취소한다."""
        if self._db_save_task and not self._db_save_task.done():
            self._db_save_task.cancel()
            self._db_save_task = None

    async def _send_pipeline_event(self, stage: str, event: str, **kwargs: Any) -> None:
        """3-Stage Filter 이벤트를 클라이언트에 전송."""
        if self._app_ws_send:
            await self._app_ws_send(
                WsMessage(
                    type=WsMessageType.PIPELINE_EVENT,
                    data={"stage": stage, "event": event, **kwargs},
                )
            )

    @abstractmethod
    async def start(self) -> None:
        """파이프라인을 시작한다."""

    @abstractmethod
    async def stop(self) -> None:
        """파이프라인을 중지하고 리소스를 정리한다."""

    @abstractmethod
    async def handle_user_audio(self, audio_b64: str) -> None:
        """User 앱에서 받은 오디오를 처리한다."""

    @abstractmethod
    async def handle_user_audio_commit(self) -> None:
        """Client VAD 발화 종료 → 오디오 커밋."""

    @abstractmethod
    async def handle_user_text(self, text: str) -> None:
        """User 텍스트 입력을 처리한다."""

    @abstractmethod
    async def handle_twilio_audio(self, audio_bytes: bytes) -> None:
        """Twilio에서 받은 수신자 오디오를 처리한다."""

    async def handle_typing_started(self) -> None:
        """사용자가 타이핑을 시작했을 때 호출 (T2V 전용, 기본 no-op)."""
