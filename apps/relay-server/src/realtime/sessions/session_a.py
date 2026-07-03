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

from src.config import settings
from src.guardrail.checker import GuardrailChecker, GuardrailLevel
from src.realtime.sessions.hallucination import is_caller_hallucination
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
        context_prune_keep: int = 1,
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
            context_prune_keep: 유지할 최근 대화 아이템 수 (컨텍스트 프루닝)
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
        self._response_expected = False  # 명시적 create_response 호출 시만 True
        # Langfuse: 턴을 transcript.done에서 버퍼링 → response.done(토큰 확정)에서 flush
        self._lf_pending_turn: dict[str, Any] | None = None
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
        # 시스템 생성 메시지 (첫 인사말 등) — transcript에 role="assistant"로 저장
        self._system_message_pending: bool = False

        # Anti-Hallucination: 대화 아이템 트래킹 + 프루닝
        # 매 턴 시작 전 이전 턴의 아이템을 삭제하여 GPT-4o가 오디오에만 집중하도록 함
        self._context_prune_keep = context_prune_keep
        self._conversation_item_ids: list[str] = []

        # Anti-Hallucination: 발화 길이 대비 번역 비율 검증 (chars/sec)
        self._audio_committed_at: float = 0.0

        self._register_handlers()

    def _register_handlers(self) -> None:
        self.session.on("response.output_audio.delta", self._handle_audio_delta)
        self.session.on("response.output_audio_transcript.delta", self._handle_transcript_delta)
        self.session.on("response.output_audio_transcript.done", self._handle_transcript_done)
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

        # 대화 아이템 트래킹 (프루닝용)
        self.session.on("conversation.item.created", self._handle_item_created)

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
        self._response_expected = True
        self._done_event.clear()

    # --- User 음성/텍스트 입력 ---

    async def send_user_audio(self, audio_b64: str) -> None:
        """User 음성 청크를 Session A에 전달 (Relay Mode)."""
        await self.session.send_audio(audio_b64)

    async def commit_user_audio(self) -> None:
        """Client VAD가 발화 종료를 감지하면 오디오를 커밋한다.

        commit_audio()가 내부적으로 response.create를 호출하므로 mark_generating() 처리.
        mark_generating()을 commit_audio() 전에 호출하여 race condition 방지:
        OpenAI가 극히 빠르게 응답 시 _response_expected=False 상태에서 delta 도착 차단.
        """
        self._user_input_at = time.time()
        self._audio_committed_at = time.time()
        # 이전 턴의 대화 아이템 삭제 → GPT-4o가 현재 오디오에만 집중
        await self._prune_conversation_items(keep_last=self._context_prune_keep)
        self.mark_generating()
        await self.session.commit_audio()

    async def send_user_text(self, text: str) -> None:
        """User 텍스트를 Session A에 전달 (Agent Mode / Push-to-Talk).

        send_text()가 내부적으로 response.create를 호출하므로 mark_generating() 처리.
        mark_generating()을 send_text() 전에 호출하여 race condition 방지.
        """
        self._user_input_at = time.time()
        self.mark_generating()
        await self.session.send_text(text)

    def mark_user_input(self) -> None:
        """User 입력 시점을 기록한다 (Text 모드에서 파이프라인이 호출)."""
        self._user_input_at = time.time()

    def set_last_user_stt(self, text: str) -> None:
        """T2V: 사용자 입력 원문을 설정한다 (transcript_bilingual original_text 보존)."""
        self._last_user_stt = text

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
        self._response_expected = False
        self._done_event.set()
        if self._guardrail:
            self._guardrail.reset()
        self._current_transcript = ""
        await self.session.cancel_response()

    # --- 이벤트 핸들러 ---

    async def _handle_audio_delta(self, event: dict[str, Any]) -> None:
        """Session A TTS 오디오 청크 -> Twilio로 전달.

        PRD M-2: Level 3인 경우 오디오를 Twilio로 전달하지 않음.
        예상치 못한 응답(OpenAI 자발적 생성)은 즉시 취소하여 할루시네이션 방지.
        """
        # 예상치 못한 응답 가드: 명시적 create_response 없이 도착한 응답은 즉시 취소
        if not self._response_expected:
            logger.warning("[SessionA] Unexpected response detected — cancelling to prevent hallucination")
            await self.session.cancel_response()
            if self._call:
                self._call.call_metrics.hallucinations_blocked += 1
            return

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

        # Anti-Hallucination: 발화 길이 대비 번역 비율 검증 (chars/sec)
        if self._audio_committed_at > 0:
            speech_s = time.time() - self._audio_committed_at
            if speech_s > 0 and len(transcript) / speech_s > settings.hallucination_max_chars_per_sec:
                logger.warning(
                    "[SessionA] Suspicious translation rate (%.1f chars/%.1fs = %.1f c/s): %s",
                    len(transcript), speech_s, len(transcript) / speech_s, transcript[:80],
                )
                self._audio_committed_at = 0.0
                if self._call:
                    self._call.call_metrics.hallucinations_blocked += 1
                return
            self._audio_committed_at = 0.0

        # 발신자 환각 차단: 구조적 노이즈·토큰 반복·자막 크레딧 (절대 정상 발화 아님).
        # session_b와 동일 철학을 발신자 측에도 적용 (오탐 위험 낮은 패턴만).
        if is_caller_hallucination(transcript):
            logger.warning("[SessionA] Caller hallucination blocked: %s", transcript[:80])
            if self._call:
                self._call.call_metrics.hallucinations_blocked += 1
            return

        logger.info("[SessionA] Translation complete: %s", transcript[:80])

        # 양방향 transcript 저장
        if self._call:
            if self._system_message_pending:
                # 시스템 생성 메시지 (첫 인사말 등) — user 발화가 아님
                self._call.transcript_bilingual.append(
                    TranscriptEntry(
                        role="assistant",
                        original_text=transcript,
                        translated_text=transcript,
                        language=self._call.target_language,
                        timestamp=time.time(),
                    )
                )
                self._system_message_pending = False
            else:
                # User 발화 → 번역
                self._call.transcript_bilingual.append(
                    TranscriptEntry(
                        role="user",
                        original_text=self._last_user_stt or transcript,
                        translated_text=transcript,
                        language=self._call.source_language,
                        timestamp=time.time(),
                    )
                )
                # Langfuse: 턴을 버퍼링 → response.done(토큰 확정)에서 flush.
                guardrail_level = None
                if self._guardrail is not None:
                    lvl = getattr(self._guardrail, "current_level", None)
                    guardrail_level = getattr(lvl, "value", lvl)
                self._lf_pending_turn = {
                    "direction": "caller_to_callee",
                    "original_text": self._last_user_stt or transcript,
                    "translated_text": transcript,
                    "language": self._call.source_language,
                    "stages": {
                        "guardrail_level": guardrail_level,
                        "stt_source": "caller_stt" if self._last_user_stt else "translation_fallback",
                    },
                }
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
        self._response_expected = False
        self._done_event.set()

        # Cost token 추적
        if self._call:
            response = event.get("response", {})
            usage = response.get("usage", {})
            turn_in = turn_out = 0
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
                turn_in = tokens.audio_input + tokens.text_input
                turn_out = tokens.audio_output + tokens.text_output
                logger.debug(
                    "[SessionA] Tokens — audio_in=%d text_in=%d audio_out=%d text_out=%d (total=%d)",
                    tokens.audio_input, tokens.text_input,
                    tokens.audio_output, tokens.text_output,
                    self._call.cost_tokens.total,
                )

            # Langfuse: 토큰이 확정된 지금 버퍼된 턴을 flush한다 (키 없으면 no-op).
            if self._lf_pending_turn is not None:
                from src.observability import tracer
                from src.config import settings

                pend = self._lf_pending_turn
                self._lf_pending_turn = None
                tracer.record_turn(
                    self._call,
                    direction=pend["direction"],
                    original_text=pend["original_text"],
                    translated_text=pend["translated_text"],
                    language=pend["language"],
                    stages=pend["stages"],
                    input_tokens=turn_in,
                    output_tokens=turn_out,
                    model=settings.openai_realtime_model,
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
        self._audio_committed_at = time.time()
        # Server VAD 모드: 이전 턴 아이템 삭제 (response.create 자동 호출 전)
        await self._prune_conversation_items(keep_last=self._context_prune_keep)
        logger.debug("[SessionA] User speech stopped")

    async def _handle_user_transcription(self, event: dict[str, Any]) -> None:
        """User 음성 STT 결과 (Whisper) → App에 원문 자막으로 전달 + 원문 임시 저장."""
        transcript = event.get("transcript", "")
        if not transcript:
            return
        # 발신자 input STT 환각 차단: 선풍기/잡음에서 whisper가 만드는 자막 크레딧·노이즈
        # ("시청해주셔서 감사합니다", "MBC 뉴스..." 등)는 원문 자막으로 띄우지 않고 버린다.
        # (번역 출력은 _handle_transcript_done에서 별도 필터 — 여기는 입력 STT 표시 차단)
        if is_caller_hallucination(transcript):
            logger.warning("[SessionA] Caller STT hallucination filtered: %s", transcript[:80])
            if self._call:
                self._call.call_metrics.hallucinations_blocked += 1
            return
        self._last_user_stt = transcript  # 번역 품질 평가용 원문 저장
        logger.info("[SessionA] User STT: %s", transcript[:80])
        if self._on_user_transcription:
            await self._on_user_transcription(transcript)

    # --- 대화 아이템 트래킹 + 프루닝 ---

    async def _handle_item_created(self, event: dict[str, Any]) -> None:
        """대화 아이템 생성 이벤트 → ID 추적."""
        item = event.get("item", {})
        item_id = item.get("id", "")
        if item_id:
            self._conversation_item_ids.append(item_id)

    async def _prune_conversation_items(self, keep_last: int = 1) -> None:
        """이전 턴의 대화 아이템을 삭제하여 컨텍스트 기반 할루시네이션을 방지한다.

        GPT-4o Realtime은 세션 내 대화 아이템이 누적되면 오디오 대신
        대화 흐름에서 "논리적으로 맞는" 번역을 생성하는 문제가 있다.
        매 턴 시작 전 이전 아이템을 삭제하여 모델이 현재 오디오에만 집중하도록 한다.

        Args:
            keep_last: 유지할 최근 아이템 수 (1 = 최신 컨텍스트 주입 아이템만 유지)
        """
        if len(self._conversation_item_ids) <= keep_last:
            return
        if keep_last > 0:
            to_delete = self._conversation_item_ids[:-keep_last]
            self._conversation_item_ids = self._conversation_item_ids[-keep_last:]
        else:
            to_delete = self._conversation_item_ids[:]
            self._conversation_item_ids = []
        for item_id in to_delete:
            try:
                await self.session.delete_item(item_id)
            except Exception:
                logger.debug("[SessionA] Failed to delete item %s", item_id)
        logger.info("[SessionA] Pruned %d old conversation items (kept %d)", len(to_delete), keep_last)

    async def prune_before_response(self) -> None:
        """T2V/Agent 파이프라인이 create_response 호출 전에 프루닝을 수행한다."""
        await self._prune_conversation_items(keep_last=self._context_prune_keep)

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
