"""VoiceToVoicePipeline — 양방향 음성 번역 파이프라인.

User 음성 → 번역 → Twilio TTS + 수신자 음성 → 번역 → App TTS

핵심 컴포넌트:
  - Echo Gate + Silence Injection (TTS 에코 차단)
  - Audio Energy Gate
  - Interrupt Handler (3-level priority)
  - First Message Handler
  - Context Manager (6턴 슬라이딩)
  - Session Recovery + degraded mode
  - Guardrail
"""

import asyncio
import base64
import logging
import time
from typing import Any, Callable, Coroutine, Literal

from src.config import settings
from src.guardrail.checker import GuardrailChecker
from src.realtime.audio_utils import pcm16_rms as _pcm16_rms, ulaw_rms as _ulaw_rms
from src.realtime.context_manager import ConversationContextManager
from src.realtime.first_message import FirstMessageHandler
from src.realtime.interrupt_handler import InterruptHandler
from src.realtime.local_vad import LocalVAD
from src.realtime.pipeline.base import BasePipeline
from src.realtime.pipeline.echo_gate import EchoGateManager
from src.realtime.recovery import SessionRecoveryManager
from src.realtime.ring_buffer import AudioRingBuffer
from src.realtime.sessions.session_a import SessionAHandler
from src.realtime.sessions.session_b import SessionBHandler
from src.realtime.sessions.session_manager import DualSessionManager
from src.tools.definitions import get_tools_for_mode
from src.twilio.media_stream import TwilioMediaStreamHandler
from src.types import (
    ActiveCall,
    CallMode,
    CommunicationMode,
    WsMessage,
    WsMessageType,
)

logger = logging.getLogger(__name__)


