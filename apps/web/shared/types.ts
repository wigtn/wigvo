// =============================================================================
// WIGVO Shared Types (v2)
// =============================================================================
// BE1 소유 - 모든 역할이 READ
// API Contract (api-contract.mdc) 기반
// =============================================================================

// -----------------------------------------------------------------------------
// Conversation Status
// -----------------------------------------------------------------------------
export type ConversationStatus =
  | 'COLLECTING'
  | 'READY'
  | 'CALLING'
  | 'COMPLETED'
  | 'CANCELLED';

// -----------------------------------------------------------------------------
// Scenario Type
// -----------------------------------------------------------------------------
export type ScenarioType = 'RESERVATION' | 'INQUIRY' | 'AS_REQUEST';

// -----------------------------------------------------------------------------
// Scenario Sub Types (v3 - 시나리오 세분화)
// -----------------------------------------------------------------------------
export type ReservationSubType = 
  | 'RESTAURANT'    // 식당
  | 'SALON'         // 미용실
  | 'HOSPITAL'      // 병원/치과
  | 'HOTEL'         // 호텔/숙소
  | 'OTHER';        // 기타

export type InquirySubType = 
  | 'PROPERTY'       // 매물 확인
  | 'BUSINESS_HOURS' // 영업시간/가격
  | 'AVAILABILITY'   // 재고/가능 여부
  | 'OTHER';         // 기타

export type AsRequestSubType = 
  | 'HOME_APPLIANCE' // 가전제품
  | 'ELECTRONICS'    // 전자기기
  | 'REPAIR'         // 수리/설치
  | 'OTHER';         // 기타

export type ScenarioSubType = ReservationSubType | InquirySubType | AsRequestSubType;

// -----------------------------------------------------------------------------
// Detected Intent (v5 - 의도 감지 + 시나리오 자동 전환)
// -----------------------------------------------------------------------------
export interface DetectedIntent {
  scenario_type: ScenarioType;
  scenario_sub_type: ScenarioSubType;
  confidence: number; // 0-1
}

// -----------------------------------------------------------------------------
// Fallback Action
// -----------------------------------------------------------------------------
export type FallbackAction = 'ASK_AVAILABLE' | 'NEXT_DAY' | 'CANCEL';

// -----------------------------------------------------------------------------
// Call Status
// -----------------------------------------------------------------------------
export type CallStatus =
  | 'PENDING'
  | 'CALLING'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'FAILED';

// -----------------------------------------------------------------------------
// Call Result
// -----------------------------------------------------------------------------
export type CallResult = 'SUCCESS' | 'NO_ANSWER' | 'REJECTED' | 'ERROR';

// -----------------------------------------------------------------------------
// Collected Data (정보 수집 결과)
// -----------------------------------------------------------------------------
export interface CollectedData {
  target_name: string | null;
  target_phone: string | null;
  scenario_type: ScenarioType | null;
  scenario_sub_type: ScenarioSubType | null;  // v3: 서브 시나리오 타입
  primary_datetime: string | null;
  service: string | null;
  fallback_datetimes: string[];
  fallback_action: FallbackAction | null;
  customer_name: string | null;
  party_size: number | null;
  special_request: string | null;
  source_language?: string | null;
  target_language?: string | null;
}

/**
 * 빈 CollectedData 객체 생성
 * - string 필드: null
 * - 배열 필드: []
 * - number 필드: null
 */
export function createEmptyCollectedData(): CollectedData {
  return {
    target_name: null,
    target_phone: null,
    scenario_type: null,
    scenario_sub_type: null,
    primary_datetime: null,
    service: null,
    fallback_datetimes: [],
    fallback_action: null,
    customer_name: null,
    party_size: null,
    special_request: null,
    source_language: null,
    target_language: null,
  };
}

/**
 * CollectedData 병합 (v3 개선: null 보존 강화)
 * - null이 아닌 새 값만 덮어쓰기
 * - 배열은 비어있지 않을 때만 교체
 * - **중요**: incoming에서 명시적으로 null을 보내도 기존 값 유지 (정보 손실 방지)
 * 
 * @param existing - 기존 수집 데이터
 * @param incoming - 새로 수집된 데이터 (LLM 응답)
 * @param preserveExisting - true면 null을 보내도 기존 값 유지 (기본값: true)
 */
