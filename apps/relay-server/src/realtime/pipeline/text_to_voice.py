"""TextToVoicePipeline — 텍스트 입력 → 음성 출력 파이프라인.

TEXT_TO_VOICE: User 텍스트 → Session A TTS → Twilio + 수신자 음성 → Session B 텍스트 번역 → App

VoiceToVoicePipeline과의 핵심 차이:
  - User 입력: 텍스트 (audio 입력 무시)
  - Session A: per-response instruction override로 번역만 강제
  - Session B: modalities=['text'] (TTS 생략, 토큰 절약)
  - Dynamic Energy Threshold: echo window 중 높은 에너지 임계값으로 에코 필터링
    에코(스피커→마이크 감쇠 20-30dB): RMS ~100-400
    수신자 직접 발화(감쇠 없음): RMS ~500-2000+
  - First Message: exact utterance 패턴 (AI 확장 방지)

hskim-wigvo-test 이식 포인트:
  - sendTextToSessionA → handle_user_text (per-response instruction override)
  - sendExactUtteranceToSessionA → FirstMessageHandler(use_exact_utterance=True)
"""

import asyncio
import base64
import logging
import time
from typing import Any, Callable, Coroutine

from src.config import settings
from src.guardrail.checker import GuardrailChecker
from src.prompt.templates import TYPING_FILLER_TEMPLATES
from src.realtime.audio_utils import ulaw_rms as _ulaw_rms
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
    WsMessage,
    WsMessageType,
)

logger = logging.getLogger(__name__)


