"""FR-5.5 CapacityManager concurrency and cleanup contract."""

import asyncio
from types import SimpleNamespace

import pytest

import src.capacity_manager as cap_mod
from src.capacity_manager import CapacityManager


def _configure_cap(monkeypatch, cap: int) -> CapacityManager:
    monkeypatch.setattr(cap_mod, "settings", SimpleNamespace(max_concurrent_calls=cap))
    return CapacityManager()


async def _commit_and_finish(cm: CapacityManager, call_ids: list[str]) -> None:
    for call_id in call_ids:
        assert await cm.commit(call_id) is True
    snapshot = await cm.snapshot()
    assert snapshot.occupied <= snapshot.maximum
    assert snapshot.reserved == 0
    for call_id in call_ids:
        await cm.finish(call_id)
    final = await cm.snapshot()
    assert final.active == 0
    assert final.reserved == 0


@pytest.mark.asyncio
async def test_inbound_claims_concurrently_respect_cap(monkeypatch):
    cm = _configure_cap(monkeypatch, 5)
    call_ids = [f"inbound-{index}" for index in range(20)]
    results = await asyncio.gather(*(cm.reserve(call_id) for call_id in call_ids))
    accepted = [call_id for call_id, ok in zip(call_ids, results, strict=True) if ok]

    assert len(accepted) == 5
    assert (await cm.snapshot()).occupied == 5
    await _commit_and_finish(cm, accepted)


@pytest.mark.asyncio
async def test_outbound_starts_concurrently_respect_cap(monkeypatch):
    cm = _configure_cap(monkeypatch, 4)
    call_ids = [f"outbound-{index}" for index in range(16)]
    results = await asyncio.gather(*(cm.reserve(call_id) for call_id in call_ids))
    accepted = [call_id for call_id, ok in zip(call_ids, results, strict=True) if ok]

    assert len(accepted) == 4
    await _commit_and_finish(cm, accepted)


@pytest.mark.asyncio
async def test_mixed_inbound_outbound_share_one_cap(monkeypatch):
    cm = _configure_cap(monkeypatch, 6)
    call_ids = [
        name
        for index in range(10)
        for name in (f"inbound-{index}", f"outbound-{index}")
    ]
    results = await asyncio.gather(*(cm.reserve(call_id) for call_id in call_ids))
    accepted = [call_id for call_id, ok in zip(call_ids, results, strict=True) if ok]

    assert len(accepted) == 6
    assert any(call_id.startswith("inbound") for call_id in accepted)
    assert any(call_id.startswith("outbound") for call_id in accepted)
    await _commit_and_finish(cm, accepted)


@pytest.mark.asyncio
async def test_session_creation_failures_release_all_reservations(monkeypatch):
    cm = _configure_cap(monkeypatch, 8)
    call_ids = [f"failed-{index}" for index in range(8)]
    results = await asyncio.gather(*(cm.reserve(call_id) for call_id in call_ids))
    accepted = [
        call_id
        for call_id, ok in zip(call_ids, results, strict=True)
        if ok
    ]

    await asyncio.gather(*(cm.release(call_id) for call_id in accepted))
    snapshot = await cm.snapshot()
    assert snapshot.active == 0
    assert snapshot.reserved == 0


@pytest.mark.asyncio
async def test_cancelled_session_start_releases_reservation(monkeypatch):
    cm = _configure_cap(monkeypatch, 1)
    started = asyncio.Event()

    async def start_then_wait() -> None:
        assert await cm.reserve("cancelled") is True
        started.set()
        try:
            await asyncio.Future()
        finally:
            await cm.release("cancelled")

    task = asyncio.create_task(start_then_wait())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    snapshot = await cm.snapshot()
    assert snapshot.active == 0
    assert snapshot.reserved == 0


@pytest.mark.asyncio
async def test_release_never_frees_an_active_call(monkeypatch):
    cm = _configure_cap(monkeypatch, 1)
    assert await cm.reserve("active") is True
    assert await cm.commit("active") is True

    await cm.release("active")
    assert (await cm.snapshot()).active == 1
    assert await cm.reserve("new") is False
    assert (await cm.snapshot()).active == 1

    await cm.finish("active")
    assert await cm.reserve("new") is True
    await cm.release("new")


@pytest.mark.asyncio
async def test_duplicate_call_id_cannot_share_one_reservation(monkeypatch):
    cm = _configure_cap(monkeypatch, 3)
    assert await cm.reserve("same-call") is True
    assert await cm.reserve("same-call") is False
    assert cm.reserved_count == 1
    await cm.release("same-call")
