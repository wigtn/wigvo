"""FakeRealtimeSession — OpenAI Realtime API 대역.

RealtimeSession과 동일한 인터페이스를 duck typing으로 구현하되,
실제 WebSocket 연결 없이 미리 준비된 응답 스크립트를 발행한다.

핵심 동작:
  - send_audio() → 내부 리스트에 base64 청크 수집
  - commit_audio() / create_response() → _emit_next_response() 호출
  - ResponseScript.to_events()로 이벤트 시퀀스를 생성하여 등록된 핸들러를 순차 호출
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass
class FakeEvent:
    """하나의 OpenAI Realtime API 이벤트."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    delay_ms: float = 0


@dataclass
class ResponseScript:
    """하나의 응답 시퀀스 스크립트.

    실제 OpenAI가 보내는 이벤트 시퀀스를 재현한다:
      1. input_audio_transcription.completed (STT 결과)
      2. response.audio.delta × N (TTS 오디오 청크)
      3. response.audio_transcript.delta (번역 텍스트 스트리밍)
      4. response.audio_transcript.done (번역 완료)
      5. response.done (토큰 사용량)
    """

    stt_text: str = ""
    translation_text: str = ""
    tts_audio_chunks: list[bytes] = field(default_factory=list)
    token_usage: dict[str, Any] = field(default_factory=dict)

    def to_events(self) -> list[FakeEvent]:
        """이벤트 시퀀스로 변환한다."""
        events: list[FakeEvent] = []

        # 1. STT 결과 (input_audio_transcription.completed)
        if self.stt_text:
            events.append(
                FakeEvent(
                    type="conversation.item.input_audio_transcription.completed",
                    data={"transcript": self.stt_text},
                )
            )

        # 2. TTS 오디오 청크 (response.audio.delta)
        for chunk in self.tts_audio_chunks:
            audio_b64 = base64.b64encode(chunk).decode("ascii")
            events.append(
                FakeEvent(
                    type="response.audio.delta",
                    data={"delta": audio_b64},
                    delay_ms=1,
                )
            )

        # 3. 번역 텍스트 스트리밍 (response.audio_transcript.delta)
        if self.translation_text:
            events.append(
                FakeEvent(
                    type="response.audio_transcript.delta",
                    data={"delta": self.translation_text},
                )
            )

        # 4. 번역 완료 (response.audio_transcript.done)
        if self.translation_text:
            events.append(
                FakeEvent(
                    type="response.audio_transcript.done",
                    data={"transcript": self.translation_text},
                )
            )

        # 5. 응답 완료 (response.done) + 토큰 사용량
        usage = self.token_usage or {
            "input_token_details": {"audio_tokens": 100, "text_tokens": 10},
            "output_token_details": {"audio_tokens": 50, "text_tokens": 5},
        }
        events.append(
            FakeEvent(
                type="response.done",
                data={"response": {"usage": usage}},
            )
        )

        return events


class FakeRealtimeSession:
    """OpenAI Realtime API 대역 (duck typing).

    RealtimeSession과 동일한 public 인터페이스를 구현하되,
    WebSocket 연결 없이 미리 준비된 ResponseScript를 발행한다.
    """

    def __init__(self, label: str, session_id: str = ""):
        self.label = label
        self.session_id = session_id or f"fake_sess_{label}"
        self._closed = False
        self._handlers: dict[str, list[Callable[..., Coroutine]]] = {}
        self._on_connection_lost: Callable[[], Coroutine] | None = None

        # 응답 스크립트 큐
        self._response_queue: list[ResponseScript] = []

        # 테스트 관측용: 수집된 오디오 청크
        self.received_audio_chunks: list[str] = []  # base64
        self.received_texts: list[str] = []
        self.committed: bool = False

    # --- RealtimeSession 인터페이스 ---

    def on(self, event_type: str, handler: Callable[..., Coroutine]) -> None:
        """이벤트 핸들러 등록 (중복 방지)."""
        handlers = self._handlers.setdefault(event_type, [])
        if handler not in handlers:
            handlers.append(handler)

    def set_on_connection_lost(self, handler: Callable[[], Coroutine]) -> None:
        self._on_connection_lost = handler

    async def send_audio(self, audio_b64: str) -> None:
        """base64 오디오 청크를 수집한다."""
        self.received_audio_chunks.append(audio_b64)

    async def send_text(self, text: str) -> None:
        """텍스트 메시지를 수집하고 응답을 발행한다."""
        self.received_texts.append(text)
        await self._emit_next_response()

    async def send_text_item(self, text: str) -> None:
        """텍스트 아이템만 생성 (response.create 없이)."""
        self.received_texts.append(text)

    async def commit_audio(self) -> None:
        """오디오 커밋 + 응답 발행."""
        self.committed = True
        await self._emit_next_response()

    async def commit_audio_only(self) -> None:
        """오디오 커밋만 (response.create 없이)."""
        self.committed = True

    async def create_response(self, instructions: str | None = None) -> None:
        """응답 생성 요청 → 다음 스크립트 발행."""
        await self._emit_next_response()

    async def clear_input_buffer(self) -> None:
        """입력 버퍼 클리어 (no-op)."""
        pass

    async def cancel_response(self) -> None:
        """응답 취소 (no-op)."""
        pass

    async def send_context_item(self, text: str) -> None:
        """컨텍스트 아이템 추가 (no-op)."""
        pass

    async def delete_item(self, item_id: str) -> None:
        """대화 아이템 삭제 (no-op)."""
        pass

    async def send_function_call_output(self, call_id: str, output: str) -> None:
        """Function call 결과 전송 (no-op)."""
        pass

    async def connect(
        self,
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        """연결 (no-op — FakeSession은 항상 연결됨)."""
        pass

    async def close(self) -> None:
        """세션 종료."""
        self._closed = True

    async def listen(self) -> None:
        """이벤트 리스너 (no-op — FakeSession은 _emit에서 직접 핸들러 호출)."""
        pass

    @property
    def is_closed(self) -> bool:
        return self._closed

    # --- 테스트 헬퍼 ---

    def enqueue_response(self, script: ResponseScript) -> None:
        """응답 스크립트를 큐에 추가한다."""
        self._response_queue.append(script)

    def enqueue_responses(self, scripts: list[ResponseScript]) -> None:
        """여러 응답 스크립트를 큐에 추가한다."""
        self._response_queue.extend(scripts)

    async def emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """단일 이벤트를 수동으로 발행한다 (테스트에서 직접 사용)."""
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            await handler(data)

    # --- Internal ---

    async def _emit_next_response(self) -> None:
        """큐에서 ResponseScript를 꺼내 등록된 핸들러를 순차 호출한다."""
        if not self._response_queue:
            logger.debug("[%s] No response script in queue — skipping", self.label)
            return

        script = self._response_queue.pop(0)
        events = script.to_events()

        for event in events:
            if event.delay_ms > 0:
                await asyncio.sleep(event.delay_ms / 1000)
            handlers = self._handlers.get(event.type, [])
            for handler in handlers:
                await handler(event.data)
