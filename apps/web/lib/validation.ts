// =============================================================================
// WIGVO Validation
// =============================================================================
// Zod 스키마 기반 입력 검증
// =============================================================================

import { z } from 'zod';
import {
  MAX_MESSAGE_LENGTH,
  UUID_REGEX,
  PHONE_NUMBER_REGEX,
} from './constants';

// -----------------------------------------------------------------------------
// Chat Request Schema
// -----------------------------------------------------------------------------

export const ChatRequestSchema = z.object({
  conversationId: z
    .string()
    .min(1, 'conversationId is required')
    .regex(UUID_REGEX, 'Invalid conversationId format'),
  message: z
    .string()
    .min(1, 'message is required')
    .max(MAX_MESSAGE_LENGTH, `message must be ${MAX_MESSAGE_LENGTH} characters or less`)
    .transform((val) => val.trim()),
  communicationMode: z
    .enum(['voice_to_voice', 'text_to_voice', 'full_agent'])
    .optional(),
  locale: z.enum(['en', 'ko']).optional(),
});

export type ChatRequestInput = z.infer<typeof ChatRequestSchema>;

// -----------------------------------------------------------------------------
// Create Conversation Request Schema
// -----------------------------------------------------------------------------

export const CreateConversationRequestSchema = z.object({
  scenarioType: z.enum(['RESERVATION', 'INQUIRY', 'AS_REQUEST']).optional(),
  subType: z
    .enum([
      'RESTAURANT',
      'SALON',
      'HOSPITAL',
      'HOTEL',
      'OTHER',
      'PROPERTY',
      'BUSINESS_HOURS',
      'AVAILABILITY',
      'HOME_APPLIANCE',
      'ELECTRONICS',
      'REPAIR',
    ])
    .optional(),
});

export type CreateConversationRequestInput = z.infer<typeof CreateConversationRequestSchema>;

// -----------------------------------------------------------------------------
// Create Call Request Schema
// -----------------------------------------------------------------------------

export const CreateCallRequestSchema = z.object({
  conversationId: z
    .string()
    .min(1, 'conversationId is required')
    .regex(UUID_REGEX, 'Invalid conversationId format'),
});

export type CreateCallRequestInput = z.infer<typeof CreateCallRequestSchema>;

// -----------------------------------------------------------------------------
// Legacy validation functions (backward compatibility)
// -----------------------------------------------------------------------------

/**
 * 채팅 메시지 유효성 검사
 */
export function validateMessage(message: string): { valid: boolean; error?: string } {
  const trimmed = message.trim();

  if (!trimmed) {
    return { valid: false, error: '메시지를 입력해주세요.' };
  }

  if (trimmed.length > MAX_MESSAGE_LENGTH) {
    return { valid: false, error: `메시지는 ${MAX_MESSAGE_LENGTH}자 이내로 입력해주세요.` };
  }

  return { valid: true };
}

/**
 * 전화번호 형식 검사 (한국 전화번호)
 */
export function isValidPhoneNumber(phone: string): boolean {
  const cleaned = phone.replace(/[\s-]/g, '');
  return PHONE_NUMBER_REGEX.test(cleaned);
}

// -----------------------------------------------------------------------------
// Validation Helper
// -----------------------------------------------------------------------------

/**
 * Zod 스키마로 요청 검증
 * @returns 성공 시 파싱된 데이터, 실패 시 에러 메시지
 */
export function validateRequest<T>(
  schema: z.ZodSchema<T>,
  data: unknown
): { success: true; data: T } | { success: false; error: string } {
  const result = schema.safeParse(data);

  if (result.success) {
    return { success: true, data: result.data };
  }

  // 첫 번째 에러 메시지 반환 (Zod v4: issues 사용)
  const firstError = result.error.issues[0];
  const errorMessage = firstError
    ? `${firstError.path.join('.')}: ${firstError.message}`
    : 'Invalid request';

  return { success: false, error: errorMessage };
}
