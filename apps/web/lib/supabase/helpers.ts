// =============================================================================
// Supabase Row → API Response Helpers
// =============================================================================
// snake_case DB rows → camelCase API responses 변환
// =============================================================================

import { CallRow, Call } from '@/shared/types';

/**
 * CallRow (snake_case) → Call (camelCase) 변환
 */
export function toCallResponse(row: CallRow): Call {
  return {
    id: row.id,
    userId: row.user_id,
    conversationId: row.conversation_id,
    requestType: row.request_type,
    targetName: row.target_name,
    targetPhone: row.target_phone,
    parsedDate: row.parsed_date,
    parsedTime: row.parsed_time,
    parsedService: row.parsed_service,
    status: row.status,
    result: row.result,
    summary: row.summary,
    callMode: row.call_mode ?? undefined,
    communicationMode: row.communication_mode === 'voice_to_text'
      ? 'voice_to_voice'  // Legacy V2T → V2V fallback
      : row.communication_mode ?? undefined,
    relayWsUrl: row.relay_ws_url ?? undefined,
    callId: row.call_id,
    callSid: row.call_sid,
    sourceLanguage: row.source_language,
    targetLanguage: row.target_language,
    durationS: row.duration_s,
    totalTokens: row.total_tokens,
    autoEnded: row.auto_ended,
    transcriptBilingual: row.transcript_bilingual ?? undefined,
    createdAt: row.created_at,
    completedAt: row.completed_at,
  };
}
