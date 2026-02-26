"""Chat API 번역기 — Session B STT 텍스트를 Chat API로 번역.

Realtime API의 E2E 번역(오디오→번역)을 분리하여 할루시네이션을 방지:
  1. Realtime API: VAD + Whisper STT (오디오→텍스트)
  2. Chat API: GPT-4o-mini (텍스트→번역 텍스트)

T2V/Agent 모드 한정 사용 (V2V는 오디오 출력이 필요하므로 기존 경로 유지).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

from src.config import settings

if TYPE_CHECKING:
    from src.realtime.context_manager import ConversationContextManager

logger = logging.getLogger(__name__)


@dataclass
class ChatTranslationResult:
    """Chat API 번역 결과."""

    translated_text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class ChatTranslator:
    """GPT-4o-mini Chat API를 사용한 텍스트 번역.

    Realtime API의 E2E 오디오 번역에서 발생하는 할루시네이션을 방지하기 위해
    번역 단계만 Chat API로 분리한다. FallbackLLM 패턴(src/guardrail/fallback_llm.py)을 따름.

    사용 위치: T2V/Agent 모드의 SessionBHandler._translate_via_chat_api()
    """

    def __init__(
        self,
        source_language: str,
        target_language: str,
        context_manager: ConversationContextManager | None = None,
        model: str | None = None,
        timeout_ms: int | None = None,
    ):
        """
        Args:
            source_language: 수신자 발화 언어 (번역 원본, e.g. "ko")
            target_language: User 언어 (번역 대상, e.g. "en")
            context_manager: 대화 컨텍스트 매니저 (번역 일관성)
            model: Chat API 모델 (기본: settings.session_b_chat_translation_model)
            timeout_ms: 요청 타임아웃 (기본: settings.session_b_chat_translation_timeout_ms)
        """
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = model or settings.session_b_chat_translation_model
        self._timeout_s = (timeout_ms or settings.session_b_chat_translation_timeout_ms) / 1000
        self._context_manager = context_manager
        self._system_prompt = (
            f"You are a professional translator. "
            f"Translate the following text from {source_language} to {target_language}. "
            f"Output ONLY the translated sentence. "
            f"Do NOT add explanations, commentary, or extra words. "
            f"If the input is unclear or meaningless, output [unclear]."
        )

    async def translate(self, stt_text: str) -> ChatTranslationResult | None:
        """STT 텍스트를 Chat API로 번역한다.

        Args:
            stt_text: Whisper STT 결과 텍스트

        Returns:
            ChatTranslationResult or None on error.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
        ]
        if self._context_manager:
            context = self._context_manager.format_context()
            if context:
                messages.append({
                    "role": "system",
                    "content": f"[Previous conversation for reference]\n{context}",
                })
        messages.append({"role": "user", "content": stt_text})

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    temperature=0,
                    max_tokens=300,
                    messages=messages,
                ),
                timeout=self._timeout_s,
            )

            translated = (response.choices[0].message.content or "").strip()
            elapsed_ms = (time.monotonic() - start) * 1000
            usage = response.usage

            if not translated:
                logger.warning("ChatTranslator: empty response (%.0fms)", elapsed_ms)
                return None

            return ChatTranslationResult(
                translated_text=translated,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                latency_ms=elapsed_ms,
            )

        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "ChatTranslator timeout (%.0fms > %.0fms limit)",
                elapsed_ms,
                self._timeout_s * 1000,
            )
            return None

        except Exception:
            logger.exception("ChatTranslator error")
            return None
