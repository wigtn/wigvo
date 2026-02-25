"""Session B: 수신자 → User (Inbound Translation).

PRD 3.2:
  Input:  수신자 음성 (targetLanguage) via Twilio
  Process: STT → Translation (target→source) → TTS (optional)
  Output: sourceLanguage 텍스트 → App 자막
  Output: sourceLanguage 음성 → App 스피커 (optional)
"""

import asyncio
import base64
import logging
import re
import time
from typing import Any, Callable, Coroutine

from src.config import settings
from src.realtime.sessions.session_manager import RealtimeSession
from src.types import ActiveCall, CostTokens, TranscriptEntry

logger = logging.getLogger(__name__)

# Whisper 한국어 할루시네이션 블랙리스트
# 학습 데이터(방송 뉴스 자막) 편향으로 무음/저에너지 구간에서 반복 생성되는 패턴
_STT_HALLUCINATION_BLOCKLIST = frozenset({
    "MBC 뉴스 이덕영입니다",
    "MBC 뉴스 이덕영입니다.",
    "MBC뉴스 이덕영입니다",
    "시청해주셔서 감사합니다",
    "시청해주셔서 감사합니다.",
    "시청해 주셔서 감사합니다",
    "시청해 주셔서 감사합니다.",
    "영상을 시청해주셔서 감사합니다",
    "끝까지 시청해주셔서 감사합니다",
    "끝까지 시청해주셔서 감사합니다.",
    "끝까지 시청해 주셔서 감사합니다",
    "끝까지 시청해 주셔서 감사합니다.",
    "구독과 좋아요 부탁드립니다",
    "구독과 좋아요 부탁드립니다.",
    "밝혔습니다",
    "밝혔습니다.",
    "전해드립니다",
    "전해드립니다.",
    "플러스포어 픽업",
})

# 동일 토큰 3회 이상 연속 반복 감지 (whisper/gpt-4o 공통 할루시네이션)
_REPETITION_RE = re.compile(r'(\b\S+\b)(\s+\1){2,}', re.IGNORECASE)

# 블록리스트 비교 전 제거할 구두점 (Whisper가 한국어 전사 끝에 추가)
# ! ? (ASCII), 。！？ (CJK 전각), … (말줄임), · (가운데점 — 한국어 합성어 구분자)
_PUNCT_STRIP = str.maketrans("", "", "!?。！？…·")


def _normalize_for_blocklist(text: str) -> str:
    """블록리스트 비교용 정규화: strip + 구두점 제거."""
    return text.strip().translate(_PUNCT_STRIP)

# [unclear] 변형 패턴 — 모델이 지시를 따르지 않고 다른 표현을 사용하는 경우
_UNCLEAR_PHRASES = (
    "[unclear]",
    "unclear",
    "inaudible",
    "cannot hear",
    "can't hear",
    "couldn't hear",
    "not clear",
    "unintelligible",
    "알아들을 수 없",
    "들리지 않",
    "불분명",
    "잘 안 들",
    "잘 들리지",
)


