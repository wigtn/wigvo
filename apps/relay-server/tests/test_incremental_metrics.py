"""Incremental metrics DB persistence 단위 테스트.

BasePipeline._send_metrics_snapshot() 확장에 대한 검증:
  - DB 저장 트리거
  - Debounce (5초 이내 중복 방지)
  - Deferred save 실행
  - DB write payload 포맷
  - DB 에러 격리
  - _cancel_db_save_task() 동작
  - Pipeline stop() 시 task 정리
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.realtime.pipeline.base import BasePipeline
from src.types import ActiveCall, CallMode, CommunicationMode, WsMessageType


def _make_call(**overrides) -> ActiveCall:
    defaults = dict(
        call_id="test-call-incr",
        user_id="u1",
        mode=CallMode.RELAY,
        source_language="en",
        target_language="ko",
        target_phone="+821012345678",
        twilio_call_sid="CA_test",
        communication_mode=CommunicationMode.VOICE_TO_VOICE,
    )
    defaults.update(overrides)
    return ActiveCall(**defaults)


class ConcretePipeline(BasePipeline):
    """테스트용 구체 파이프라인."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self._cancel_db_save_task()

    async def handle_user_audio(self, audio_b64: str) -> None:
        pass

    async def handle_user_audio_commit(self) -> None:
        pass

    async def handle_user_text(self, text: str) -> None:
        pass

    async def handle_twilio_audio(self, audio_bytes: bytes) -> None:
        pass


@pytest.fixture
def pipeline():
    call = _make_call()
    p = ConcretePipeline(call)
    p._app_ws_send = AsyncMock()
    return p


@pytest.mark.asyncio
async def test_send_metrics_triggers_db_save(pipeline):
    """_send_metrics_snapshot() 호출 시 DB 저장이 트리거된다."""
    pipeline._persist_metrics_snapshot = AsyncMock()
    pipeline._last_db_save_at = 0.0  # 첫 호출 → 즉시 저장

    await pipeline._send_metrics_snapshot()

    # App WS로 전송 확인
    pipeline._app_ws_send.assert_called_once()
    msg = pipeline._app_ws_send.call_args[0][0]
    assert msg.type == WsMessageType.METRICS

    # DB 저장이 fire-and-forget task로 생성됨 → 짧은 대기 후 확인
    await asyncio.sleep(0.05)
    pipeline._persist_metrics_snapshot.assert_called_once()


@pytest.mark.asyncio
async def test_debounce_prevents_rapid_saves(pipeline):
    """5초 이내 2번 호출 → DB write는 즉시 1회 + deferred 1회 예약."""
    pipeline._persist_metrics_snapshot = AsyncMock()

    # 첫 호출 → 즉시 저장
    await pipeline._maybe_save_metrics_to_db()
    await asyncio.sleep(0.05)
    assert pipeline._persist_metrics_snapshot.call_count == 1

    # 즉시 두 번째 호출 → deferred task 생성 (즉시 저장 아님)
    await pipeline._maybe_save_metrics_to_db()
    await asyncio.sleep(0.05)
    # 아직 deferred가 실행되지 않았으므로 여전히 1회
    assert pipeline._persist_metrics_snapshot.call_count == 1
    # deferred task가 존재
    assert pipeline._db_save_task is not None
    assert not pipeline._db_save_task.done()

    # deferred task 정리
    pipeline._cancel_db_save_task()


@pytest.mark.asyncio
async def test_deferred_save_fires_after_delay(pipeline):
    """지연된 저장이 debounce 후 실행된다."""
    pipeline._persist_metrics_snapshot = AsyncMock()
    pipeline._DB_SAVE_DEBOUNCE_S = 0.1  # 테스트용 짧은 debounce

    # 첫 호출 → 즉시
    await pipeline._maybe_save_metrics_to_db()
    await asyncio.sleep(0.02)
    assert pipeline._persist_metrics_snapshot.call_count == 1

    # 두 번째 호출 → deferred (0.1초 미만이므로)
    await pipeline._maybe_save_metrics_to_db()
    await asyncio.sleep(0.02)
    assert pipeline._persist_metrics_snapshot.call_count == 1

    # debounce 시간 대기
    await asyncio.sleep(0.15)
    assert pipeline._persist_metrics_snapshot.call_count == 2


@pytest.mark.asyncio
async def test_persist_snapshot_correct_format(pipeline):
    """DB write payload가 cleanup_call 포맷과 일치한다."""
    captured: dict[str, object] = {}

    async def fake_update_call(call_id, **fields):
        captured["call_id"] = call_id
        captured["fields"] = fields
        return 1

    with patch("src.db.pg_client.update_call", new=fake_update_call):
        await pipeline._persist_metrics_snapshot()

    assert captured["call_id"] == "test-call-incr"
    fields = captured["fields"]
    assert "call_result_data" in fields
    assert "metrics" in fields["call_result_data"]
    assert "cost_usd" in fields["call_result_data"]
    assert "transcript_bilingual" in fields
    assert "cost_tokens" in fields
    assert "total_tokens" in fields


@pytest.mark.asyncio
async def test_persist_snapshot_handles_db_error(pipeline):
    """DB 에러 시 통화 흐름에 영향 없음 (warning 로그만)."""
    async def fake_update_call(*_args, **_kwargs):
        raise Exception("DB connection failed")

    with patch("src.db.pg_client.update_call", new=fake_update_call):
        # 에러가 전파되지 않아야 함
        await pipeline._persist_metrics_snapshot()
        # 함수가 정상 종료되면 테스트 통과


@pytest.mark.asyncio
async def test_cancel_db_save_task(pipeline):
    """_cancel_db_save_task() 호출 시 대기 중인 task가 취소된다."""
    pipeline._persist_metrics_snapshot = AsyncMock()
    pipeline._DB_SAVE_DEBOUNCE_S = 10.0  # 긴 debounce로 deferred task 유지

    # 첫 호출 → 즉시
    await pipeline._maybe_save_metrics_to_db()
    # 두 번째 → deferred task 생성
    await pipeline._maybe_save_metrics_to_db()

    assert pipeline._db_save_task is not None
    task = pipeline._db_save_task

    pipeline._cancel_db_save_task()
    assert pipeline._db_save_task is None
    # 취소된 task는 완료 상태
    await asyncio.sleep(0.05)
    assert task.done()


@pytest.mark.asyncio
async def test_pipeline_stop_cancels_save(pipeline):
    """stop() 시 deferred save task가 정리된다."""
    pipeline._persist_metrics_snapshot = AsyncMock()
    pipeline._DB_SAVE_DEBOUNCE_S = 10.0

    await pipeline._maybe_save_metrics_to_db()
    await pipeline._maybe_save_metrics_to_db()
    assert pipeline._db_save_task is not None

    await pipeline.stop()
    assert pipeline._db_save_task is None