class VoiceToVoicePipeline(BasePipeline):
    """양방향 음성 번역 파이프라인 (EchoGateManager + Interrupt + Recovery)."""

    def __init__(
        self,
        call: ActiveCall,
        dual_session: DualSessionManager,
        twilio_handler: TwilioMediaStreamHandler,
        app_ws_send: Callable[[WsMessage], Coroutine[Any, Any, None]],
        prompt_a: str = "",
        prompt_b: str = "",
    ):
        super().__init__(call)
        self.dual_session = dual_session
        self.twilio_handler = twilio_handler
        self._app_ws_send = app_ws_send
        self._call_timer_task: asyncio.Task | None = None
        self._prompt_a = prompt_a
        self._prompt_b = prompt_b

        # Guardrail (PRD Phase 4 / M-2)
        self.guardrail: GuardrailChecker | None = None
        if settings.guardrail_enabled:
            self.guardrail = GuardrailChecker(
                target_language=call.target_language,
                enabled=True,
            )

        # 대화 컨텍스트 매니저 (번역 일관성)
        self.context_manager = ConversationContextManager()

        # Session A 핸들러: User -> 수신자
        self.session_a = SessionAHandler(
            session=dual_session.session_a,
            call=call,
            on_tts_audio=self._on_session_a_tts,
            on_caption=self._on_session_a_caption,
            on_response_done=self._on_session_a_done,
            guardrail=self.guardrail,
            on_guardrail_filler=self._on_guardrail_filler,
            on_guardrail_corrected_tts=self._on_guardrail_corrected_tts,
            on_guardrail_event=self._on_guardrail_event,
            on_function_call_result=self._on_function_call_result,
            on_transcript_complete=self._on_turn_complete,
            on_user_transcription=self._on_user_transcription,
        )

        # Session B 핸들러: 수신자 -> User
        self.session_b = SessionBHandler(
            session=dual_session.session_b,
            call=call,
            on_translated_audio=self._on_session_b_audio,
            on_caption=self._on_session_b_caption,
            on_original_caption=self._on_session_b_original_caption,
            on_recipient_speech_started=self._on_recipient_started,
            on_recipient_speech_stopped=self._on_recipient_stopped,
            on_transcript_complete=self._on_turn_complete,
            on_caption_done=self._on_session_b_caption_done,
            use_local_vad=settings.local_vad_enabled,
        )

        # Local VAD (Silero + RMS Energy Gate)
        self.local_vad: LocalVAD | None = None
        if settings.local_vad_enabled:
            self.local_vad = LocalVAD(
                rms_threshold=settings.local_vad_rms_threshold,
                speech_threshold=settings.local_vad_speech_threshold,
                silence_threshold=settings.local_vad_silence_threshold,
                min_speech_frames=settings.local_vad_min_speech_frames,
                min_silence_frames=settings.local_vad_min_silence_frames,
                on_speech_start=self._on_local_vad_speech_start,
                on_speech_end=self._on_local_vad_speech_end,
            )

        # First Message 핸들러
        self.first_message = FirstMessageHandler(
            call=call,
            session_a=self.session_a,
            on_notify_app=self._notify_app,
        )

        # Interrupt 핸들러
        self.interrupt = InterruptHandler(
            session_a=self.session_a,
            twilio_handler=twilio_handler,
            on_notify_app=self._notify_app,
            call=call,
        )

        # Ring Buffers
        self.ring_buffer_a = AudioRingBuffer(
            capacity=settings.ring_buffer_capacity_slots,
        )
        self.ring_buffer_b = AudioRingBuffer(
            capacity=settings.ring_buffer_capacity_slots,
        )

        # User audio RMS logging (주기적 샘플링)
        self._user_audio_chunk_count = 0

        # Echo Gate Manager (TTS 에코 차단)
        self.echo_gate = EchoGateManager(
            session_b=self.session_b,
            local_vad=self.local_vad,
            call_metrics=self.call.call_metrics,
            echo_margin_s=0.3,
            max_echo_window_s=1.2,
            settling_s=settings.echo_post_settling_s,
        )

        # Interrupt debounce: 노이즈에 의한 즉시 TTS 취소 방지 (400ms 대기 후 확인)

        # Session B 출력 큐 (수신자 TTS 순차 스트리밍)
        # 현재 응답은 즉시 스트리밍, 다음 응답은 재생 완료 대기 후 시작
        _BOutputItem = tuple[
            Literal["audio", "caption", "original_caption", "caption_done"],
            Any,
        ]
        self._b_output_queue: asyncio.Queue[_BOutputItem] = asyncio.Queue()
        self._b_output_drain_task: asyncio.Task | None = None
        self._b_playback_first_chunk_at: float = 0.0
        self._b_playback_total_bytes: int = 0

        # Recovery Managers
        tools_a = get_tools_for_mode(call.mode) if call.mode == CallMode.AGENT else None
        self.recovery_a = SessionRecoveryManager(
            session=dual_session.session_a,
            ring_buffer=self.ring_buffer_a,
            call=call,
            system_prompt=prompt_a,
            on_notify_app=self._notify_app,
            tools=tools_a,
        )
        self.recovery_b = SessionRecoveryManager(
            session=dual_session.session_b,
            ring_buffer=self.ring_buffer_b,
            call=call,
            system_prompt=prompt_b,
            on_notify_app=self._notify_app,
            on_recovered_caption=self._on_session_b_caption,
        )

    async def start(self) -> None:
        self.call.started_at = time.time()
        self._call_timer_task = asyncio.create_task(self._call_duration_timer())
        self._b_output_drain_task = asyncio.create_task(self._drain_b_output())
        self.recovery_a.start_monitoring()
        self.recovery_b.start_monitoring()
        logger.info("VoiceToVoicePipeline started for call %s", self.call.call_id)

    async def stop(self) -> None:
        if self._call_timer_task:
            self._call_timer_task.cancel()
            try:
                await self._call_timer_task
            except asyncio.CancelledError:
                pass

        await self.echo_gate.stop()

        if self._b_output_drain_task and not self._b_output_drain_task.done():
            self._b_output_drain_task.cancel()
            try:
                await self._b_output_drain_task
            except asyncio.CancelledError:
                pass

        self._cancel_db_save_task()

        if self.local_vad:
            self.local_vad.reset()

        self.session_b.stop()
        await self.recovery_a.stop()
        await self.recovery_b.stop()
        logger.info("VoiceToVoicePipeline stopped for call %s", self.call.call_id)

    # --- User App -> Session A ---

    async def handle_user_audio(self, audio_b64: str) -> None:
        audio_bytes = base64.b64decode(audio_b64)
        seq = self.ring_buffer_a.write(audio_bytes)

        # 사용자 오디오 RMS 로깅 (~1초마다, pcm16 100ms chunk 기준 10회)
        self._user_audio_chunk_count += 1
        if self._user_audio_chunk_count % 10 == 0:
            rms = _pcm16_rms(audio_bytes)
            logger.info("[SessionA] User audio RMS=%.0f", rms)

        if self.recovery_a.is_recovering:
            return
        if self.recovery_a.is_degraded:
            transcript = await self.recovery_a.process_degraded_audio(audio_bytes)
            if transcript:
                await self._on_session_a_caption("user", f"[지연] {transcript}")
            return

        await self.session_a.send_user_audio(audio_b64)
        self.ring_buffer_a.mark_sent(seq)

    async def handle_user_audio_commit(self) -> None:
        if self.recovery_a.is_recovering or self.recovery_a.is_degraded:
            return
        await self._app_ws_send(
            WsMessage(
                type=WsMessageType.TRANSLATION_STATE,
                data={"state": "processing"},
            )
        )
        await self.context_manager.inject_context(self.dual_session.session_a)
        await self.session_a.commit_user_audio()

    async def handle_user_text(self, text: str) -> None:
        self.call.transcript_history.append({"role": "user", "text": text})
        self.session_a.mark_user_input()

        if self.interrupt.is_recipient_speaking:
            logger.info("Recipient is speaking — holding text until they finish...")
            await self.interrupt.wait_for_recipient_done(timeout=10.0)

        if self.session_a.is_generating:
            logger.debug("Waiting for Session A to finish before sending text...")
            await self.session_a.wait_for_done(timeout=5.0)

        if self.call.mode == CallMode.RELAY:
            await self.session_a.send_user_text(
                f"[User says in {self.call.source_language}]: {text}"
            )
        else:
            await self.session_a.send_user_text(text)

    # --- Twilio -> Session B ---

    async def handle_twilio_audio(self, audio_bytes: bytes) -> None:
        seq = self.ring_buffer_b.write(audio_bytes)

        if self.recovery_b.is_recovering:
            return
        if self.recovery_b.is_degraded:
            return

        # Echo Gate: echo window 중 무음 대체 또는 에너지 기반 break
        effective_audio = self.echo_gate.filter_audio(audio_bytes)

        # Local VAD 경로: VAD 상태에 따라 실제 오디오 또는 무음을 Session B에 전송
        # SPEAKING 상태: 오디오 그대로 전송 (GPT-4o가 전체 음성을 들어야 정확한 번역 가능)
        # SILENCE 상태: 무음 프레임 전송 (노이즈가 Whisper에 축적되어 할루시네이션 유발 방지)
        # Echo window 중에는 VAD 처리를 스킵 (에코가 speech로 오감지되는 것을 방지)
        if self.local_vad is not None:
            vad_suppressed = self.echo_gate.is_suppressing
            if not vad_suppressed:
                await self.local_vad.process(effective_audio)
            if self.local_vad.is_speaking and not vad_suppressed:
                audio_to_send = effective_audio
            else:
                audio_to_send = bytes([0xFF] * len(effective_audio))
            audio_b64 = base64.b64encode(audio_to_send).decode("ascii")
            await self.session_b.send_recipient_audio(audio_b64)
            self.ring_buffer_b.mark_sent(seq)
            return

        # Legacy path: Server VAD (local_vad_enabled=False)
        if self.echo_gate.in_echo_window:
            silence_b64 = base64.b64encode(effective_audio).decode("ascii")
            await self.session_b.send_recipient_audio(silence_b64)
            return

        # 오디오 에너지 게이트 (무음 필터링)
        if settings.audio_energy_gate_enabled:
            rms = _ulaw_rms(audio_bytes)
            if rms < settings.audio_energy_min_rms:
                return

        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        await self.session_b.send_recipient_audio(audio_b64)
        self.ring_buffer_b.mark_sent(seq)

    # --- Session A 콜백 ---

    async def _on_session_a_tts(self, audio_bytes: bytes) -> None:
        if self.interrupt.is_recipient_speaking:
            return
        is_first = self.echo_gate.on_tts_chunk(len(audio_bytes))
        if is_first:
            # 첫 메시지 레이턴시 측정 (pipeline start → first TTS to Twilio)
            if self.call.call_metrics.first_message_latency_ms == 0.0 and self.call.started_at > 0:
                self.call.call_metrics.first_message_latency_ms = (
                    time.time() - self.call.started_at
                ) * 1000
        await self.twilio_handler.send_audio(audio_bytes)

    async def _on_user_transcription(self, text: str) -> None:
        """사용자 원문 STT → App 채팅창에 표시."""
        await self._app_ws_send(
            WsMessage(
                type=WsMessageType.CAPTION,
                data={
                    "role": "user",
                    "text": text,
                    "direction": "outbound",
                    "language": self.call.source_language,
                },
            )
        )

    async def _on_session_a_caption(self, role: str, text: str) -> None:
        await self._app_ws_send(
            WsMessage(
                type=WsMessageType.CAPTION,
                data={"role": role, "text": text, "direction": "outbound"},
            )
        )

    async def _on_session_a_done(self) -> None:
        self.echo_gate.on_tts_done()
        await self._app_ws_send(
            WsMessage(
                type=WsMessageType.TRANSLATION_STATE,
                data={"state": "done"},
            )
        )
        await self._send_metrics_snapshot()

    # --- Session B 콜백 (큐 기반 순차 스트리밍) ---

    async def _on_session_b_audio(self, audio_bytes: bytes) -> None:
        await self._b_output_queue.put(("audio", audio_bytes))

    async def _on_session_b_caption(self, role: str, text: str) -> None:
        await self._b_output_queue.put(("caption", (role, text)))

    async def _on_session_b_caption_done(self) -> None:
        await self._b_output_queue.put(("caption_done", None))

    async def _on_session_b_original_caption(self, role: str, text: str) -> None:
        await self._b_output_queue.put(("original_caption", (role, text)))

    async def _drain_b_output(self) -> None:
        """Session B 출력 큐 소비자 — 응답 단위로 순차 스트리밍.

        현재 응답의 오디오/캡션은 즉시 전달 (레이턴시 유지).
        응답 경계(caption_done) 도달 시 클라이언트 재생 완료를 추정 대기한 후
        다음 응답을 스트리밍 → 겹침 없이 모든 발화를 순서대로 전달.

        오디오 포맷: pcm16 24kHz (1초 = 48,000 bytes)
        """
        _PCM16_24K_BPS = 48_000  # bytes per second
        try:
            while True:
                item_type, data = await self._b_output_queue.get()

                if item_type == "audio":
                    if self._b_playback_first_chunk_at == 0.0:
                        self._b_playback_first_chunk_at = time.time()
                    self._b_playback_total_bytes += len(data)
                    audio_b64 = base64.b64encode(data).decode("ascii")
                    await self._app_ws_send(
                        WsMessage(
                            type=WsMessageType.RECIPIENT_AUDIO,
                            data={"audio": audio_b64},
                        )
                    )

                elif item_type == "caption":
                    role, text = data
                    await self._app_ws_send(
                        WsMessage(
                            type=WsMessageType.CAPTION_TRANSLATED,
                            data={
                                "role": role,
                                "text": text,
                                "stage": 2,
                                "language": self.call.source_language,
                                "direction": "inbound",
                            },
                        )
                    )

                elif item_type == "original_caption":
                    role, text = data
                    await self._app_ws_send(
                        WsMessage(
                            type=WsMessageType.CAPTION_ORIGINAL,
                            data={
                                "role": role,
                                "text": text,
                                "stage": 1,
                                "language": self.call.target_language,
                                "direction": "inbound",
                            },
                        )
                    )

                elif item_type == "caption_done":
                    await self._app_ws_send(
                        WsMessage(
                            type=WsMessageType.TRANSLATION_STATE,
                            data={"state": "caption_done", "direction": "inbound"},
                        )
                    )
                    # 응답 경계 — 클라이언트 재생 완료 추정 대기
                    if self._b_playback_total_bytes > 0:
                        audio_duration_s = self._b_playback_total_bytes / _PCM16_24K_BPS
                        elapsed = time.time() - self._b_playback_first_chunk_at
                        remaining = max(audio_duration_s - elapsed, 0)
                        if remaining > 0.05:
                            logger.info(
                                "B output queue: waiting %.1fs for playback (%.1fs audio, %.1fs elapsed)",
                                remaining, audio_duration_s, elapsed,
                            )
                            await asyncio.sleep(remaining)
                    self._b_playback_first_chunk_at = 0.0
                    self._b_playback_total_bytes = 0

        except asyncio.CancelledError:
            pass

    # --- Local VAD 콜백 ---

    async def _on_local_vad_speech_start(self) -> None:
        """Local VAD가 수신자 발화 시작을 감지."""
        await self.session_b.notify_speech_started()

    async def _on_local_vad_speech_end(self) -> None:
        """Local VAD가 수신자 발화 종료를 감지."""
        peak_rms = self.local_vad.peak_rms if self.local_vad else 0.0
        await self.session_b.notify_speech_stopped(peak_rms=peak_rms)

    # --- 수신자 발화 감지 ---

    async def _on_recipient_started(self) -> None:
        if self.echo_gate.in_echo_window:
            logger.info("Recipient speech during echo window — breaking echo gate")
            self.echo_gate.on_recipient_speech()

        if not self.call.first_message_sent:
            await self.first_message.on_recipient_speech_detected()
        else:
            await self.interrupt.on_recipient_speech_started()

    async def _on_recipient_stopped(self) -> None:
        await self.context_manager.inject_context(self.dual_session.session_b)
        await self.interrupt.on_recipient_speech_stopped()

    # --- 대화 컨텍스트 ---

    async def _on_turn_complete(self, role: str, text: str) -> None:
        self.context_manager.add_turn(role, text)
        if role == "recipient" and self.call.mode == CallMode.AGENT:
            await self._forward_recipient_to_session_a(text)
        await self._send_metrics_snapshot()

    async def _forward_recipient_to_session_a(self, text: str) -> None:
        self.call.transcript_history.append({"role": "recipient", "text": text})
        if self.session_a.is_generating:
            logger.debug("Waiting for Session A before forwarding recipient translation...")
            await self.session_a.wait_for_done(timeout=5.0)
        logger.info("Agent Mode: forwarding recipient translation to Session A: %s", text[:80])
        await self.session_a.send_user_text(f"[Recipient says]: {text}")

    # --- Guardrail 콜백 ---

    async def _on_guardrail_filler(self, filler_text: str) -> None:
        logger.info("Guardrail: sending filler to Twilio: '%s'", filler_text)
        await self.twilio_handler.send_clear()

    async def _on_guardrail_corrected_tts(self, corrected_text: str) -> None:
        logger.info("Guardrail: re-generating TTS with corrected text: '%s'", corrected_text[:60])
        await self.dual_session.session_a.send_text(corrected_text)

    async def _on_guardrail_event(self, event_data: dict) -> None:
        self.call.guardrail_events_log.append(event_data)
        await self._app_ws_send(
            WsMessage(
                type=WsMessageType.GUARDRAIL_TRIGGERED,
                data=event_data,
            )
        )

    # --- Function Call 결과 ---

    async def _on_function_call_result(self, result: str, data: dict) -> None:
        logger.info("Function call result: %s", result)
        await self._app_ws_send(
            WsMessage(
                type=WsMessageType.CALL_STATUS,
                data={"status": "call_result", "result": result, "data": data},
            )
        )

    # --- App 알림 ---

    async def _notify_app(self, msg: WsMessage) -> None:
        await self._app_ws_send(msg)

    # --- 통화 시간 제한 ---

    async def _call_duration_timer(self) -> None:
        try:
            warning_s = settings.call_warning_ms / 1000
            max_s = settings.max_call_duration_ms / 1000

            await asyncio.sleep(warning_s)
            await self._notify_app(
                WsMessage(
                    type=WsMessageType.CALL_STATUS,
                    data={"status": "warning", "message": "통화 종료까지 2분 남았습니다."},
                )
            )
            await asyncio.sleep(max_s - warning_s)
            await self._notify_app(
                WsMessage(
                    type=WsMessageType.CALL_STATUS,
                    data={"status": "timeout", "message": "최대 통화 시간을 초과하여 자동 종료됩니다."},
                )
            )
            logger.info("Call %s timed out (max duration reached)", self.call.call_id)
        except asyncio.CancelledError:
            pass
