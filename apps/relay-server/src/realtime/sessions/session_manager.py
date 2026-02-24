"""OpenAI Realtime API 세션 매니저.

Dual Session (A + B)을 생성하고 라이프사이클을 관리한다.

PRD 3.2:
  - Session A: User → 수신자 (source → target 번역)
  - Session B: 수신자 → User (target → source 번역)
"""

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

import websockets
from websockets.asyncio.client import ClientConnection

from src.config import settings
from src.types import CallMode, CommunicationMode, SessionConfig, VadMode

logger = logging.getLogger(__name__)

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"


class RealtimeSession:
    """단일 OpenAI Realtime API WebSocket 세션."""

    def __init__(self, label: str, config: SessionConfig):
        self.label = label
        self.config = config
        self.ws: ClientConnection | None = None
        self.session_id: str = ""
        self._closed = False
        self._handlers: dict[str, list[Callable[..., Coroutine]]] = {}
        self._on_connection_lost: Callable[[], Coroutine] | None = None

    def on(self, event_type: str, handler: Callable[..., Coroutine]) -> None:
        """이벤트 핸들러 등록 (중복 방지)."""
        handlers = self._handlers.setdefault(event_type, [])
        if handler not in handlers:
            handlers.append(handler)

    def set_on_connection_lost(self, handler: Callable[[], Coroutine]) -> None:
        """연결 종료 콜백 등록 (Recovery에서 사용)."""
        self._on_connection_lost = handler

    async def connect(
        self,
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        """OpenAI Realtime API에 WebSocket 연결하고 세션을 설정한다.

        Args:
            system_prompt: 시스템 프롬프트
            tools: Function Calling 도구 목록 (Agent Mode에서만 사용)
        """
        self._closed = False
        url = f"{OPENAI_REALTIME_URL}?model={settings.openai_realtime_model}"
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

        logger.info("[%s] Connecting to OpenAI Realtime API...", self.label)
        last_err: Exception | None = None
        for attempt in range(1, settings.openai_ws_connect_retries + 2):  # 1 + retries
            try:
                self.ws = await websockets.connect(
                    url,
                    additional_headers=headers,
                    open_timeout=settings.openai_ws_connect_timeout_s,
                )
                break
            except Exception as e:
                last_err = e
                max_attempts = settings.openai_ws_connect_retries + 1
                if attempt < max_attempts:
                    logger.warning(
                        "[%s] Connect attempt %d/%d failed: %s — retrying...",
                        self.label, attempt, max_attempts, e,
                    )
                    await asyncio.sleep(1)
                else:
                    raise last_err
        logger.info("[%s] Connected", self.label)

        # 세션 설정
        session_config: dict[str, Any] = {
            "modalities": self.config.modalities,
            "instructions": system_prompt,
            "input_audio_format": self.config.input_audio_format,
            "output_audio_format": self.config.output_audio_format,
            "turn_detection": (
                {
                    "type": "server_vad",
                    "threshold": settings.session_b_vad_threshold,
                    "silence_duration_ms": settings.session_b_vad_silence_ms,
                    "prefix_padding_ms": settings.session_b_vad_prefix_padding_ms,
                    "create_response": False,
                }
                if self.config.vad_mode == VadMode.SERVER
                else None  # VadMode.LOCAL / CLIENT / PUSH_TO_TALK → null (수동 제어)
            ),
        }

        # 2단계 자막: input_audio_transcription 활성화 (PRD 5.4)
        if self.config.input_audio_transcription:
            session_config["input_audio_transcription"] = self.config.input_audio_transcription
            logger.info(
                "[%s] input_audio_transcription enabled: %s",
                self.label,
                self.config.input_audio_transcription,
            )

        # Agent Mode: Function Calling 도구 추가
        if tools:
            session_config["tools"] = tools
            session_config["tool_choice"] = "auto"
            logger.info(
                "[%s] Function Calling enabled with %d tools", self.label, len(tools)
            )

        await self._send(
            {
                "type": "session.update",
                "session": session_config,
            }
        )

    async def send_audio(self, audio_b64: str) -> None:
        """base64로 인코딩된 오디오를 세션에 전송한다."""
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": audio_b64,
            }
        )

    async def send_text(self, text: str) -> None:
        """텍스트 메시지를 세션에 전송한다 (Agent mode)."""
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self._send({"type": "response.create"})

    async def send_text_item(self, text: str) -> None:
        """텍스트 아이템만 생성한다 (response.create는 별도 호출).

        per-response instruction override 패턴에서 사용:
          1. send_text_item(text)  — 아이템 생성
          2. create_response(instructions=...)  — 지시와 함께 응답 요청
        """
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )

    async def create_response(self, instructions: str | None = None) -> None:
        """응답 생성을 요청한다. instructions로 per-response override 가능.

        Args:
            instructions: 이번 응답에만 적용할 지시 (hskim 패턴 이식).
                         None이면 세션 기본 instructions 사용.
        """
        payload: dict[str, Any] = {"type": "response.create"}
        if instructions:
            payload["response"] = {"instructions": instructions}
        await self._send(payload)

    async def commit_audio(self) -> None:
        """오디오 버퍼를 커밋하고 응답을 요청한다 (Client VAD 사용 시)."""
        await self._send({"type": "input_audio_buffer.commit"})
        await self._send({"type": "response.create"})

    async def commit_audio_only(self) -> None:
        """오디오 버퍼를 커밋한다 (response.create 없이, Local VAD에서 사용)."""
        await self._send({"type": "input_audio_buffer.commit"})

    async def clear_input_buffer(self) -> None:
        """입력 오디오 버퍼를 비운다 (에코 잔여물 제거)."""
        await self._send({"type": "input_audio_buffer.clear"})

    async def cancel_response(self) -> None:
        """현재 진행 중인 응답을 취소한다 (Interrupt 처리)."""
        await self._send({"type": "response.cancel"})
        logger.info("[%s] Response cancelled (interrupt)", self.label)

    async def delete_item(self, item_id: str) -> None:
        """대화 아이템을 삭제한다 (컨텍스트 누적 방지)."""
        await self._send({
            "type": "conversation.item.delete",
            "item_id": item_id,
        })

    async def send_context_item(self, text: str) -> None:
        """대화 컨텍스트 아이템을 세션에 추가한다."""
        await self._send({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        })

    async def send_function_call_output(self, call_id: str, output: str) -> None:
        """Function Call의 결과를 OpenAI에 전송한다.

        Args:
            call_id: OpenAI function_call의 call_id
            output: 함수 실행 결과 (JSON 문자열)
        """
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )
        # 결과 전송 후 새 응답 생성 요청
        await self._send({"type": "response.create"})
        logger.info("[%s] Function call output sent for call_id=%s", self.label, call_id)

    async def listen(self) -> None:
        """WebSocket 메시지를 수신하고 등록된 핸들러를 호출한다."""
        if not self.ws:
            return

        try:
            async for raw in self.ws:
                if self._closed:
                    break

                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "session.created":
                    self.session_id = event.get("session", {}).get("id", "")
                    logger.info("[%s] Session created: %s", self.label, self.session_id)

                if event_type == "error":
                    logger.error("[%s] Error: %s", self.label, event)

                # 등록된 핸들러 호출
                for handler in self._handlers.get(event_type, []):
                    try:
                        await handler(event)
                    except Exception:
                        logger.exception(
                            "[%s] Handler error for %s", self.label, event_type
                        )

        except websockets.exceptions.ConnectionClosed:
            logger.info("[%s] Connection closed", self.label)
        finally:
            self._closed = True
            if self._on_connection_lost:
                try:
                    await self._on_connection_lost()
                except Exception:
                    logger.exception("[%s] on_connection_lost handler error", self.label)

    async def close(self) -> None:
        """세션을 종료한다."""
        self._closed = True
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
            logger.info("[%s] Session closed", self.label)

    async def _send(self, data: dict[str, Any]) -> None:
        if self.ws and not self._closed:
            await self.ws.send(json.dumps(data))

    @property
    def is_closed(self) -> bool:
        return self._closed