export function mergeCollectedData(
  existing: CollectedData,
  incoming: Partial<CollectedData>,
  preserveExisting: boolean = true
): CollectedData {
  // preserveExisting이 true면: null이어도 기존 값 유지
  // preserveExisting이 false면: undefined만 기존 값 유지, null은 명시적 삭제로 처리
  
  const mergeString = (existingVal: string | null, incomingVal: string | null | undefined): string | null => {
    if (preserveExisting) {
      // null을 보내도 기존 값 유지
      return incomingVal !== undefined && incomingVal !== null ? incomingVal : existingVal;
    } else {
      // undefined만 기존 값 유지, null은 null로 설정
      return incomingVal !== undefined ? incomingVal : existingVal;
    }
  };
  
  const mergeNumber = (existingVal: number | null, incomingVal: number | null | undefined): number | null => {
    if (preserveExisting) {
      return incomingVal !== undefined && incomingVal !== null ? incomingVal : existingVal;
    } else {
      return incomingVal !== undefined ? incomingVal : existingVal;
    }
  };
  
  return {
    target_name: mergeString(existing.target_name, incoming.target_name),
    target_phone: mergeString(existing.target_phone, incoming.target_phone),
    scenario_type: incoming.scenario_type !== undefined && incoming.scenario_type !== null
      ? incoming.scenario_type
      : existing.scenario_type,
    scenario_sub_type: incoming.scenario_sub_type !== undefined && incoming.scenario_sub_type !== null
      ? incoming.scenario_sub_type
      : existing.scenario_sub_type,
    primary_datetime: mergeString(existing.primary_datetime, incoming.primary_datetime),
    service: mergeString(existing.service, incoming.service),
    fallback_datetimes:
      incoming.fallback_datetimes && incoming.fallback_datetimes.length > 0
        ? incoming.fallback_datetimes
        : existing.fallback_datetimes,
    fallback_action: incoming.fallback_action !== undefined && incoming.fallback_action !== null
      ? incoming.fallback_action
      : existing.fallback_action,
    customer_name: mergeString(existing.customer_name, incoming.customer_name),
    party_size: mergeNumber(existing.party_size, incoming.party_size),
    special_request: mergeString(existing.special_request, incoming.special_request),
    source_language: mergeString(existing.source_language ?? null, incoming.source_language),
    target_language: mergeString(existing.target_language ?? null, incoming.target_language),
  };
}

// -----------------------------------------------------------------------------
// Message
// -----------------------------------------------------------------------------
export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: string;
}

// -----------------------------------------------------------------------------
// Conversation
// -----------------------------------------------------------------------------
export interface Conversation {
  id: string;
  userId: string;
  status: ConversationStatus;
  collectedData: CollectedData;
  messages?: Message[];
  greeting?: string; // POST /api/conversations 응답 시에만 포함
  createdAt: string;
  updatedAt: string;
}

// -----------------------------------------------------------------------------
// Call
// -----------------------------------------------------------------------------
// Transcript entry from relay server (bilingual transcript)
export interface TranscriptEntry {
  role: 'user' | 'recipient';
  original_text: string;
  translated_text: string;
  language: string;
  timestamp: number;
}

export interface Call {
  id: string;
  userId: string;
  conversationId: string | null;
  requestType: ScenarioType;
  targetName: string | null;
  targetPhone: string;
  parsedDate: string | null;
  parsedTime: string | null;
  parsedService: string | null;
  status: CallStatus;
  result: CallResult | null;
  summary: string | null;
  callMode?: 'agent' | 'relay';
  communicationMode?: 'voice_to_voice' | 'text_to_voice' | 'full_agent';
  relayWsUrl?: string;
  callId?: string | null;
  callSid?: string | null;
  sourceLanguage?: string | null;
  targetLanguage?: string | null;
  durationS?: number | null;
  totalTokens?: number | null;
  autoEnded?: boolean;
  transcriptBilingual?: TranscriptEntry[];
  createdAt: string;
  completedAt: string | null;
}

// -----------------------------------------------------------------------------
// Database Row Types (snake_case - Supabase convention)
// -----------------------------------------------------------------------------

export interface ConversationRow {
  id: string;
  user_id: string;
  status: ConversationStatus;
  collected_data: CollectedData;
  created_at: string;
  updated_at: string;
}

export interface MessageRow {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface CallRow {
  id: string;
  conversation_id: string;
  user_id: string;
  request_type: ScenarioType;
  target_phone: string;
  target_name: string | null;
  parsed_date: string | null;
  parsed_time: string | null;
  parsed_service: string | null;
  status: CallStatus;
  result: CallResult | null;
  summary: string | null;
  call_mode: 'agent' | 'relay' | null;
  communication_mode: 'voice_to_voice' | 'text_to_voice' | 'voice_to_text' | 'full_agent' | null;
  relay_ws_url: string | null;
  call_id: string | null;
  call_sid: string | null;
  source_language: string | null;
  target_language: string | null;
  duration_s: number | null;
  total_tokens: number | null;
  auto_ended: boolean;
  transcript_bilingual: TranscriptEntry[] | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

// -----------------------------------------------------------------------------
// API Request/Response Types
// -----------------------------------------------------------------------------

// POST /api/chat
export interface ChatRequest {
  conversationId: string;
  message: string;
  communicationMode?: 'voice_to_voice' | 'text_to_voice' | 'full_agent';
}

export interface ChatResponse {
  message: string;
  collected: CollectedData;
  is_complete: boolean;
  conversation_status: ConversationStatus;
}

// POST /api/calls
export interface CreateCallRequest {
  conversationId: string;
  communicationMode?: 'voice_to_voice' | 'text_to_voice' | 'full_agent';
}

// POST /api/conversations request (v3: 시나리오 선택)
export interface CreateConversationRequest {
  scenarioType?: ScenarioType;
  subType?: ScenarioSubType;
}

// POST /api/conversations response
export interface CreateConversationResponse {
  id: string;
  userId: string;
  status: ConversationStatus;
  collectedData: CollectedData;
  greeting: string;
  createdAt: string;
  // v3: 시나리오 선택 옵션 (초기 화면용)
  scenarioOptions?: ScenarioOption[];
}

// 시나리오 선택 옵션
export interface ScenarioOption {
  type: ScenarioType;
  label: string;
  icon: string;
  subTypes: SubTypeOption[];
}

export interface SubTypeOption {
  type: ScenarioSubType;
  label: string;
}

// GET /api/calls response
export interface CallsListResponse {
  calls: Call[];
}
