"""단일 프로세스 통화 용량의 원자적 소유자 (FR-5.5).

예약과 활성 상태를 같은 lock 아래에서 관리해 모든 시점에
``active + reserved <= max_concurrent_calls``를 보장한다.

수명주기:
  reserve() -> OpenAI/Twilio 준비 -> commit() -> 통화 종료 시 finish()

``release()``는 합의된 seam대로 아직 활성화되지 않은 reservation만 해제한다.
이미 active인 통화는 중앙 cleanup 경로에서 ``finish()``로 반환한다.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass

from src.config import settings


@dataclass(frozen=True)
class CapacitySnapshot:
    active: int
    reserved: int
    maximum: int

    @property
    def occupied(self) -> int:
        return self.active + self.reserved

    @property
    def available(self) -> int:
        return max(0, self.maximum - self.occupied)

    def as_dict(self) -> dict[str, int]:
        return {**asdict(self), "occupied": self.occupied, "available": self.available}


class CapacityManager:
    """전역 상한의 단일 소유자. 모든 상태 전이는 asyncio.Lock 아래에서 수행한다."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._reserved: set[str] = set()
        self._active: set[str] = set()

    @property
    def reserved_count(self) -> int:
        return len(self._reserved)

    @property
    def active_count(self) -> int:
        return len(self._active)

    def _snapshot_unlocked(self) -> CapacitySnapshot:
        return CapacitySnapshot(
            active=len(self._active),
            reserved=len(self._reserved),
            maximum=settings.max_concurrent_calls,
        )

    async def snapshot(self) -> CapacitySnapshot:
        async with self._lock:
            return self._snapshot_unlocked()

    async def reserve(self, call_id: str) -> bool:
        """빈 자리를 원자적으로 예약한다. 중복 ID와 상한 도달은 False다."""
        rejected: CapacitySnapshot | None = None
        async with self._lock:
            if call_id in self._reserved or call_id in self._active:
                return False
            snapshot = self._snapshot_unlocked()
            if snapshot.occupied >= snapshot.maximum:
                rejected = snapshot
            else:
                self._reserved.add(call_id)
                return True

        # lock 밖에서 로깅/알림한다. 인바운드와 아웃바운드가 같은 경로를 탄다.
        if rejected is not None:
            from src.observability.operations import operations

            operations.record_capacity_rejection(rejected)
        return False

    async def commit(self, call_id: str) -> bool:
        """reservation을 active로 원자 전환한다."""
        async with self._lock:
            if call_id not in self._reserved:
                return False
            self._reserved.remove(call_id)
            self._active.add(call_id)
            return True

    async def release(self, call_id: str) -> None:
        """아직 active가 아닌 reservation만 반환한다 (idempotent)."""
        async with self._lock:
            self._reserved.discard(call_id)

    async def finish(self, call_id: str) -> None:
        """중앙 cleanup에서 active 슬롯을 반환한다 (idempotent)."""
        async with self._lock:
            self._active.discard(call_id)


capacity_manager = CapacityManager()
