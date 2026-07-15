from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from src.config import settings
from src.inbound import repository
from src.inbound.bootstrap import (
    BootstrapResult,
    bootstrap_inbound_session,
    cleanup_inbound_session,
    media_handlers_registered,
)
from src.inbound.models import DispatchRecord, DispatchState

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    status_code = 500

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class DispatchNotFound(DispatchError):
    status_code = 404


class DispatchForbidden(DispatchError):
    status_code = 403


class DispatchConflict(DispatchError):
    status_code = 409


class DispatchUnavailable(DispatchError):
    status_code = 503


class DispatchBootstrapFailed(DispatchError):
    status_code = 502


class InboundDispatchService:
    def __init__(self) -> None:
        self._create_lock = asyncio.Lock()
        self._pickup_locks: dict[UUID, asyncio.Lock] = {}
        self._starting: set[UUID] = set()
        self._known_calls: set[UUID] = set()
        self._reconnect_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._sweeper_task: asyncio.Task[None] | None = None

    async def resolve_tenant(self, inbound_number: str) -> tuple[UUID, list[str]]:
        result = await repository.resolve_inbound_tenant(inbound_number)
        if result is None:
            raise DispatchNotFound("Inbound DID is not assigned to a tenant")
        return result

    async def create_ringing(
        self,
        *,
        call_id: UUID,
        tenant_id: UUID,
        provider_call_sid: str | None,
    ) -> DispatchRecord:
        """A-owned incoming route calls this before opening pending media."""
        async with self._create_lock:
            count = await repository.count_preconnected_dispatches()
            if count >= settings.max_waiting_calls:
                raise DispatchUnavailable("Inbound waiting queue is full")
            dispatch = await repository.create_dispatch(
                call_id=call_id,
                tenant_id=tenant_id,
                provider_call_sid=provider_call_sid,
            )
            self._known_calls.add(dispatch.call_id)
            return dispatch

    async def mark_waiting(self, call_id: UUID, tenant_id: UUID) -> DispatchRecord:
        row = await repository.mark_waiting(call_id, tenant_id)
        if row is None:
            raise DispatchConflict("Dispatch is not in RINGING state")
        self._known_calls.add(call_id)
        return row

    async def list_waiting(self, tenant_id: UUID) -> list[DispatchRecord]:
        calls = await repository.list_waiting(tenant_id)
        self._known_calls.update(call.call_id for call in calls)
        return calls

    async def pickup(
        self,
        *,
        call_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
    ) -> tuple[DispatchRecord, BootstrapResult]:
        lock = self._pickup_locks.setdefault(call_id, asyncio.Lock())
        async with lock:
            return await self._pickup_once(
                call_id=call_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )

    async def _pickup_once(
        self,
        *,
        call_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
    ) -> tuple[DispatchRecord, BootstrapResult]:
        if not media_handlers_registered():
            raise DispatchUnavailable("Inbound media bootstrap is not available yet")

        claimed = await repository.claim_dispatch(
            call_id=call_id,
            tenant_id=tenant_id,
            user_id=user_id,
            claim_ttl_s=settings.claim_ttl_s,
        )
        if claimed is None:
            current = await repository.get_dispatch(call_id)
            if current is None:
                raise DispatchNotFound("Inbound call not found")
            if current.tenant_id != tenant_id:
                raise DispatchForbidden("Inbound call belongs to another tenant")
            if current.claimed_by == user_id and current.state == DispatchState.CONNECTED:
                self._known_calls.add(call_id)
                return current, self._connected_result(current)
            raise DispatchConflict("Inbound call was already claimed")

        self._known_calls.add(call_id)

        starting = await repository.transition_dispatch(
            call_id=call_id,
            tenant_id=tenant_id,
            from_states=[DispatchState.CLAIMED],
            to_state=DispatchState.SESSION_STARTING,
            claimed_by=user_id,
        )
        if starting is None:
            raise DispatchConflict("Inbound call claim changed before session start")

        self._starting.add(call_id)
        try:
            async with asyncio.timeout(settings.session_starting_timeout_s):
                result = await bootstrap_inbound_session(str(call_id), tenant_id)
        except TimeoutError as exc:
            await self._fail_start(call_id, "session_start_timeout")
            raise DispatchBootstrapFailed("Inbound session initialization timed out") from exc
        except Exception as exc:
            logger.exception("Inbound bootstrap failed (call=%s)", call_id)
            await self._fail_start(call_id, "session_start_failed")
            raise DispatchBootstrapFailed("Inbound session initialization failed") from exc
        finally:
            self._starting.discard(call_id)

        if (
            result.role != "agent"
            or not result.relay_ws_url
            or not result.source_language
            or not result.target_language
        ):
            await self._fail_start(call_id, "invalid_bootstrap_result")
            raise DispatchBootstrapFailed("Inbound media bootstrap returned an invalid result")

        connected = await repository.transition_dispatch(
            call_id=call_id,
            tenant_id=tenant_id,
            from_states=[DispatchState.SESSION_STARTING],
            to_state=DispatchState.CONNECTED,
            claimed_by=user_id,
        )
        if connected is None:
            await cleanup_inbound_session(str(call_id), "dispatch_state_changed")
            await repository.finish_dispatch(call_id, "dispatch_state_changed")
            raise DispatchConflict("Inbound call ended during session initialization")
        return connected, result

    def _connected_result(self, dispatch: DispatchRecord) -> BootstrapResult:
        languages = dispatch.languages or ["ko", "en"]
        source = languages[0] if languages else "ko"
        target = languages[1] if len(languages) > 1 else "en"
        ws_base = settings.relay_server_url.replace("https://", "wss://").replace(
            "http://", "ws://"
        )
        return BootstrapResult(
            relay_ws_url=f"{ws_base}/relay/calls/{dispatch.call_id}/stream",
            source_language=source,
            target_language=target,
        )

    async def _fail_start(self, call_id: UUID, reason: str) -> None:
        try:
            await cleanup_inbound_session(str(call_id), reason)
        finally:
            await repository.finish_dispatch(call_id, reason)

    async def authorize_pickup(
        self, *, call_id: UUID, tenant_id: UUID, user_id: UUID
    ) -> bool:
        return await repository.pickup_token_is_current(
            call_id=call_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    async def is_inbound(self, call_id: UUID) -> bool:
        if call_id in self._known_calls:
            return True
        exists = await repository.is_inbound_dispatch(call_id)
        if exists:
            self._known_calls.add(call_id)
        return exists

    def is_known_inbound(self, call_id: UUID) -> bool:
        return call_id in self._known_calls

    def cancel_reconnect_cleanup(self, call_id: UUID) -> None:
        task = self._reconnect_tasks.pop(call_id, None)
        if task is not None:
            task.cancel()

    def schedule_reconnect_cleanup(self, call_id: UUID) -> None:
        self.cancel_reconnect_cleanup(call_id)
        self._reconnect_tasks[call_id] = asyncio.create_task(
            self._cleanup_after_reconnect_grace(call_id)
        )

    async def _cleanup_after_reconnect_grace(self, call_id: UUID) -> None:
        try:
            await asyncio.sleep(settings.inbound_reconnect_grace_s)
            from src.call_manager import call_manager

            if call_manager.get_app_ws(str(call_id)) is None:
                await call_manager.cleanup_call(
                    str(call_id), reason="app_reconnect_timeout"
                )
        except asyncio.CancelledError:
            return
        finally:
            current = asyncio.current_task()
            if self._reconnect_tasks.get(call_id) is current:
                self._reconnect_tasks.pop(call_id, None)

    async def finish(self, call_id: UUID, reason: str) -> DispatchRecord | None:
        self.cancel_reconnect_cleanup(call_id)
        row = await repository.finish_dispatch(call_id, reason)
        self._known_calls.discard(call_id)
        self._pickup_locks.pop(call_id, None)
        return row

    async def start(self) -> None:
        if not settings.database_url or not media_handlers_registered():
            logger.info("Inbound dispatch worker idle until media handlers are registered")
            return
        recovered = await repository.recover_after_restart()
        if recovered:
            logger.warning("Closed %d stale inbound dispatch rows after restart", recovered)
        if self._sweeper_task is None:
            self._sweeper_task = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        if self._sweeper_task is not None:
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except asyncio.CancelledError:
                pass
            self._sweeper_task = None
        tasks = list(self._reconnect_tasks.values())
        self._reconnect_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(settings.dispatch_sweep_interval_s)
                released = await repository.release_expired_claims()
                timed_out = await repository.timeout_waiting_calls(
                    settings.inbound_wait_timeout_s
                )
                for call_id in timed_out:
                    await cleanup_inbound_session(str(call_id), "agent_timeout")
                if released or timed_out:
                    logger.info(
                        "Inbound dispatch sweep: claims_released=%d calls_timed_out=%d",
                        len(released),
                        len(timed_out),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Inbound dispatch sweep failed")


dispatch_service = InboundDispatchService()
