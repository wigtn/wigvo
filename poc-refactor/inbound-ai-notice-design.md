# 인바운드 AI 통역 고지 안내문 설계 (PoC)

> **Status**: 설계 승인 대기 · 2026-07-16 · 브레인스토밍 산출물
> **관련**: `PRD_poc-server-refactor.md` FR-6.1a(PendingMediaHandler 대기 미디어), 법무 요건표("AI 통역 고지·동의" 인·아웃바운드 필요 · "착신 직후 고지·거부 흐름" 인바운드 별도 설계 필요)
> **선행 상태**: 인바운드 착신→dispatch→미디어스트림→홀드음까지 실전화 검증 완료(2026-07-16). 웹부스(에이전트 pickup UI) 미배포.

## 1. 배경 / 문제

법무 요건상 **AI 통역 사용은 착신 직후 caller에게 고지**해야 하고(개인정보 처리·국외이전 포함), **거부 흐름**이 있어야 한다(요건표: "착신 직후 고지·거부 흐름 = 인바운드 별도 설계 필요").

현재 인바운드 구현엔 이 고지가 **없다**. 고지는 pickup 후 AI 세션(DualSession)이 말하도록 되어 있는데, 웹부스가 없어 pickup이 일어나지 않으면 caller는 정적 홀드음만 듣다 타임아웃된다. 즉 **착신 직후 고지 요건이 충족되지 않는다.**

## 2. 결정 (PoC 스코프)

- **위치**: 착신 후 `WAITING_FOR_AGENT` 진입 직후, **홀드음 루프 시작 전에 고지 안내문을 1회 재생**. pickup·DualSession과 무관하게 항상 재생됨(지연 생성 원칙 유지 — 고지는 정적 오디오라 OpenAI 세션 불필요).
- **동의 방식 = 암묵 동의(A)**: 고지 후 **계속 대기 = 동의**, **끊기 = 거부**. DTMF/음성 능동 동의 없음(후속).
- **언어 = 영어 고정**: caller(외국인)에게 항상 영어 고지. 언어 감지·다국어는 후속.
- **자산 = 정적 pre-recorded µ-law 파일**: 홀드 chime과 동일 방식. 런타임 TTS 아님(비용·지연 0).

## 3. 흐름

```
착신 → POST /twilio/incoming → dispatch(RINGING) → <Connect><Stream>
  → media-stream WS "start" 이벤트
     → dispatch_service.mark_waiting() → WAITING_FOR_AGENT
     → [영어 고지 안내문 1회 재생]        ← 신규
     → 홀드음 루프(기존, 무한 반복)
  → (에이전트 claim/pickup) → handoff → AudioRouter → CONNECTED → 통역
  → caller hangup / timeout → cleanup(기존)
```

## 4. 구현

**4.1 `PendingMediaHandler` (`src/inbound/media.py`) — 대기 오디오 재구성**

현재 `handle_message`의 "start" 분기는 `mark_waiting` 후 `self._start_hold()`(= `_hold_loop` 태스크)를 띄운다. 이를 **고지 1회 → 홀드 무한**의 단일 태스크로 재구성한다:

- 신규 `_notice_audio()` (lru_cache): `static/audio/inbound-notice-en.ulaw.b64`를 로드·검증(홀드의 `_hold_audio()`와 동일 로직: whitespace strip → b64decode(validate) → `len % 160 == 0`).
- 대기 태스크를 `_hold_loop` → **`_waiting_loop`**로 확장:
  1. 고지 프레임을 20ms 페이싱으로 **1회** 송출(`send_audio`), 각 프레임마다 `self.twilio.is_closed` 체크 → 중단 시 return.
  2. 고지 완료 시 `logger.info("inbound AI-interpretation notice delivered call=%s", call_id)` (고지 전달 증적).
  3. 이후 기존 홀드 루프(200ms burst + `_HOLD_PAUSE_S` 정적, 무한).
- `_start_hold`/`_stop_hold` 태스크 핸들 관리·`handoff`의 `_stop_hold()` 취소 로직은 그대로 재사용(태스크가 고지+홀드를 담당하도록 이름/내용만 조정).

**4.2 고지 문구 (초안 — 법무 승인 전 자리표시자)**

> "This call will be assisted by AI-powered interpretation. Your voice may be processed and transferred abroad for translation. Please stay on the line to be connected to an agent, or hang up now to decline."

- 개인정보 처리·국외이전 고지 포함(요건표 반영). **최종 문구는 법무 승인 후 확정 → 자산 재생성.**

**4.3 자산 생성**

- 스크립트 `scripts/gen_inbound_notice.py`(또는 기존 홀드 자산 생성 방식 재사용): 영어 문구 → TTS(예: OpenAI TTS) WAV → 8kHz mono µ-law 변환 → base64 → `static/audio/inbound-notice-en.ulaw.b64` 커밋. 문구 변경 시 재실행.

## 5. 동의/거부 처리 (암묵)

- **동의**: 고지 재생 후에도 caller가 대기하면 동의로 간주(통화 녹음 고지와 동일한 방어 가능한 관행).
- **거부**: 고지 중/후 caller hangup → 기존 `run()`의 `twilio_stopped` 경로가 dispatch 종료·cleanup. 별도 분기 없음.
- **증적**: 4.1의 "notice delivered" 로그 + dispatch 종료 상태로 최소 감사 추적. (DB consent 컬럼·타임스탬프는 후속.)

## 6. 테스트

- **유닛(`tests/test_inbound_media.py` 확장)**: "start" 이벤트 후 `PendingMediaHandler`가 **고지 프레임을 먼저, 그 다음 홀드 프레임**을 송출함을 검증(프레임 순서/카운트). 고지 중 hangup 시 홀드로 안 넘어가고 정리됨을 검증.
- **자산 유효성**: `_notice_audio()`가 유효한 µ-law(160의 배수, 비무음)로 로드됨을 검증.
- 전체 스위트 `DATABASE_URL="" uv run pytest -q` 그린 유지.

## 7. 스코프 밖 (YAGNI · 후속)

- DTMF/음성 능동 동의·거부(B/C) · 언어 감지·다국어 고지 · DB consent 컬럼 · tenant별 고지 커스터마이즈 · **pickup이 고지 도중 발생 시 고지 완료 보장**(현재는 중단 허용 — 실무상 고지는 수 초, claim+bootstrap보다 빨라 대부분 완료됨).

## 8. 열린 항목

- **고지 문구 법무 승인** (4.2) — 승인 전엔 초안 자산으로 진행, 승인 후 재생성.
- 아웃바운드 고지(현행 AI 세션 발화)와 문구 일관성 정렬 여부.
