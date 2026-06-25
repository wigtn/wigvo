// Mappers between Drizzle row shapes (camelCase) and the shared CallRow
// snake_case shape exposed by the previous Supabase layer. Centralized so
// API routes don't have to repeat the conversion.

import type { schema } from './client';
import {
  CallRow,
  CallStatus,
  CallResult,
  ScenarioType,
  TranscriptEntry,
} from '@/shared/types';
import * as schemaTypes from './schema';

type CallSelect = typeof schemaTypes.calls.$inferSelect;

export function callRowFromDb(row: CallSelect): CallRow {
  return {
    id: row.id,
    conversation_id: row.conversationId ?? '',
    user_id: row.userId ?? '',
    request_type: row.requestType as ScenarioType,
    target_phone: row.targetPhone ?? '',
    target_name: row.targetName,
    parsed_date: row.parsedDate,
    parsed_time: row.parsedTime,
    parsed_service: row.parsedService,
    status: row.status as CallStatus,
    result: row.result as CallResult | null,
    summary: row.summary,
    call_mode: row.callMode as CallRow['call_mode'],
    communication_mode: row.communicationMode as CallRow['communication_mode'],
    relay_ws_url: row.relayWsUrl,
    call_id: row.callId,
    call_sid: row.callSid,
    source_language: row.sourceLanguage,
    target_language: row.targetLanguage,
    duration_s: row.durationS,
    total_tokens: row.totalTokens,
    auto_ended: row.autoEnded,
    transcript_bilingual: row.transcriptBilingual as TranscriptEntry[] | null,
    created_at: (row.createdAt as unknown as Date).toISOString(),
    updated_at: (row.updatedAt as unknown as Date).toISOString(),
    completed_at: row.completedAt
      ? (row.completedAt as unknown as Date).toISOString()
      : null,
  };
}

// Re-export for files that prefer the bundled namespace import.
export { schema };
