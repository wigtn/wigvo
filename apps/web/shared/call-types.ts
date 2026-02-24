// =============================================================================
// Relay Server 통화 관련 타입 정의
// =============================================================================
// Relay Server (wigvo/apps/relay-server/src/types.py) 와 동기화
// =============================================================================

// --- WebSocket Message Types ---

export enum WsMessageType {
  // App → Relay
  AUDIO_CHUNK = 'audio_chunk',
  TEXT_INPUT = 'text_input',
  VAD_STATE = 'vad_state',
  TYPING_STATE = 'typing_state',
  END_CALL = 'end_call',

  // Relay → App
  CAPTION = 'caption',
  CAPTION_ORIGINAL = 'caption.original',
  CAPTION_TRANSLATED = 'caption.translated',
  RECIPIENT_AUDIO = 'recipient_audio',
  CALL_STATUS = 'call_status',
  INTERRUPT_ALERT = 'interrupt_alert',
  SESSION_RECOVERY = 'session.recovery',
  GUARDRAIL_TRIGGERED = 'guardrail.triggered',
  TRANSLATION_STATE = 'translation.state',
  METRICS = 'metrics',
  ERROR = 'error',
}

// --- Supported Languages ---

export interface SupportedLanguage {
  code: string;
  label: string;
  flag: string;
}

export const SUPPORTED_LANGUAGES: readonly SupportedLanguage[] = [
  { code: 'ko', label: '\uD55C\uAD6D\uC5B4', flag: '\uD83C\uDDF0\uD83C\uDDF7' },
  { code: 'en', label: 'English', flag: '\uD83C\uDDFA\uD83C\uDDF8' },
  { code: 'ja', label: '\u65E5\u672C\u8A9E', flag: '\uD83C\uDDEF\uD83C\uDDF5' },
  { code: 'zh', label: '\u4E2D\u6587', flag: '\uD83C\uDDE8\uD83C\uDDF3' },
  { code: 'vi', label: 'Ti\u1EBFng Vi\u1EC7t', flag: '\uD83C\uDDFB\uD83C\uDDF3' },
] as const;

export interface LanguagePair {
  source: SupportedLanguage;
  target: SupportedLanguage;
}

export const DEFAULT_LANGUAGE_PAIR: LanguagePair = {
  source: SUPPORTED_LANGUAGES[0], // ko
  target: SUPPORTED_LANGUAGES[1], // en
};

// --- Call Mode ---

export type CallMode = 'agent' | 'relay';

// --- Communication Mode (v4: 4가지 통화 모드) ---

export type CommunicationMode =
  | 'voice_to_voice'   // 양방향 음성 번역
  | 'text_to_voice'    // 텍스트 입력 → AI 음성 전달
  | 'full_agent';      // AI 자율 통화

// --- Call Category (v5: 2-category UI) ---

export type CallCategory = 'direct' | 'ai_auto';

/** Map CommunicationMode → CallCategory */
export function getCallCategory(mode: CommunicationMode): CallCategory {
  return mode === 'full_agent' ? 'ai_auto' : 'direct';
}

/** Direct call sub-options for UI */
export interface DirectCallOptions {
  translation: boolean;    // 번역 on/off
  inputMethod: 'voice' | 'text';  // 사용자 입력 방식
}

/** Resolve DirectCallOptions to CommunicationMode */
export function resolveDirectMode(options: DirectCallOptions): CommunicationMode {
  return options.inputMethod === 'text' ? 'text_to_voice' : 'voice_to_voice';
}

export interface ModeUIConfig {
  audioInput: boolean;   // 마이크 녹음 활성화
  audioOutput: boolean;  // 수신자 음성 재생
  textInput: boolean;    // 텍스트 입력 UI 표시
  captionOnly: boolean;  // 자막 확대 모드
}

export function communicationModeToCallMode(mode: CommunicationMode): CallMode {
  return mode === 'full_agent' ? 'agent' : 'relay';
}

export function getModeUIConfig(mode: CommunicationMode): ModeUIConfig {
  switch (mode) {
    case 'voice_to_voice':
      return { audioInput: true, audioOutput: true, textInput: false, captionOnly: false };
    case 'text_to_voice':
      return { audioInput: false, audioOutput: true, textInput: true, captionOnly: false };
    case 'full_agent':
      return { audioInput: false, audioOutput: true, textInput: true, captionOnly: false };
  }
}

// --- Caption ---

export interface CaptionEntry {
  id: string;
  speaker: 'user' | 'recipient' | 'ai';
  text: string;
  language: string;
  isFinal: boolean;
  timestamp: number;
  stage?: 1 | 2;
  originalText?: string;  // Stage 1 원문 (Stage 2와 합쳐서 표시)
}

// --- WebSocket Message ---

export interface RelayWsMessage {
  type: WsMessageType;
  data: Record<string, unknown>;
}

// --- Call Start / End ---

export interface CallStartParams {
  call_id: string;
  phone_number: string;
  mode: CallMode;
  source_language: string;
  target_language: string;
  vad_mode: 'client' | 'server' | 'push_to_talk';
  collected_data: Record<string, unknown> | null;
  system_prompt_override?: string;
  communication_mode?: CommunicationMode;
}

export interface CallStartResult {
  call_id: string;
  call_sid: string;
  relay_ws_url: string;
  session_ids: Record<string, string>;
}
