"""EchoGateManager — TTS 에코 차단 관리자.

Session A TTS 출력 → Twilio → 수신자 전화기 스피커 → 마이크 → Twilio → Session B
경로에서 발생하는 에코를 차단한다.

Echo Gate + Silence Injection:
  - TTS 전송 중 + 동적 cooldown 구간에서 Twilio 오디오를 무음(0xFF)으로 대체
  - 에너지 기반 break: 수신자 실제 발화(RMS > threshold) 시 즉시 게이트 해제

VoiceToVoicePipeline, TextToVoicePipeline 모두에서 사용.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from src.config import settings
from src.realtime.audio_utils import ulaw_rms as _ulaw_rms

if TYPE_CHECKING:
    from src.realtime.local_vad import LocalVAD
    from src.realtime.sessions.session_b import SessionBHandler
    from src.types import CallMetrics

logger = logging.getLogger(__name__)


class EchoGateManager:
    """Echo Gate + Silence Injection — TTS 에코 차단 관리자.

    Session A TTS → Twilio → 수신자 스피커 → 마이크 → Twilio → Session B
    경로의 에코를 차단한다. TTS 전송 중 + 동적 cooldown 구간에서
    Twilio 오디오를 mu-law silence(0xFF)로 대체.
    """

    def __init__(
        self,
        session_b: SessionBHandler,
        local_vad: LocalVAD | None,
        call_metrics: CallMetrics,
        echo_margin_s: float = 0.3,
        max_echo_window_s: float | None = 1.2,
        settling_s: float = 2.0,
    ):
        self._session_b = session_b
        self._local_vad = local_vad
        self._call_metrics = call_metrics
        self._echo_margin_s = echo_margin_s
        self._max_echo_window_s = max_echo_window_s
        self._settling_s = settling_s

        self._in_echo_window = False
        self._settling_until: float = 0.0
        self._echo_cooldown_task: asyncio.Task | None = None
        self._tts_first_chunk_at: float = 0.0
        self._tts_total_bytes: int = 0

    # --- Public properties ---

    @property
    def in_echo_window(self) -> bool:
        """Echo window가 활성 상태인지."""
        return self._in_echo_window

    @in_echo_window.setter
    def in_echo_window(self, value: bool) -> None:
        self._in_echo_window = value

    @property
    def is_suppressing(self) -> bool:
        """VAD를 억제해야 하는지. echo window 중 또는 settling 중이면 True."""
        return self._in_echo_window or time.time() < self._settling_until

    # --- Public methods ---

    def on_tts_chunk(self, chunk_size: int) -> bool:
        """TTS 청크 수신 시 호출. echo window 활성화 + 바이트 추적.

        Returns:
            True if this is the first chunk of the current TTS response.
        """
        is_first = self._tts_first_chunk_at == 0.0
        if is_first:
            self._tts_first_chunk_at = time.time()
            self._tts_total_bytes = 0
        self._tts_total_bytes += chunk_size
        self._activate()
        return is_first

    def on_tts_done(self) -> None:
        """TTS 응답 완료 시 호출 — 동적 cooldown 시작."""
        self._start_cooldown()

    def on_recipient_speech(self) -> None:
        """수신자 발화 감지 시 호출 — echo window 즉시 해제."""
        self._deactivate()

    def filter_audio(self, audio_bytes: bytes) -> bytes:
        """Twilio 오디오를 필터링한다.

        Echo window 중:
          - RMS > threshold → echo gate break (원본 전달)
          - RMS <= threshold → mu-law silence(0xFF)로 대체
        Echo window 외: 원본 그대로 전달.
        """
        if self._in_echo_window:
            rms = _ulaw_rms(audio_bytes)
            if rms > settings.echo_energy_threshold_rms:
                logger.info(
                    "High energy (RMS=%.0f) during echo window — breaking echo gate",
                    rms,
                )
                self._call_metrics.echo_gate_breakthroughs += 1
                self._deactivate()
                return audio_bytes
            return b"\xff" * len(audio_bytes)
        return audio_bytes

    async def stop(self) -> None:
        """리소스 정리 — cooldown task 취소."""
        if self._echo_cooldown_task and not self._echo_cooldown_task.done():
            self._echo_cooldown_task.cancel()
            try:
                await self._echo_cooldown_task
            except asyncio.CancelledError:
                pass

    # --- Internal ---

    def _activate(self) -> None:
        """Echo window를 활성화한다."""
        if not self._in_echo_window:
            logger.info("Echo window activated — silence injection for Session B input")
            self._call_metrics.echo_suppressions += 1
        self._in_echo_window = True
        if self._echo_cooldown_task and not self._echo_cooldown_task.done():
            self._echo_cooldown_task.cancel()
            self._echo_cooldown_task = None

    def _deactivate(self) -> None:
        """Echo window를 즉시 해제한다."""
        self._in_echo_window = False
        self._settling_until = 0.0
        if self._echo_cooldown_task and not self._echo_cooldown_task.done():
            self._echo_cooldown_task.cancel()
            self._echo_cooldown_task = None
        self._tts_first_chunk_at = 0.0
        self._tts_total_bytes = 0

    def _start_cooldown(self) -> None:
        """동적 cooldown 타이머를 시작한다."""
        if self._echo_cooldown_task and not self._echo_cooldown_task.done():
            self._echo_cooldown_task.cancel()
        first_chunk_at = self._tts_first_chunk_at
        total_bytes = self._tts_total_bytes
        self._tts_first_chunk_at = 0.0
        self._tts_total_bytes = 0
        self._echo_cooldown_task = asyncio.create_task(
            self._cooldown_timer(first_chunk_at, total_bytes)
        )

    async def _cooldown_timer(self, first_chunk_at: float, total_bytes: int) -> None:
        """동적 cooldown: TTS 길이에 비례하는 대기 시간.

        cooldown = remaining_playback + echo_margin_s
        V2V: min(..., max_echo_window_s) cap 적용
        T2V: cap 없음 (max_echo_window_s=None)
        """
        try:
            audio_duration_s = total_bytes / 8000  # g711_ulaw @ 8kHz
            elapsed = time.time() - first_chunk_at if first_chunk_at > 0 else 0
            remaining_playback = max(audio_duration_s - elapsed, 0)
            cooldown = remaining_playback + self._echo_margin_s
            if self._max_echo_window_s is not None:
                cooldown = min(cooldown, self._max_echo_window_s)

            await asyncio.sleep(cooldown)
            self._in_echo_window = False
            self._settling_until = time.time() + self._settling_s
            await self._session_b.clear_input_buffer()
            if self._local_vad is not None:
                self._local_vad.reset_state()
            logger.info(
                "Echo window closed after %.1fs cooldown — settling %.1fs "
                "(audio=%.1fs, remaining=%.1fs, margin=%.1fs)",
                cooldown,
                self._settling_s,
                audio_duration_s,
                remaining_playback,
                self._echo_margin_s,
            )
        except asyncio.CancelledError:
            pass
