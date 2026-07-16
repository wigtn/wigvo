"""Fallback LLM 교정 (GPT-4o-mini).

PRD M-2:
  - Level 2: 비동기 교정 (TTS 출력 후 백그라운드)
  - Level 3: 동기 교정 (TTS 차단 후 교정 완료까지 대기)
  - Timeout: 2000ms (초과 시 원문 그대로 전달)
  - Model: gpt-4o-mini, Temperature: 0, Max Tokens: 200
"""

from __future__ import annotations

import asyncio
import logging
import time

from openai import AsyncOpenAI

from src.config import settings
from src.observability.operations import operations

logger = logging.getLogger(__name__)

# PRD 명세: 교정 시스템 프롬프트
_CORRECTION_SYSTEM_PROMPT = (
    "당신은 한국어 교정 전문가입니다.\n"
    "입력된 한국어 문장을 해요체(존댓말)로 교정하세요.\n"
    "원래 의미를 변경하지 마세요.\n"
    "반말, 비격식 표현, 문법 오류만 수정하세요.\n"
    "교정된 문장만 출력하세요. 설명은 불필요합니다."
)

# Language-specific system prompts
_CORRECTION_PROMPTS: dict[str, str] = {
    "ko": _CORRECTION_SYSTEM_PROMPT,
    "en": (
        "You are a professional English language editor.\n"
        "Correct the given sentence to be polite and formal.\n"
        "Do not change the original meaning.\n"
        "Only fix informal expressions, slang, and grammar errors.\n"
        "Output only the corrected sentence."
    ),
    "ja": (
        "あなたは日本語の校正専門家です。\n"
        "入力された文章を丁寧語（です・ます調）に校正してください。\n"
        "元の意味を変えないでください。\n"
        "タメ口、くだけた表現、文法エラーのみ修正してください。\n"
        "校正された文章のみ出力してください。"
    ),
    "zh": (
        "你是中文校对专家。\n"
        "将输入的句子修改为礼貌正式的表达。\n"
        "不要改变原意。\n"
        "只修正非正式表达和语法错误。\n"
        "只输出修正后的句子。"
    ),
}


class FallbackLLM:
    """GPT-4o-mini를 사용한 텍스트 교정."""

    def __init__(
        self,
        model: str | None = None,
        timeout_ms: int | None = None,
    ):
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = model or settings.guardrail_fallback_model
        self._timeout_s = (timeout_ms or settings.guardrail_fallback_timeout_ms) / 1000

    async def correct(self, text: str, language: str = "en") -> str:
        """텍스트를 교정한다.

        Args:
            text: 교정할 텍스트
            language: 대상 언어 코드

        Returns:
            교정된 텍스트. 타임아웃 또는 에러 시 원문 반환.
        """
        system_prompt = _CORRECTION_PROMPTS.get(language, _CORRECTION_PROMPTS["en"])

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    temperature=0,
                    max_tokens=200,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                ),
                timeout=self._timeout_s,
            )

            corrected = (response.choices[0].message.content or "").strip()
            elapsed_ms = (time.monotonic() - start) * 1000

            if corrected and corrected != text:
                logger.info(
                    "Fallback LLM corrected (%s, %.0fms): '%s' -> '%s'",
                    language,
                    elapsed_ms,
                    text[:60],
                    corrected[:60],
                )
                return corrected

            logger.debug("Fallback LLM: no change needed (%.0fms)", elapsed_ms)
            return text

        except asyncio.TimeoutError:
            operations.record_openai_error("guardrail_timeout")
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "Fallback LLM timeout (%.0fms > %.0fms limit), using original text",
                elapsed_ms,
                self._timeout_s * 1000,
            )
            return text

        except Exception:
            operations.record_openai_error("guardrail")
            logger.exception("Fallback LLM error, using original text")
            return text