class DualSessionManager:
    """Session A + Session B를 함께 관리한다."""

    def __init__(
        self,
        mode: CallMode,
        source_language: str,
        target_language: str,
        vad_mode: VadMode = VadMode.SERVER,
        communication_mode: CommunicationMode = CommunicationMode.VOICE_TO_VOICE,
    ):
        self.mode = mode
        self.source_language = source_language
        self.target_language = target_language
        self.vad_mode = vad_mode
        self.communication_mode = communication_mode

        # Local VAD 활성 시 Session B의 VAD 모드를 LOCAL로 설정
        session_b_vad_mode: VadMode
        if settings.local_vad_enabled:
            session_b_vad_mode = VadMode.LOCAL
        else:
            session_b_vad_mode = VadMode.SERVER

        # Session B modalities: communication_mode에 따라 분기
        # - VOICE_TO_VOICE / VOICE_TO_TEXT: ["text", "audio"] — 수신자 번역 TTS를 App에 재생
        # - TEXT_TO_VOICE / FULL_AGENT: ["text"] — Session B는 텍스트 번역만 (TTS 불필요, 토큰 절약)
        #   Local VAD 모드에서는 turn_detection=null이므로 server VAD 비활성화 무관
        if communication_mode in (
            CommunicationMode.TEXT_TO_VOICE,
            CommunicationMode.FULL_AGENT,
        ):
            session_b_modalities = ["text"]
        else:
            session_b_modalities = ["text", "audio"]

        # Session A: User → 수신자 (PRD 3.2 / M-4)
        # Client VAD 시 turn_detection=null (서버가 아닌 클라이언트가 발화 종료 판단)
        self.session_a = RealtimeSession(
            label="SessionA",
            config=SessionConfig(
                mode=mode,
                source_language=source_language,
                target_language=target_language,
                input_audio_format="pcm16",
                output_audio_format="g711_ulaw",  # Twilio로 출력
                vad_mode=vad_mode,
                input_audio_transcription={"model": "whisper-1", "language": source_language},
            ),
        )

        # Session B: 수신자 → User (PRD 3.2 / M-4)
        # Local VAD 활성 시 turn_detection=null → 수동 commit + response.create
        self.session_b = RealtimeSession(
            label="SessionB",
            config=SessionConfig(
                mode=mode,
                source_language=target_language,
                target_language=source_language,
                input_audio_format="g711_ulaw",  # Twilio에서 입력
                output_audio_format="pcm16",  # App으로 출력
                vad_mode=session_b_vad_mode,
                modalities=session_b_modalities,
                input_audio_transcription={"model": "whisper-1", "language": target_language},  # 2단계 자막: 원문 STT + 언어 힌트 (PRD 5.4)
            ),
        )

    async def connect(
        self,
        prompt_a: str,
        prompt_b: str,
        tools_a: list[dict[str, Any]] | None = None,
        tools_b: list[dict[str, Any]] | None = None,
    ) -> None:
        """양쪽 세션을 동시에 연결한다.

        Args:
            prompt_a: Session A 시스템 프롬프트
            prompt_b: Session B 시스템 프롬프트
            tools_a: Session A Function Calling 도구 (Agent Mode)
            tools_b: Session B Function Calling 도구 (Agent Mode)
        """
        try:
            await asyncio.gather(
                self.session_a.connect(prompt_a, tools=tools_a),
                self.session_b.connect(prompt_b, tools=tools_b),
            )
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        """양쪽 세션을 종료한다."""
        await asyncio.gather(
            self.session_a.close(),
            self.session_b.close(),
            return_exceptions=True,
        )

    async def listen_all(self) -> None:
        """양쪽 세션의 이벤트를 동시에 수신한다."""
        await asyncio.gather(
            self.session_a.listen(),
            self.session_b.listen(),
            return_exceptions=True,
        )
