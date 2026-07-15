"""flow_span seam 테스트 (PoC refactor · FR-5.1) — PII 드롭 + no-op 안전성.

키 없는 환경에서 flow_span은 no-op이어야 하고, 본문 예외는 삼키지 말고 전파해야 한다.
PII 가능성 attr(transcript/전화번호/번역문 등)은 _safe_attrs가 드롭한다.
"""

import pytest

from src.observability.langfuse_tracer import LangfuseTracer


def test_flow_span_noop_when_disabled():
    t = LangfuseTracer()
    t._enabled = False
    with t.flow_span("wi.x", call_id="c1", tenant_id="t1") as span:
        assert span is None  # 키 없으면 no-op


def test_safe_attrs_drops_pii_keys():
    t = LangfuseTracer()
    safe = t._safe_attrs(
        {
            "call_id": "c1",
            "tenant_id": "t1",
            "state": "CLAIMED",
            "duration_ms": 12,
            "transcript": "안녕하세요",
            "phone_number": "+8210...",
            "translated_text": "hi",
        }
    )
    assert safe == {"call_id": "c1", "tenant_id": "t1", "state": "CLAIMED", "duration_ms": 12}


def test_flow_span_runs_body_and_propagates_exception():
    t = LangfuseTracer()
    t._enabled = False

    ran = {"v": False}
    with t.flow_span("wi.ok"):
        ran["v"] = True
    assert ran["v"] is True

    # 본문 예외는 추적이 삼키지 않고 그대로 전파
    with pytest.raises(ValueError):
        with t.flow_span("wi.boom"):
            raise ValueError("boom")