class TextToVoicePipeline(BasePipeline):
    """텍스트 입력 → 음성 출력 파이프라인 (per-response instruction override)."""

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

        # 텍스트 전송 직렬화 Lock (race condition 방지)
        self._text_send_lock = asyncio.Lock()

        # 타이핑 필러: 통화당 1회만 전송
        self._typing_filler_sent = False

        # Per-response instruction override (hskim 이식)
        self._strict_relay_instruction = (
            f"Translate the user's message from {call.source_language} to "
            f"{call.target_language} and speak ONLY that translated sentence. "
            f"Do NOT answer the question, do NOT add any extra words, "
            f"do NOT ask follow-up questions."
        )

        # Guardrail (PRD Phase 4 / M-2)
        self.guardrail: GuardrailChecker | None = None
        if settings.guardrail_enabled:
            self.guardrail = GuardrailChecker(
                target_language=call.target_language,
                enabled=True,
            )

        # 대화 컨텍스트 매니저 (번역 일관성)
        self.context_manager = ConversationContextManager()

        # Session A 핸들러: User text → 번역 TTS → Twilio
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
        )

        # Session B 핸들러: 수신자 음성 → 텍스트 번역 → App
        # modalities=['text'] — DualSessionManager가 communication_mode 기반으로 설정
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

        # First Message: exact utterance 패턴 (AI 확장 방지)
        self.first_message = FirstMessageHandler(
            call=call,
            session_a=self.session_a,
            on_notify_app=self._notify_app,
            use_exact_utterance=True,
        )

        # Interrupt 핸들러
        self.interrupt = InterruptHandler(
            session_a=self.session_a,
            twilio_handler=twilio_handler,
            on_notify_app=self._notify_app,
            call=call,
        )

        # Echo Gate Manager (TTS 에코 차단)
        self.echo_gate = EchoGateManager(
            session_b=self.session_b,
            local_vad=self.local_vad,
            call_metrics=self.call.call_metrics,
            echo_margin_s=0.5,
            max_echo_window_s=None,
        )

        # Interrupt debounce: 노이즈에 의한 즉시 TTS 취소 방지 (400ms 대기 후 확인)

        # Ring Buffer: Session B만 (User audio 없으므로 A 불필요)
        self.ring_buffer_b = AudioRingBuffer(
            capacity=settings.ring_buffer_capacity_slots,
        )

        # Recovery: Session B만 (Session A는 텍스트 입력이므로 audio recovery 불필요)
        tools_a = get_tools_for_mode(call.mode) if call.mode == CallMode.AGENT else None
        self.recovery_a = SessionRecoveryManager(
            session=dual_session.session_a,
            ring_buffer=AudioRingBuffer(capacity=1),  # placeholder (audio recovery 불사용)
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
        self.recovery_a.start_monitoring()
        self.recovery_b.start_monitoring()
        logger.info("TextToVoicePipeline started for call %s", self.call.call_id)

    async def stop(self) -> None:
        if self._call_timer_task:
            self._call_timer_task.cancel()
            try:
                await self._call_timer_task
            except asyncio.CancelledError:
                pass

        await self.echo_gate.stop()

        self._cancel_db_save_task()

        if self.local_vad:
            self.local_vad.reset()

        self.session_b.stop()
        await self.recovery_a.stop()
        await self.recovery_b.stop()
        logger.info("TextToVoicePipeline stopped for call %s", self.call.call_id)

    # --- User App -> Session A (텍스트 입력) ---

    async def handle_user_audio(self, audio_b64: str) -> None:
        """TextToVoice 모드에서 audio 입력은 무시한다."""
        logger.debug("TextToVoice: ignoring audio input (text-only mode)")

    async def handle_user_audio_commit(self) -> None:
        """TextToVoice 모드에서 audio commit은 무시한다."""
        logger.debug("TextToVoice: ignoring audio commit (text-only mode)")

    async def handle_typing_started(self) -> None:
        """사용자 타이핑 시작 → 수신자에게 '잠시만 기다려주세요' TTS 전송."""
        if self._typing_filler_sent:
            return
        self._typing_filler_sent = True

        filler = TYPING_FILLER_TEMPLATES.get(
            self.call.target_language,
            TYPING_FILLER_TEMPLATES["en"],
        )
        logger.info("Typing filler → recipient: %s", filler)

        async with self._text_send_lock:
            if self.session_a.is_generating:
                await self.session_a.wait_for_done(timeout=3.0)
            # send_text_item 없이 create_response만 사용 (이슈 1)
            # send_text_item은 대화 히스토리에 user 메시지로 추가되어
            # 번역기 system prompt가 이를 사용자 발화로 해석하는 문제 방지
            await self.dual_session.session_a.create_response(
                instructions=f'Say exactly this sentence and nothing else: "{filler}"',
            )
            # 즉시 generating 상태로 전환 — OpenAI response.audio.delta 도착 전에
            # handle_user_text()가 is_generating을 정상 감지하도록 함
            self.session_a.mark_generating()

    async def handle_user_text(self, text: str) -> None:
        """텍스트 입력을 Session A에 per-response instruction override로 전달한다.

        hskim 이식: sendTextToSessionA 패턴
          1. conversation.item.create (텍스트 아이템)
          2. response.create + response.instructions (번역만 강제)

        Lock으로 직렬화하여 여러 텍스트가 동시에 response.create를 호출하는
        race condition (conversation_already_has_active_response)을 방지한다.
        """
        self._typing_filler_sent = False
        async with self._text_send_lock:
            self.call.transcript_history.append({"role": "user", "text": text})
            self.session_a.mark_user_input()

            if self.session_a.is_generating:
                logger.debug("Waiting for Session A to finish before sending text...")
                await self.session_a.wait_for_done(timeout=5.0)

            await self._app_ws_send(
                WsMessage(
                    type=WsMessageType.TRANSLATION_STATE,
                    data={"state": "processing"},
                )
            )

            await self.context_manager.inject_context(self.dual_session.session_a)

            # Per-response instruction override (Relay mode)
            # Agent mode에서는 기본 instructions 사용
            if self.call.mode == CallMode.RELAY:
                await self.dual_session.session_a.send_text_item(text)
                await self.dual_session.session_a.create_response(
                    instructions=self._strict_relay_instruction,
                )
                # 즉시 generating 상태로 전환 (race condition 방지)
                self.session_a.mark_generating()
            else:
                await self.session_a.send_user_text(text)

    # --- Twilio -> Session B ---

    async def handle_twilio_audio(self, audio_bytes: bytes) -> None:
        """수신자 음성을 Session B에 전달한다.

        TTS echo 방지 (Silence Injection + Energy-based Gate Breaking):
        - Echo window 중: Twilio 오디오를 무음으로 대체하여 Session B에 전송
        - 단, 높은 에너지(수신자 직접 발화)를 감지하면 echo gate를 즉시 해제
        - 에코(스피커→마이크 감쇠): RMS ~100-400 → 무음 처리
        - 수신자 직접 발화(감쇠 없음): RMS ~500-2000+ → echo gate 해제
        """
        seq = self.ring_buffer_b.write(audio_bytes)

        if self.recovery_b.is_recovering or self.recovery_b.is_degraded:
            return

        # Echo Gate: echo window 중 무음 대체 또는 에너지 기반 break
        effective_audio = self.echo_gate.filter_audio(audio_bytes)

        # Local VAD 경로: VAD 상태에 따라 실제 오디오 또는 무음을 Session B에 전송
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
        """Session A TTS 출력을 Twilio에 전달 + echo window 활성화 + 오디오 길이 추적."""
        is_first = self.echo_gate.on_tts_chunk(len(audio_bytes))
        if is_first:
            # 첫 메시지 레이턴시 측정 (pipeline start → first TTS to Twilio)
            if self.call.call_metrics.first_message_latency_ms == 0.0 and self.call.started_at > 0:
                self.call.call_metrics.first_message_latency_ms = (
                    time.time() - self.call.started_at
                ) * 1000
        await self.twilio_handler.send_audio(audio_bytes)

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

    # --- Session B 콜백 ---

    async def _on_session_b_audio(self, audio_bytes: bytes) -> None:
        """Session B modalities=['text']이므로 audio 콜백은 발생하지 않지만, 안전장치로 유지."""
        logger.debug("TextToVoice: unexpected Session B audio (modalities should be ['text'])")

    async def _on_session_b_caption(self, role: str, text: str) -> None:
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

    async def _on_session_b_caption_done(self) -> None:
        """Session B 번역 완료 → 클라이언트에 스트림 종료 신호 전송."""
        await self._app_ws_send(
            WsMessage(
                type=WsMessageType.TRANSLATION_STATE,
                data={"state": "caption_done", "direction": "inbound"},
            )
        )

    async def _on_session_b_original_caption(self, role: str, text: str) -> None:
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

    # --- 수신자 발화 감지 ---

    async def _on_recipient_started(self) -> None:
        if self.echo_gate.in_echo_window:
            logger.info("Recipient speech during echo window — breaking echo gate")
            self.echo_gate.on_recipient_speech()

        if not self.call.first_message_sent:
            await self.first_message.on_recipient_speech_detected()
        else:
            # 즉시 interrupt: LocalVAD 필터(RMS gate + Silero 3프레임)를 통과한 speech는
            # 충분히 신뢰할 수 있으므로 debounce 없이 즉시 TTS 중단
            await self.interrupt.on_recipient_speech_started()

    async def _on_recipient_stopped(self) -> None:
        await self.context_manager.inject_context(self.dual_session.session_b)
        await self.interrupt.on_recipient_speech_stopped()

    # --- Local VAD 콜백 ---

    async def _on_local_vad_speech_start(self) -> None:
        """Local VAD가 수신자 발화 시작을 감지."""
        await self.session_b.notify_speech_started()

    async def _on_local_vad_speech_end(self) -> None:
        """Local VAD가 수신자 발화 종료를 감지."""
        peak_rms = self.local_vad.peak_rms if self.local_vad else 0.0
        await self.session_b.notify_speech_stopped(peak_rms=peak_rms)

    # --- 대화 컨텍스트 ---

    async def _on_turn_complete(self, role: str, text: str) -> None:
        self.context_manager.add_turn(role, text)
        await self._send_metrics_snapshot()

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