class SessionBHandler:
    """Session B의 이벤트를 처리한다."""

    def __init__(
        self,
        session: RealtimeSession,
        call: ActiveCall | None = None,
        on_translated_audio: Callable[[bytes], Coroutine] | None = None,
        on_caption: Callable[[str, str], Coroutine] | None = None,
        on_original_caption: Callable[[str, str], Coroutine] | None = None,
        on_recipient_speech_started: Callable[[], Coroutine] | None = None,
        on_recipient_speech_stopped: Callable[[], Coroutine] | None = None,
        on_transcript_complete: Callable[[str, str], Coroutine] | None = None,
        on_caption_done: Callable[[], Coroutine] | None = None,
        use_local_vad: bool = False,
        context_prune_keep: int = 1,
    ):
        """
        Args:
            session: Session B RealtimeSession
            call: ActiveCall 인스턴스 (transcript/cost 추적용)
            on_translated_audio: 번역된 음성 콜백 (pcm16 bytes → App에 전달)
            on_caption: 번역 자막 콜백 (role, text → App에 전달) — 2단계 자막 Stage 2
            on_original_caption: 원문 자막 콜백 (role, text → App에 전달) — 2단계 자막 Stage 1
            on_recipient_speech_started: 수신자 발화 시작 콜백 (First Message / Interrupt)
            on_recipient_speech_stopped: 수신자 발화 종료 콜백
            on_transcript_complete: 번역 완료 콜백 (role, text → 대화 컨텍스트 추적)
            use_local_vad: True면 Server VAD 이벤트 미등록 (LocalVAD가 대신 제어)
        """
        self.session = session
        self._call = call
        self._on_translated_audio = on_translated_audio
        self._on_caption = on_caption
        self._on_original_caption = on_original_caption
        self._on_recipient_speech_started = on_recipient_speech_started
        self._on_recipient_speech_stopped = on_recipient_speech_stopped
        self._on_transcript_complete = on_transcript_complete
        self._on_caption_done = on_caption_done
        self._is_recipient_speaking = False
        self._speech_started_count: int = 0
        self._transcript_completed_count: int = 0
        self._output_suppressed = False
        self._pending_output: list[tuple[str, Any]] = []
        self._speech_started_at: float = 0.0  # 파이프라인 지연 계측용
        self._speech_stopped_at: float = 0.0  # processing latency 계측용
        self._use_local_vad = use_local_vad
        self._context_prune_keep = context_prune_keep

        # Response 생성 상태 추적: conversation_already_has_active_response 방지
        self._is_response_active = False
        self._response_done_event = asyncio.Event()
        self._response_done_event.set()  # 초기 상태: 응답 없음

        # 번역 품질 평가용: Recipient STT 원문 임시 저장
        self._last_recipient_stt: str = ""
        # STT latency 임시 저장: E2E와 동시에 기록하여 리스트 정합성 보장
        self._pending_stt_ms: float = 0.0

        # Debounced response creation (create_response=False 모드)
        # VAD speech_stopped 후 일정 시간 대기, 새 speech_started가 없으면 수동 response.create
        self._response_debounce_task: asyncio.Task | None = None
        self._response_debounce_s: float = 0.3  # 300ms debounce

        # Silence timeout: speech_started 후 N초 안에 speech_stopped이 안 오면 강제 response
        # 배경소음이 VAD를 영구 "발화 중" 상태로 만드는 문제 방지
        self._silence_timeout_task: asyncio.Task | None = None
        self._silence_timeout_s: float = 15.0
        self._timeout_forced: bool = False  # timeout이 response를 강제 생성했는지 여부

        # 최소 발화 길이 필터: 너무 짧은 segment는 노이즈로 간주
        self._min_speech_s: float = settings.session_b_min_speech_ms / 1000.0

        # Max speech duration timer: PSTN 배경 소음으로 Server VAD의
        # speech_stopped가 지연되는 문제를 방지. 타이머 초과 시 강제 commit.
        self._max_speech_timer: asyncio.Task | None = None

        # 대화 아이템 트래킹: 컨텍스트 누적에 의한 할루시네이션 방지
        # 매 턴 시작 전 이전 턴의 아이템을 삭제하여 GPT-4o가 오디오에만 집중하도록 함
        self._conversation_item_ids: list[str] = []

        self._register_handlers()

    def _register_handlers(self) -> None:
        self.session.on("response.audio.delta", self._handle_audio_delta)
        self.session.on("response.audio_transcript.delta", self._handle_transcript_delta)
        self.session.on("response.audio_transcript.done", self._handle_transcript_done)
        # modalities=['text'] 전용: response.text.delta/done 핸들러
        self.session.on("response.text.delta", self._handle_text_delta)
        self.session.on("response.text.done", self._handle_text_done)
        self.session.on("response.done", self._handle_response_done)
        # 대화 아이템 트래킹 (프루닝용)
        self.session.on("conversation.item.created", self._handle_item_created)
        # Local VAD 모드에서는 Server VAD 이벤트를 등록하지 않음
        # (turn_detection=null이므로 이 이벤트가 발생하지 않지만, 명시적 비등록)
        if not self._use_local_vad:
            self.session.on(
                "input_audio_buffer.speech_started", self._handle_speech_started
            )
            self.session.on(
                "input_audio_buffer.speech_stopped", self._handle_speech_stopped
            )
        # 2단계 자막 Stage 1: 수신자 원문 STT (PRD 5.4)
        self.session.on(
            "conversation.item.input_audio_transcription.completed",
            self._handle_input_transcription_completed,
        )

    def stop(self) -> None:
        """비동기 타스크를 정리한다 (파이프라인 stop 시 호출)."""
        if self._response_debounce_task and not self._response_debounce_task.done():
            self._response_debounce_task.cancel()
            self._response_debounce_task = None
        self._cancel_silence_timeout()
        if self._max_speech_timer and not self._max_speech_timer.done():
            self._max_speech_timer.cancel()
            self._max_speech_timer = None

    @property
    def output_suppressed(self) -> bool:
        return self._output_suppressed

    @output_suppressed.setter
    def output_suppressed(self, value: bool) -> None:
        self._output_suppressed = value

    @property
    def is_recipient_speaking(self) -> bool:
        return self._is_recipient_speaking

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
        (대화 컨텍스트는 ConversationContextManager가 요약본으로 별도 주입)

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
                logger.debug("[SessionB] Failed to delete item %s", item_id)
        logger.info("[SessionB] Pruned %d old conversation items (kept %d)", len(to_delete), keep_last)

    # --- 수신자 오디오 입력 (Twilio → Session B) ---

    async def send_recipient_audio(self, audio_b64: str) -> None:
        """Twilio에서 받은 수신자 오디오를 Session B에 전달 (g711_ulaw)."""
        await self.session.send_audio(audio_b64)

    # --- Local VAD 콜백 (외부에서 호출) ---

    async def notify_speech_started(self, skip_clear: bool = False) -> None:
        """Local VAD가 수신자 발화 시작을 감지했을 때 호출한다.

        Server VAD의 _handle_speech_started와 동일한 로직을 수행하되,
        발화 시작 전 축적된 무음 오디오를 제거하여 Whisper 할루시네이션을 방지한다.

        Args:
            skip_clear: True면 입력 버퍼 클리어를 건너뛴다.
                post-echo settling breakthrough 시 이미 버퍼를 클리어하고
                큐잉된 오디오를 flush한 뒤 호출하므로 재클리어 방지.
        """
        # 축적된 무음/노이즈 제거 → Whisper 할루시네이션 방지
        if not skip_clear:
            await self.session.clear_input_buffer()

        self._speech_started_count += 1
        self._is_recipient_speaking = True
        self._speech_started_at = time.time()
        self._timeout_forced = False

        # 대기 중인 debounce response.create 취소 (연속 발화)
        if self._response_debounce_task and not self._response_debounce_task.done():
            self._response_debounce_task.cancel()
            logger.info("[SessionB] Local VAD speech started — debounce cancelled (continuous speech)")
        else:
            logger.info("[SessionB] Local VAD speech started")

        # Silence timeout 시작
        self._start_silence_timeout()

        if self._on_recipient_speech_started:
            await self._on_recipient_speech_started()

    async def notify_speech_stopped(self, peak_rms: float = 0.0) -> None:
        """Local VAD가 수신자 발화 종료를 감지했을 때 호출한다.

        Server VAD의 _handle_speech_stopped와 동일한 로직을 수행하되,
        commit_audio_only() 후 create_response()를 호출한다.
        (Server VAD는 자동 commit하지만, Local VAD(turn_detection=null)에서는 수동 commit 필요)

        Args:
            peak_rms: speech 구간의 최대 RMS (0이면 체크 스킵)
        """
        self._is_recipient_speaking = False
        self._speech_stopped_at = time.time()
        self._cancel_silence_timeout()

        # 최소 발화 길이 필터
        speech_duration = time.time() - self._speech_started_at
        if speech_duration < self._min_speech_s:
            logger.info(
                "[SessionB] Local VAD speech stopped — too short (%.0fms < %.0fms), ignoring as noise — clearing buffer",
                speech_duration * 1000,
                self._min_speech_s * 1000,
            )
            # SPEAKING 중 전송된 노이즈 오디오를 제거하여 다음 commit 시 할루시네이션 방지
            await self.session.clear_input_buffer()
            return

        # Peak RMS 품질 필터: 에너지가 약한 speech는 노이즈/잔향으로 간주
        # 실제 발화: peak RMS 300-2000+ (조용한 PSTN 포함), 노이즈/잔향: peak RMS 100-200
        if peak_rms > 0 and peak_rms < settings.session_b_min_peak_rms:
            logger.info(
                "[SessionB] Local VAD speech stopped — weak energy (%.0fms, peak RMS=%.0f < %.0f), ignoring as noise — clearing buffer",
                speech_duration * 1000,
                peak_rms,
                settings.session_b_min_peak_rms,
            )
            await self.session.clear_input_buffer()
            return

        # speech_duration 기록 (노이즈 필터 통과 후)
        if self._call and self._speech_started_at > 0:
            self._call.call_metrics.session_b_speech_durations_ms.append(speech_duration * 1000)

        logger.info("[SessionB] Local VAD speech stopped (%.0fms, peak RMS=%.0f)", speech_duration * 1000, peak_rms)

        # Timeout이 이미 response를 강제 생성했으면 중복 방지
        if self._timeout_forced:
            logger.info("[SessionB] Skipping response — already forced by silence timeout")
            self._timeout_forced = False
            return

        if self._on_recipient_speech_stopped:
            await self._on_recipient_speech_stopped()

        # Debounced response creation (commit + response.create)
        if self._response_debounce_task and not self._response_debounce_task.done():
            self._response_debounce_task.cancel()
        self._response_debounce_task = asyncio.create_task(
            self._debounced_create_response()
        )

    # --- 이벤트 핸들러 ---

    async def clear_input_buffer(self) -> None:
        """에코 잔여물을 제거하기 위해 입력 오디오 버퍼를 비운다."""
        await self.session.clear_input_buffer()

    async def flush_pending_output(self) -> None:
        """억제 해제 후 큐에 저장된 출력을 배출한다."""
        pending = self._pending_output[:]
        self._pending_output.clear()
        for entry_type, data in pending:
            if entry_type == "audio" and self._on_translated_audio:
                await self._on_translated_audio(data)
            elif entry_type == "caption" and self._on_caption:
                await self._on_caption(data[0], data[1])
            elif entry_type == "original_caption" and self._on_original_caption:
                await self._on_original_caption(data[0], data[1])
        if pending:
            logger.debug("[SessionB] Flushed %d pending output items", len(pending))

    def clear_pending_output(self) -> None:
        """억제 중 쌓인 출력을 폐기한다 (에코 환각 제거)."""
        count = len(self._pending_output)
        self._pending_output.clear()
        if count:
            logger.info("[SessionB] Discarded %d pending output items (echo artifacts)", count)

    async def _handle_audio_delta(self, event: dict[str, Any]) -> None:
        """Session B 번역 음성 → App으로 전달 (pcm16). 억제 중이면 큐에 저장."""
        delta_b64 = event.get("delta", "")
        if not delta_b64:
            return
        audio_bytes = base64.b64decode(delta_b64)
        if self._output_suppressed:
            self._pending_output.append(("audio", audio_bytes))
            return
        if self._on_translated_audio:
            await self._on_translated_audio(audio_bytes)

    async def _handle_transcript_delta(self, event: dict[str, Any]) -> None:
        """번역된 텍스트 스트리밍 → App 자막. 억제 중이면 큐에 저장."""
        delta = event.get("delta", "")
        if not delta:
            return
        if self._output_suppressed:
            self._pending_output.append(("caption", ("recipient", delta)))
            return
        if self._on_caption:
            await self._on_caption("recipient", delta)

    async def _handle_text_delta(self, event: dict[str, Any]) -> None:
        """modalities=['text'] 모드: 번역 텍스트 스트리밍 → App 자막.

        response.text.delta는 response.audio_transcript.delta와 동일한
        'delta' 필드를 사용하므로 동일 로직으로 처리한다.
        """
        delta = event.get("delta", "")
        if not delta:
            return
        if self._output_suppressed:
            self._pending_output.append(("caption", ("recipient", delta)))
            return
        if self._on_caption:
            await self._on_caption("recipient", delta)

    async def _handle_transcript_done(self, event: dict[str, Any]) -> None:
        """번역 텍스트 완료 + 양방향 transcript 저장 (억제 중에도 항상 저장)."""
        transcript = event.get("transcript", "")
        if not transcript:
            return
        await self._save_transcript_and_notify(transcript)

    async def _handle_text_done(self, event: dict[str, Any]) -> None:
        """modalities=['text'] 모드: 번역 텍스트 완료.

        Spike 검증 결과: response.text.done은 'text' 필드를 사용한다 (NOT 'transcript').
        """
        text = event.get("text", "")
        if not text:
            return
        await self._save_transcript_and_notify(text)

    async def _save_transcript_and_notify(self, transcript: str) -> None:
        """번역 완료 텍스트를 저장하고 컨텍스트 콜백을 호출한다."""
        # --- Stage 2 Anti-Hallucination 필터 ---

        # 1) [unclear] 변형 패턴 필터링 (모델이 다양한 표현으로 "못 알아들었다"를 출력)
        transcript_lower = transcript.lower()
        if any(phrase in transcript_lower for phrase in _UNCLEAR_PHRASES):
            logger.info("[SessionB] Unclear marker filtered: %s", transcript[:80])
            self._pending_stt_ms = 0.0
            if self._call:
                self._call.call_metrics.hallucinations_blocked += 1
            return

        # 2) STT 블록리스트 재적용 (구두점 정규화: "감사합니다!" → "감사합니다")
        if _normalize_for_blocklist(transcript) in _STT_HALLUCINATION_BLOCKLIST:
            logger.warning("[SessionB] Translation hallucination blocked: %s", transcript[:80])
            self._pending_stt_ms = 0.0
            if self._call:
                self._call.call_metrics.hallucinations_blocked += 1
            return

        # 3) 반복 패턴 감지 (동일 토큰 3회 이상 연속 반복)
        if _REPETITION_RE.search(transcript):
            logger.warning("[SessionB] Repetition hallucination blocked: %s", transcript[:80])
            self._pending_stt_ms = 0.0
            if self._call:
                self._call.call_metrics.hallucinations_blocked += 1
            return

        # 4) 발화 길이 대비 번역 비율 검증 (짧은 입력 + 긴 번역 = 추측)
        if self._speech_started_at > 0 and self._speech_stopped_at > 0:
            speech_s = self._speech_stopped_at - self._speech_started_at
            if speech_s > 0 and len(transcript) / speech_s > settings.hallucination_max_chars_per_sec:
                logger.warning(
                    "[SessionB] Suspicious translation rate (%.1f chars/%.1fs = %.1f c/s): %s",
                    len(transcript), speech_s, len(transcript) / speech_s, transcript[:80],
                )
                self._pending_stt_ms = 0.0
                if self._call:
                    self._call.call_metrics.hallucinations_blocked += 1
                return

        self._transcript_completed_count += 1
        if self._call:
            self._call.call_metrics.vad_false_triggers = max(0, self._speech_started_count - self._transcript_completed_count)

        if self._speech_started_at > 0:
            e2e_ms = (time.time() - self._speech_started_at) * 1000
            logger.info(
                "[SessionB] Translation complete (e2e=%.0fms): %s",
                e2e_ms, transcript[:80],
            )
            if self._call:
                self._call.call_metrics.session_b_e2e_latencies_ms.append(e2e_ms)
                # STT latency를 E2E와 동시에 기록 — 리스트 인덱스 정합성 보장
                self._call.call_metrics.session_b_stt_latencies_ms.append(
                    self._pending_stt_ms if self._pending_stt_ms > 0 else e2e_ms
                )
                self._pending_stt_ms = 0.0
                self._call.call_metrics.turn_count += 1
                # processing latency: speech_stopped → 번역 완료 (STT와 독립적)
                if self._speech_stopped_at > 0:
                    proc_ms = (time.time() - self._speech_stopped_at) * 1000
                    self._call.call_metrics.session_b_processing_latencies_ms.append(proc_ms)
        else:
            logger.info("[SessionB] Translation complete: %s", transcript[:80])

        # 양방향 transcript 저장 — 억제 상태와 무관하게 항상 저장
        # original_text: Recipient STT 원문 (target lang), translated_text: 번역 출력 (source lang)
        if self._call:
            self._call.transcript_bilingual.append(
                TranscriptEntry(
                    role="recipient",
                    original_text=self._last_recipient_stt or transcript,
                    translated_text=transcript,
                    language=self._call.target_language,
                    timestamp=time.time(),
                )
            )
            self._last_recipient_stt = ""  # 사용 후 초기화

        # 대화 컨텍스트 콜백
        if self._on_transcript_complete:
            await self._on_transcript_complete("recipient", transcript)

        # 번역 완료 알림 (클라이언트 streamingRef 리셋용)
        if self._on_caption_done:
            await self._on_caption_done()

        # 타임스탬프 리셋
        self._speech_started_at = 0.0
        self._speech_stopped_at = 0.0
        self._pending_stt_ms = 0.0

    async def _handle_response_done(self, event: dict[str, Any]) -> None:
        """Session B 응답 완료 + cost token 추적."""
        self._is_response_active = False
        self._response_done_event.set()

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
                    "[SessionB] Tokens — audio_in=%d text_in=%d audio_out=%d text_out=%d (total=%d)",
                    tokens.audio_input, tokens.text_input,
                    tokens.audio_output, tokens.text_output,
                    self._call.cost_tokens.total,
                )

    async def _handle_speech_started(self, event: dict[str, Any]) -> None:
        """Server VAD가 수신자 발화 시작을 감지.

        이 이벤트는 First Message Strategy (PRD 3.4)와
        Interrupt 처리 (PRD 3.6)의 핵심 트리거다.

        Max speech timer를 시작하여, PSTN 배경 소음으로 speech_stopped가
        지연되는 경우에도 max_speech_duration_s 이내에 번역이 시작되도록 한다.
        """
        self._is_recipient_speaking = True
        self._speech_started_count += 1
        self._speech_started_at = time.time()
        self._timeout_forced = False  # 새 발화 시작 → timeout 플래그 초기화

        # 대기 중인 debounce response.create 취소 (연속 발화)
        if self._response_debounce_task and not self._response_debounce_task.done():
            self._response_debounce_task.cancel()
            logger.info("[SessionB] Recipient speech started — debounce cancelled (continuous speech)")
        else:
            logger.info("[SessionB] Recipient speech started")

        # Silence timeout 시작: speech_stopped이 안 오면 강제 response
        self._start_silence_timeout()

        if self._on_recipient_speech_started:
            await self._on_recipient_speech_started()

    async def _handle_speech_stopped(self, event: dict[str, Any]) -> None:
        """Server VAD가 수신자 발화 종료를 감지.

        create_response=False 모드: debounce 후 수동 response.create 호출.
        """
        self._is_recipient_speaking = False
        self._speech_stopped_at = time.time()
        self._cancel_silence_timeout()

        # 최소 발화 길이 필터: 너무 짧은 segment는 노이즈로 간주
        speech_duration = time.time() - self._speech_started_at
        if speech_duration < self._min_speech_s:
            logger.info(
                "[SessionB] Recipient speech stopped — too short (%.0fms < %.0fms), ignoring as noise",
                speech_duration * 1000,
                self._min_speech_s * 1000,
            )
            return

        # speech_duration 기록 (노이즈 필터 통과 후)
        if self._call and self._speech_started_at > 0:
            self._call.call_metrics.session_b_speech_durations_ms.append(speech_duration * 1000)

        logger.info("[SessionB] Recipient speech stopped (%.0fms)", speech_duration * 1000)

        # Timeout이 이미 response를 강제 생성했으면 중복 방지
        if self._timeout_forced:
            logger.info("[SessionB] Skipping response — already forced by silence timeout")
            self._timeout_forced = False
            return

        if self._on_recipient_speech_stopped:
            await self._on_recipient_speech_stopped()

        # Debounced response creation
        if self._response_debounce_task and not self._response_debounce_task.done():
            self._response_debounce_task.cancel()
        self._response_debounce_task = asyncio.create_task(
            self._debounced_create_response()
        )

    async def _debounced_create_response(self) -> None:
        """debounce 대기 후 응답 생성을 요청한다.

        Server VAD 모드: speech_stopped 시 자동 commit → response.create만 호출.
        Local VAD 모드: turn_detection=null이므로 수동 commit_audio_only() 후 response.create.

        conversation_already_has_active_response 방지:
        이전 응답이 아직 생성 중이면 완료 대기 후 새 응답을 생성한다.
        """
        try:
            await asyncio.sleep(self._response_debounce_s)

            # 이전 응답 생성 중이면 완료 대기 (최대 5초)
            if self._is_response_active:
                logger.info("[SessionB] Waiting for previous response to complete before new create_response...")
                try:
                    await asyncio.wait_for(self._response_done_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("[SessionB] Previous response wait timeout (5s) — proceeding anyway")

            # 이전 턴의 대화 아이템 삭제 → GPT-4o가 현재 오디오에만 집중
            # T2V: keep_last=0 (컨텍스트 기반 추측 방지), V2V: keep_last=1
            await self._prune_conversation_items(keep_last=self._context_prune_keep)

            if self._use_local_vad:
                # clear_input_buffer는 notify_speech_started에서 이미 호출됨 (line 261)
                # timeout 경로와 달리 여기서는 중복 clear 불필요
                logger.info(
                    "[SessionB] Debounce complete (%.0fms) — committing audio + creating response (local VAD)",
                    self._response_debounce_s * 1000,
                )
                await self.session.commit_audio_only()
            else:
                logger.info(
                    "[SessionB] Debounce complete (%.0fms) — creating response",
                    self._response_debounce_s * 1000,
                )

            self._is_response_active = True
            self._response_done_event.clear()
            await self.session.create_response()
        except asyncio.CancelledError:
            logger.debug("[SessionB] Debounced response creation cancelled")
        except Exception:
            logger.exception("[SessionB] Error in debounced response creation")

    # --- Silence Timeout (VAD stuck 방지) ---

    def _start_silence_timeout(self) -> None:
        self._cancel_silence_timeout()
        self._silence_timeout_task = asyncio.create_task(
            self._silence_timeout_handler()
        )

    def _cancel_silence_timeout(self) -> None:
        if self._silence_timeout_task and not self._silence_timeout_task.done():
            self._silence_timeout_task.cancel()
            self._silence_timeout_task = None

    async def _silence_timeout_handler(self) -> None:
        """speech_stopped이 타임아웃 내에 안 오면 강제로 response.create."""
        try:
            await asyncio.sleep(self._silence_timeout_s)
            logger.warning(
                "[SessionB] Silence timeout (%.0fs) — VAD stuck, forcing response creation",
                self._silence_timeout_s,
            )
            self._is_recipient_speaking = False
            self._speech_stopped_at = time.time()  # chars/sec 필터가 작동하도록 설정
            self._timeout_forced = True

            # 이전 응답 생성 중이면 완료 대기 (최대 5초)
            if self._is_response_active:
                logger.info("[SessionB] Timeout: waiting for previous response to complete...")
                try:
                    await asyncio.wait_for(self._response_done_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("[SessionB] Timeout: previous response wait timeout (5s) — proceeding anyway")

            # 이전 턴 아이템 삭제 (debounced_create_response와 동일)
            await self._prune_conversation_items(keep_last=self._context_prune_keep)

            if self._use_local_vad:
                # 15초 축적된 노이즈 제거 후 commit (할루시네이션 방지)
                await self.session.clear_input_buffer()
                await self.session.commit_audio_only()

            self._is_response_active = True
            self._response_done_event.clear()
            await self.session.create_response()

            if self._on_recipient_speech_stopped:
                await self._on_recipient_speech_stopped()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[SessionB] Error in silence timeout handler")

    # --- 2단계 자막 Stage 1: 원문 STT (PRD 5.4) ---

    async def _handle_input_transcription_completed(self, event: dict[str, Any]) -> None:
        """수신자 원문 STT 완료 → 즉시 원문 자막 전송 (2단계 자막 Stage 1).

        OpenAI input_audio_transcription이 활성화된 경우,
        수신자 발화의 원문 텍스트(예: 한국어)를 App에 즉시 전달한다.
        번역 텍스트(Stage 2)는 response.audio_transcript.done에서 별도로 전송된다.
        억제 중이면 큐에 저장하여 나중에 배출한다.
        """
        transcript = event.get("transcript", "")
        if not transcript:
            return
        self._last_recipient_stt = transcript  # 번역 품질 평가용 원문 저장
        # Whisper STT 할루시네이션 필터링 (구두점 정규화: "감사합니다!" → "감사합니다")
        if _normalize_for_blocklist(transcript) in _STT_HALLUCINATION_BLOCKLIST:
            logger.warning("[SessionB] STT hallucination blocked: %s", transcript[:80])
            if self._call:
                self._call.call_metrics.hallucinations_blocked += 1
            return
        if self._output_suppressed:
            self._pending_output.append(("original_caption", ("recipient", transcript)))
            return
        if self._speech_started_at > 0:
            stt_ms = (time.time() - self._speech_started_at) * 1000
            logger.info("[SessionB] Original STT (Stage 1, stt=%.0fms): %s", stt_ms, transcript[:80])
            # STT latency를 임시 저장 — E2E 기록 시 함께 append하여 리스트 정합성 보장
            self._pending_stt_ms = stt_ms
            if self._call and self._speech_stopped_at > 0:
                after_stop_ms = (time.time() - self._speech_stopped_at) * 1000
                if after_stop_ms > 0:
                    self._call.call_metrics.session_b_stt_after_stop_ms.append(after_stop_ms)
        else:
            logger.info("[SessionB] Original STT (Stage 1): %s", transcript[:80])
        if self._on_original_caption:
            await self._on_original_caption("recipient", transcript)
