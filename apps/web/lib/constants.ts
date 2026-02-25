// =============================================================================
// WIGVO Constants
// =============================================================================
// 프로젝트 전역 상수 정의
// =============================================================================

// -----------------------------------------------------------------------------
// Chat / LLM
// -----------------------------------------------------------------------------

/** LLM 컨텍스트에 포함할 최근 메시지 수 */
export const LLM_CONTEXT_MESSAGE_LIMIT = 10;

/** 대화 기록 조회 시 최대 메시지 수 */
export const CONVERSATION_HISTORY_LIMIT = 20;

/** 대화 목록 조회 시 최대 개수 */
export const CONVERSATION_LIST_LIMIT = 20;

/** 사용자 메시지 최대 길이 */
export const MAX_MESSAGE_LENGTH = 1000;

// -----------------------------------------------------------------------------
// Relay Server
// -----------------------------------------------------------------------------

/** Relay Server HTTP URL (서버사이드) */
export const RELAY_SERVER_URL = process.env.RELAY_SERVER_URL || 'http://localhost:8000';

/** Relay Server WebSocket URL (클라이언트사이드) */
export const RELAY_WS_URL = process.env.NEXT_PUBLIC_RELAY_WS_URL || 'ws://localhost:8000';

// --- VAD (Voice Activity Detection) ---

/** RMS speech 임계값 */
export const VAD_SPEECH_THRESHOLD = 0.015;

/** RMS silence 임계값 */
export const VAD_SILENCE_THRESHOLD = 0.008;

/** 발화 시작 지연 (ms) */
export const VAD_SPEECH_ONSET_DELAY = 150;

/** 발화 종료 지연 (ms) */
export const VAD_SPEECH_END_DELAY = 350;

/** 오디오 청크 크기 (100ms @ 16kHz mono) */
export const VAD_CHUNK_SIZE = 1600;

// -----------------------------------------------------------------------------
// UI / UX
// -----------------------------------------------------------------------------

/** 에러 메시지 자동 디스미스 시간 (밀리초) */
export const ERROR_AUTO_DISMISS_MS = 5000;

/** localStorage 키: 현재 대화 ID */
export const STORAGE_KEY_CONVERSATION_ID = 'currentConversationId';

// -----------------------------------------------------------------------------
// Validation
// -----------------------------------------------------------------------------

/** 한국 전화번호 정규식 */
export const PHONE_NUMBER_REGEX = /^(0[0-9]{1,2})[0-9]{3,4}[0-9]{4}$/;

/** UUID v4 정규식 */
export const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

