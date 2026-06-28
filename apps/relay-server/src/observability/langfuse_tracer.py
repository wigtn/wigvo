"""Langfuse 기반 통화 추적 — 발신자↔수신자 양방향 대화 흐름 모니터링.

매핑:
  Trace             = 통화 1건 (call_id)
  child observation = 각 발화 턴 (방향 태그: caller→callee / callee→caller)

턴이 timestamp 순으로 쌓이므로 Langfuse trace 타임라인이 곧 대화의
오고-가는 흐름이 된다. 데모 중 부스 모니터에 띄워 실시간으로 보여줄 수 있다.

안전성:
  - Langfuse 키가 없거나 패키지가 미설치면 전체 no-op (통화 경로에 영향 없음).
  - 모든 메서드는 예외를 격리한다 — 추적 실패가 통화를 깨뜨리지 않는다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.config import settings

if TYPE_CHECKING:
    from src.types import ActiveCall

logger = logging.getLogger(__name__)

# 방향 → trace 타임라인에 표시될 사람이 읽기 쉬운 라벨
DIRECTION_LABELS = {
    "caller_to_callee": "🗣️ Caller → Callee",
    "callee_to_caller": "📞 Callee → Caller",
}


class LangfuseTracer:
    """통화별 Langfuse trace 라이프사이클을 관리하는 싱글톤.

    키 미설정 시 self._enabled=False 로 모든 동작이 no-op 이 된다.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._enabled: bool = False
        # call_id → 루트 observation(=trace) 핸들. 턴은 여기에 자식으로 붙는다.
        self._roots: dict[str, Any] = {}
        self._init_client()

    def _init_client(self) -> None:
        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            logger.info("Langfuse 추적 비활성화 (키 없음) — no-op 모드")
            return
        try:
            from langfuse import Langfuse  # 지연 import: 키 있을 때만 의존

            self._client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            self._enabled = True
            logger.info("Langfuse 추적 활성화 (host=%s)", settings.langfuse_host)
        except Exception:
            logger.warning("Langfuse 초기화 실패 — 추적 비활성화", exc_info=True)
            self._client = None
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start_call(self, call: "ActiveCall") -> None:
        """통화 시작 시 루트 trace 를 생성한다."""
        if not self._enabled or not call.call_id:
            return
        if call.call_id in self._roots:
            return
        try:
            mode = call.communication_mode.value if call.communication_mode else "unknown"
            # 루트 observation 이름이 곧 trace 이름이 된다.
            root = self._client.start_observation(
                name=f"📞 WIGVO Call · {call.source_language}↔{call.target_language} · {mode}",
                metadata={
                    "call_id": call.call_id,
                    "call_sid": call.call_sid,
                    "mode": mode,
                    "source_language": call.source_language,
                    "target_language": call.target_language,
                },
            )
            self._roots[call.call_id] = root
            # trace 레벨 input (deprecated API지만 v4에서 동작)
            try:
                root.set_trace_io(
                    input={
                        "mode": mode,
                        "source_language": call.source_language,
                        "target_language": call.target_language,
                    }
                )
            except Exception:
                logger.debug("Langfuse set_trace_io(input) 실패 (무시)", exc_info=True)
        except Exception:
            logger.warning("Langfuse start_call 실패", exc_info=True)

    def record_turn(
        self,
        call: "ActiveCall",
        *,
        direction: str,
        original_text: str,
        translated_text: str,
        language: str = "",
        latency_ms: float | None = None,
        latency_breakdown: dict[str, float] | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str | None = None,
        stages: dict[str, Any] | None = None,
    ) -> None:
        """Record a single utterance turn as a child generation on the trace.

        direction: "caller_to_callee" | "callee_to_caller"
        stages: pipeline-stage signals (echo gate, VAD, STT, guardrail, ...)
                surfaced under "stage.*" metadata keys.
        """
        if not self._enabled or not call.call_id:
            return
        try:
            root = self._roots.get(call.call_id)
            if root is None:
                # Lazily create the trace even if start_call was missed.
                self.start_call(call)
                root = self._roots.get(call.call_id)
                if root is None:
                    return

            label = DIRECTION_LABELS.get(direction, direction)
            metadata: dict[str, Any] = {"direction": direction}
            if language:
                metadata["language"] = language

            # Per-stage latency under "latency.*"
            if latency_ms is not None:
                metadata["latency.total_ms"] = round(latency_ms, 1)
            if latency_breakdown:
                for key, val in latency_breakdown.items():
                    if val is not None:
                        metadata[f"latency.{key}"] = round(float(val), 1)

            # Pipeline-stage decisions under "stage.*"
            if stages:
                for key, val in stages.items():
                    if val is not None:
                        metadata[f"stage.{key}"] = val

            usage = None
            if input_tokens or output_tokens:
                usage = {
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": input_tokens + output_tokens,
                }

            obs = root.start_observation(
                name=label,
                as_type="generation",
                input=original_text,
                output=translated_text,
                model=model,
                usage_details=usage,
                metadata=metadata,
            )
            obs.end()
        except Exception:
            logger.warning("Langfuse record_turn failed", exc_info=True)

    def record_event(
        self,
        call: "ActiveCall",
        *,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a discrete pipeline moment (hallucination blocked, echo gate
        block, interrupt/barge-in) as an event observation on the trace."""
        if not self._enabled or not call.call_id:
            return
        try:
            root = self._roots.get(call.call_id)
            if root is None:
                return
            obs = root.start_observation(
                name=name,
                as_type="event",
                metadata=metadata or {},
            )
            obs.end()
        except Exception:
            logger.warning("Langfuse record_event failed", exc_info=True)

    def end_call(self, call: "ActiveCall") -> None:
        """통화 종료 시 trace 를 마감하고 flush 한다."""
        if not self._enabled:
            return
        try:
            root = self._roots.pop(call.call_id, None)
            if root is not None:
                metrics = call.call_metrics
                summary = {
                    "turn_count": getattr(metrics, "turn_count", None),
                    "cost_usd": round(call.cost_tokens.cost_usd, 6),
                    "total_tokens": call.cost_tokens.total,
                    "hallucinations_blocked": getattr(metrics, "hallucinations_blocked", None),
                }
                try:
                    root.update(output=summary)
                    root.set_trace_io(output=summary)
                except Exception:
                    logger.debug("Langfuse end trace 요약 실패 (무시)", exc_info=True)
                root.end()
            self._client.flush()
        except Exception:
            logger.warning("Langfuse end_call 실패", exc_info=True)


# 모듈 import 시 1회 생성되는 싱글톤. 키가 없으면 즉시 no-op 모드.
tracer = LangfuseTracer()
