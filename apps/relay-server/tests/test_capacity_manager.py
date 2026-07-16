"""CapacityManager seam 스모크 (PoC refactor · FR-5.5).

계약(reserve/commit/release + 불변식)만 검증한다. 인·아웃바운드 혼합 동시성 5케이스
(초과 0 · 종료 후 reserved 0)는 WI-5에서 확장한다.
"""

from types import SimpleNamespace

import pytest

import src.capacity_manager as cap_mod
from src.capacity_manager import CapacityManager


@pytest.mark.asyncio
async def test_reserve_commit_release_invariant(monkeypatch):
    monkeypatch.setattr(cap_mod, "settings", SimpleNamespace(max_concurrent_calls=2))
    cm = CapacityManager()
    monkeypatch.setattr(cm, "_active_count", lambda: 0)

    assert await cm.reserve("a") is True
    assert await cm.reserve("b") is True
    assert cm.reserved_count == 2
    assert await cm.reserve("c") is False  # 상한 도달 → 거절
    cm.release("a")  # 자리 반환
    assert cm.reserved_count == 1
    assert await cm.reserve("c") is True
    cm.commit("b")  # active로 이관 → reserved에서 제거
    assert "b" not in cm._reserved


@pytest.mark.asyncio
async def test_active_plus_reserved_never_exceeds_cap(monkeypatch):
    monkeypatch.setattr(cap_mod, "settings", SimpleNamespace(max_concurrent_calls=3))
    cm = CapacityManager()
    active = {"n": 2}  # 이미 active 2건 가정
    monkeypatch.setattr(cm, "_active_count", lambda: active["n"])

    assert await cm.reserve("x") is True  # active 2 + reserved 0 < 3
    assert await cm.reserve("y") is False  # 2 + 1 = 3 → 초과 방지
    assert cm.reserved_count == 1


@pytest.mark.asyncio
async def test_duplicate_call_id_cannot_share_one_reservation(monkeypatch):
    monkeypatch.setattr(cap_mod, "settings", SimpleNamespace(max_concurrent_calls=3))
    cm = CapacityManager()
    monkeypatch.setattr(cm, "_active_count", lambda: 0)

    assert await cm.reserve("same-call") is True
    assert await cm.reserve("same-call") is False
    assert cm.reserved_count == 1


def test_release_is_idempotent():
    cm = CapacityManager()
    cm.release("never-reserved")  # 예외 없이 무시
    cm.commit("never-reserved")
    assert cm.reserved_count == 0
