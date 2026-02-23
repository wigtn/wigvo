"""Session A: User -> 수신자 (Outbound Translation).

PRD 3.2:
  Input:  User 음성 (sourceLanguage) 또는 텍스트
  Process: STT -> Translation (source->target) -> Guardrail -> TTS
  Output: targetLanguage 음성 -> Twilio -> 수신자
  Side:   번역된 텍스트 -> App 자막
"""

import asyncio
import base64
import logging
import time
from typing import Any, Callable, Coroutine

from src.guardrail.checker import GuardrailChecker, GuardrailLevel
from src.realtime.sessions.session_manager import RealtimeSession
from src.tools.executor import FunctionExecutor
from src.types import ActiveCall, CallMode, CostTokens, TranscriptEntry

logger = logging.getLogger(__name__)


class SessionAHandler:
    """Session A의 이벤트를 처리한다."""

    def __init__(
        self,
        session: RealtimeSession,
        call: ActiveCall | None = None,
        on_tts_audio: Callable[[bytes], Coroutine] | None = None,
        on_caption: Callable[[str, str], Coroutine] | None = None,
        on_response_done: Callable[[], Coroutine] | None = None,
        guardrail: GuardrailChecker | None = None,
        on_guardrail_filler: Callable[[str], Coroutine] | None = None,
        on_guardrail_corrected_tts: Callable[[str], Coroutine] | None = None,
        on_guardrail_event: Callable[[dict], Coroutine] | None = None,
        on_function_call_result: Callable[[str, dict], Coroutine] | None = None,
        on_transcript_complete: Callable[[str, str], Coroutine] | None = None,
        on_user_transcription: Callable[[str], Coroutine] | None = None,
    ):
        """
        Args:
            session: Session A RealtimeSession
            call: ActiveCall 인스턴스 (transcript/cost 추적용)
            on_tts_audio: TTS 오디오 청크 콜백 (g711_ulaw bytes -> Twilio로 전달)
            on_caption: 자막 콜백 (role, text -> App에 전달)
            on_response_done: 응답 완료 콜백
            guardrail: GuardrailChecker 인스턴스 (None이면 guardrail 비활성화)
            on_guardrail_filler: Level 3 시 필러 텍스트 콜백 ("잠시만요")
            on_guardrail_corrected_tts: Level 3 교정 완료 시 교정된 텍스트 -> 재TTS 콜백
            on_guardrail_event: Guardrail 이벤트 로그 콜백 (App에 디버그 알림)
            on_function_call_result: Function Call 결과 콜백 (Agent Mode, result + args)
            on_transcript_complete: 번역 완료 콜백 (role, text → 대화 컨텍스트 추적)
        """
        self.session = session
        self._call = call
        self._on_tts_audio = on_tts_audio
        self._on_caption = on_caption
        self._on_response_done = on_response_done
        self._on_transcript_complete = on_transcript_complete
        self._on_user_transcription = on_user_transcription
        self._guardrail = guardrail
        self._on_guardrail_filler = on_guardrail_filler
        self._on_guardrail_corrected_tts = on_guardrail_corrected_tts
        self._on_guardrail_event = on_guardrail_event
        self._is_generating = False
        self._done_event = asyncio.Event()
        self._done_event.set()  # 초기 상태: 생성 중 아님

        # 현재 응답의 전체 transcript (Level 2/3 교정용)
        self._current_transcript: str = ""

        # Function Calling (Agent Mode only)
        self._function_executor: FunctionExecutor | None = None
        if call and call.mode == CallMode.AGENT:
            self._function_executor = FunctionExecutor(
                call=call,
                on_call_result=on_function_call_result,
            )
        # 현재 진행 중인 function call의 인자 누적 버퍼
        self._fc_call_id: str = ""
        self._fc_name: str = ""
        self._fc_arguments: str = ""

        # 성능 계측: User 입력 → TTS first chunk 레이턴시
        self._user_input_at: float = 0.0
        self._first_audio_received: bool = False

        # 번역 품질 평가용: User STT 원문 임시 저장
        self._last_user_stt: str = ""

        self._register_handlers()

    def _register_handlers(self) -> None:
        self.session.on("response.audio.delta", self._handle_audio_delta)
        self.session.on("response.audio_transcript.delta", self._handle_transcript_delta)
        self.session.on("response.audio_transcript.done", self._handle_transcript_done)
        self.session.on("response.done", self._handle_response_done)
        self.session.on(
            "input_audio_buffer.speech_started", self._handle_user_speech_started
        )
        self.session.on(
            "input_audio_buffer.speech_stopped", self._handle_user_speech_stopped
        )
        self.session.on(
            "conversation.item.input_audio_transcription.completed",
            self._handle_user_transcription,
        )

        # Function Calling 이벤트 (Agent Mode)
        self.session.on(
            "response.function_call_arguments.delta",
            self._handle_function_call_arguments_delta,
        )
        self.session.on(
            "response.function_call_arguments.done",
            self._handle_function_call_arguments_done,
        )

    @property
    def is_generating(self) -> bool:
        return self._is_generating

    def mark_generating(self) -> None:
        """create_response() 직후 호출하여 즉시 generating 상태로 전환.

        OpenAI response.audio.delta가 ~180ms 후 도착하기 전에 is_generating=True로 설정하여
        후속 create_response() 호출이 wait_for_done()을 정상 대기하도록 한다.
        (conversation_already_has_active_response race condition 방지)
        """
        self._is_generating = True
        self._done_event.clear()

    # --- User 음성/텍스트 입력 ---

    async def send_user_audio(self, audio_b64: str) -> None:
        """User 음성 청크를 Session A에 전달 (Relay Mode)."""
        await self.session.send_audio(audio_b64)

    async def commit_user_audio(self) -> None:
        """Client VAD가 발화 종료를 감지하면 오디오를 커밋한다."""
        self._user_input_at = time.time()
        await self.session.commit_audio()

    async def send_user_text(self, text: str) -> None:
        """User 텍스트를 Session A에 전달 (Agent Mode / Push-to-Talk)."""
        self._user_input_at = time.time()
        await self.session.send_text(text)

    def mark_user_input(self) -> None:
        """User 입력 시점을 기록한다 (Text 모드에서 파이프라인이 호출)."""
        self._user_input_at = time.time()

    async def wait_for_done(self, timeout: float = 5.0) -> bool:
        """응답 생성 완료를 대기한다. 이미 완료 상태면 즉시 반환."""
        try:
            await asyncio.wait_for(self._done_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def cancel(self) -> None:
        """진행 중인 TTS를 중단한다 (Interrupt)."""
        self._is_generating = False
        self._done_event.set()
        if self._guardrail:
            self._guardrail.reset()
        self._current_transcript = ""
        await self.session.cancel_response()

    # --- 이벤트 핸들러 ---

    async def _handle_audio_delta(self, event: dict[str, Any]) -> None:
        """Session A TTS 오디오 청크 -> Twilio로 전달.

        PRD M-2: Level 3인 경우 오디오를 Twilio로 전달하지 않음.
        """
        # 성능 계측: 응답의 첫 번째 TTS 청크 → 번역 레이턴시 기록
        if not self._first_audio_received:
            self._first_audio_received = True
            if self._user_input_at > 0 and self._call:
                latency_ms = (time.time() - self._user_input_at) * 1000
                self._call.call_metrics.session_a_latencies_ms.append(latency_ms)
                self._call.call_metrics.turn_count += 1
                logger.info("[SessionA] Translation latency: %.0fms", latency_ms)
                self._user_input_at = 0.0

        self._is_generating = True
        self._done_event.clear()
        delta_b64 = event.get("delta", "")
        if not delta_b64 or not self._on_tts_audio:
            return

        # Guardrail Level 3: TTS 오디오 차단
        if self._guardrail and self._guardrail.is_blocking:
            return

        audio_bytes = base64.b64decode(delta_b64)
        await self._on_tts_audio(audio_bytes)

    async def _handle_transcript_delta(self, event: dict[str, Any]) -> None:
        """번역된 텍스트 스트리밍 -> Guardrail 검사 + App 자막.

        PRD M-2: 텍스트 델타가 오디오보다 먼저 도착하므로,
        텍스트를 검사하여 오디오 차단 여부를 결정할 수 있다.
        """
        delta = event.get("delta", "")
        if not delta:
            return

        self._current_transcript += delta

        # Guardrail 텍스트 델타 검사
        if self._guardrail:
            prev_level = self._guardrail.current_level
            level = self._guardrail.check_text_delta(delta)

            # Level 3으로 에스컬레이션 시 필러 오디오 재생
            if level == GuardrailLevel.LEVEL_3 and prev_level < GuardrailLevel.LEVEL_3:
                logger.warning(
                    "[SessionA] Guardrail Level 3 triggered — blocking TTS audio"
                )
                if self._on_guardrail_filler:
                    filler = self._guardrail.check_full_text(self._current_transcript).filler_text
                    await self._on_guardrail_filler(filler)

        # 자막은 항상 전달 (Level 3에서도 App에는 자막 표시)
        if self._on_caption:
            await self._on_caption("assistant", delta)

    async def _handle_transcript_done(self, event: dict[str, Any]) -> None:
        """번역 텍스트 완료 -> Level 2/3 교정 처리 + 양방향 transcript 저장."""
        transcript = event.get("transcript", "")
        if not transcript:
            return

        logger.info("[SessionA] Translation complete: %s", transcript[:80])

        # 양방향 transcript 저장 (Session A: user 발화 → 번역)
        # original_text: User STT 원문 (source lang), translated_text: 번역 출력 (target lang)
        if self._call:
            self._call.transcript_bilingual.append(
                TranscriptEntry(
                    role="user",
                    original_text=self._last_user_stt or transcript,
                    translated_text=transcript,
                    language=self._call.source_language,
                    timestamp=time.time(),
                )
            )
            self._last_user_stt = ""  # 사용 후 초기화

        # 대화 컨텍스트 콜백
        if self._on_transcript_complete:
            await self._on_transcript_complete("user", transcript)

        if not self._guardrail:
            return

        level = self._guardrail.current_level

        if level == GuardrailLevel.LEVEL_2:
            # Level 2: 비동기 교정 (TTS는 이미 전달됨, 백그라운드에서 교정 로그)
            asyncio.create_task(self._handle_level2_correction(transcript))

        elif level == GuardrailLevel.LEVEL_3:
            # Level 3: 동기 교정 (TTS는 차단됨, 교정 후 재전송)
            await self._handle_level3_correction(transcript)

    async def _handle_response_done(self, event: dict[str, Any]) -> None:
        """Session A 응답 완료 + cost token 추적."""
        self._is_generating = False
        self._done_event.set()

        # Cost token 추적
        if self._call:
            response = event.get("response", {})
            usage = response.get("usage", {})
            if usage:
                input_details = usage.get("input_token_details", {})
                output_details = usage.get("output_token_details", {})
                tokens = CostTokens(
                    audio_input=input_details.get("audio_tokens", 0),
                    text_input=input_details.get("text_tokens", 0),
                    audio_output=output_details.get("audio_tokens", 0),
                    text_output=output_details.get("text_tokens", 0),
                )
                self._call.cost_tokens.add(tokens)
                logger.debug(
                    "[SessionA] Tokens — audio_in=%d text_in=%d audio_out=%d text_out=%d (total=%d)",
                    tokens.audio_input, tokens.text_input,
                    tokens.audio_output, tokens.text_output,
                    self._call.cost_tokens.total,
                )

        # 다음 응답을 위해 상태 초기화
        self._first_audio_received = False
        if self._guardrail:
            self._guardrail.reset()
        self._current_transcript = ""
        if self._on_response_done:
            await self._on_response_done()

    async def _handle_user_speech_started(self, event: dict[str, Any]) -> None:
        """Server VAD가 User 발화 시작을 감지."""
        logger.debug("[SessionA] User speech started")

    async def _handle_user_speech_stopped(self, event: dict[str, Any]) -> None:
        """Server VAD가 User 발화 종료를 감지."""
        self._user_input_at = time.time()
        logger.debug("[SessionA] User speech stopped")

    async def _handle_user_transcription(self, event: dict[str, Any]) -> None:
        """User 음성 STT 결과 (Whisper) → App에 원문 자막으로 전달 + 원문 임시 저장."""
        transcript = event.get("transcript", "")
        if not transcript:
            return
        self._last_user_stt = transcript  # 번역 품질 평가용 원문 저장
        logger.info("[SessionA] User STT: %s", transcript[:80])
        if self._on_user_transcription:
            await self._on_user_transcription(transcript)

    # --- Function Calling 핸들러 (Agent Mode) ---

    async def _handle_function_call_arguments_delta(self, event: dict[str, Any]) -> None:
        """Function Call 인자가 스트리밍으로 도착한다 (delta).

        OpenAI Realtime API는 function call의 인자를 청크 단위로 전송하므로,
        모든 delta를 누적하여 완성된 JSON을 만든다.
        """
        if not self._function_executor:
            return

        delta = event.get("delta", "")
        call_id = event.get("call_id", "")
        name = event.get("name", "")

        # 새로운 function call 시작 시 버퍼 초기화
        if call_id and call_id != self._fc_call_id:
            self._fc_call_id = call_id
            self._fc_name = name
            self._fc_arguments = ""

        self._fc_arguments += delta

    async def _handle_function_call_arguments_done(self, event: dict[str, Any]) -> None:
        """Function Call 인자 수신 완료 -> 실행 + 결과 전송.

        1. FunctionExecutor로 함수를 실행한다.
        2. 결과를 conversation.item.create (function_call_output)로 OpenAI에 전송한다.
        3. response.create로 AI가 다음 응답을 생성하도록 한다.
        """
        if not self._function_executor:
            return

        call_id = event.get("call_id", self._fc_call_id)
        name = event.get("name", self._fc_name)
        arguments = event.get("arguments", self._fc_arguments)

        if not call_id or not name:
            logger.warning("[SessionA] Function call done but missing call_id or name")
            return

        logger.info("[SessionA] Function call complete: %s (call_id=%s)", name, call_id)

        # 함수 실행
        output = await self._function_executor.execute(
            function_name=name,
            arguments=arguments,
            call_id=call_id,
        )

        # OpenAI에 결과 전송 + 새 응답 요청
        await self.session.send_function_call_output(call_id, output)

        # 버퍼 초기화
        self._fc_call_id = ""
        self._fc_name = ""
        self._fc_arguments = ""

    # --- Guardrail 교정 ---

    async def _handle_level2_correction(self, transcript: str) -> None:
        """Level 2 비동기 교정 (백그라운드)."""
        if not self._guardrail:
            return

        if self._call:
            self._call.call_metrics.guardrail_level2_count += 1

        await self._guardrail.correct_async(transcript)

        # App에 guardrail 이벤트 알림 (디버그용)
        if self._on_guardrail_event:
            await self._on_guardrail_event({
                "level": 2,
                "original": transcript,
            })

    async def _handle_level3_correction(self, transcript: str) -> None:
        """Level 3 동기 교정 (차단 후 교정된 텍스트로 재TTS)."""
        if not self._guardrail:
            return

        if self._call:
            self._call.call_metrics.guardrail_level3_count += 1

        result = await self._guardrail.correct_text(transcript)

        # App에 guardrail 이벤트 알림
        if self._on_guardrail_event:
            await self._on_guardrail_event({
                "level": 3,
                "original": transcript,
                "corrected": result.corrected_text,
                "correction_time_ms": result.correction_time_ms,
            })

        # 교정된 텍스트로 새 TTS 생성 요청
        if result.corrected_text and self._on_guardrail_corrected_tts:
            await self._on_guardrail_corrected_tts(result.corrected_text)
