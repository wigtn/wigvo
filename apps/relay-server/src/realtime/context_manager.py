"""대화 컨텍스트 매니저 — 최근 턴을 슬라이딩 윈도우로 관리.

번역 일관성을 위해 OpenAI Realtime 세션에 최근 대화 맥락을 주입한다.
session.update가 아닌 conversation.item.create를 사용하여 세션 상태 리셋을 방지한다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.realtime.sessions.session_manager import RealtimeSession

logger = logging.getLogger(__name__)

MAX_TURNS = 6
MAX_CHARS_PER_TURN = 100


class ConversationContextManager:
    """슬라이딩 윈도우 기반 대화 컨텍스트 매니저.

    최근 N턴의 대화를 유지하고, OpenAI 세션에 컨텍스트로 주입한다.
    ~200 토큰 (6턴 x 100자) 으로 오디오 대비 무시 가능한 비용.
    """

    def __init__(self, max_turns: int = MAX_TURNS, max_chars: int = MAX_CHARS_PER_TURN):
        self._turns: list[dict[str, str]] = []
        self._max_turns = max_turns
        self._max_chars = max_chars

    def add_turn(self, role: str, text: str) -> None:
        """완료된 턴을 추가한다.

        Args:
            role: "user" 또는 "recipient"
            text: 번역된 텍스트
        """
        text = text.strip()
        if not text:
            return
        self._turns.append({
            "role": role,
            "text": text[:self._max_chars],
        })
        if len(self._turns) > self._max_turns:
            self._turns = self._turns[-self._max_turns:]
        logger.debug("Context: added %s turn (%d total)", role, len(self._turns))

    def format_context(self) -> str:
        """컨텍스트를 읽기 쉬운 포맷으로 반환한다."""
        if not self._turns:
            return ""
        lines = []
        for turn in self._turns:
            label = "User" if turn["role"] == "user" else "Recipient"
            lines.append(f"{label}: {turn['text']}")
        return "\n".join(lines)

    async def inject_context(self, session: RealtimeSession) -> None:
        """OpenAI 세션에 컨텍스트를 conversation.item.create로 주입한다.

        session.update를 사용하지 않는 이유: 세션 전체 설정을 리셋하기 때문.
        conversation.item.create는 기존 세션 상태를 유지하면서 아이템만 추가한다.
        """
        context = self.format_context()
        if not context:
            return

        await session.send_context_item(
            f"[Previous conversation for reference ONLY]\n{context}\n"
            f"[IMPORTANT: Translate ONLY the actual audio you hear next. "
            f"Do NOT generate what you expect based on this context.]"
        )
        logger.debug("Context injected: %d turns", len(self._turns))

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    def clear(self) -> None:
        """컨텍스트를 초기화한다."""
        self._turns.clear()
