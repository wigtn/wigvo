"""동시통화 용량 관리 — `active + reserved ≤ cap` 불변식의 단일 소유자 (FR-5.5 seam).

PoC(단일 프로세스): asyncio.Lock 아래에서 active 확인 + reservation 생성을 원자적으로
수행해, 기존 `/calls/start` soft-cap의 TOCTOU 경쟁(동시 start 시 1~2개 초과)을 해소한다.

수명주기: OpenAI 세션 생성 전에 reserve() → register_call로 active 편입 후 commit()
→ 모든 실패·취소 경로에서 release(). 인바운드(WI-6)·아웃바운드가 같은 인스턴스를 공유한다.

seam 범위: 여기서는 아웃바운드 경쟁 해소 + 계약 확립까지. WI-5에서 인바운드 예약·claim TTL·
종료 트리거 다중화 + 인·아웃 혼합 동시성 테스트(초과 0 · 종료 후 reserved 0)로 하드닝한다.
"""

import asyncio
import logging

from src.config import settings

logger = logging.getLogger(__name__)


class CapacityManager:
    """전역 동시통화 상한의 단일 소유자. 불변식: active + reserved ≤ max_concurrent_calls."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._reserved: set[str] = set()

    def _active_count(self) -> int:
        # 지연 import — call_manager ↔ capacity_manager 순환 참조 방지
        from src.call_manager import call_manager

        return call_manager.active_call_count

    @property
    def reserved_count(self) -> int:
        return len(self._reserved)

    async def reserve(self, call_id: str) -> bool:
        """active + reserved < cap이면 예약하고 True. 초과면 False(통화 미생성)."""
        async with self._lock:
            # 같은 call_id로 시작 요청이 겹치면 set 크기는 늘지 않지만 두 요청 모두
            # 예약 성공으로 진행할 수 있다. 두 번째 요청을 거절해 한 reservation이
            # 정확히 한 통화 생성 경로만 소유하도록 한다.
            if call_id in self._reserved:
                return False
            if self._active_count() + len(self._reserved) >= settings.max_concurrent_calls:
                return False
            self._reserved.add(call_id)
            return True

    def commit(self, call_id: str) -> None:
        """예약 → active 전환. register_call로 active에 편입된 뒤 호출."""
        self._reserved.discard(call_id)

    def release(self, call_id: str) -> None:
        """예약 취소 (idempotent). 모든 실패·취소 경로에서 호출."""
        self._reserved.discard(call_id)


# 전역 단일 인스턴스 — 인·아웃바운드가 공유한다.
capacity_manager = CapacityManager()
