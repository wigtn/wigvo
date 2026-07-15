// =============================================================================
// Relay Server HTTP Client (server-side only)
// =============================================================================
// Relay Server API와 통신하는 클라이언트
// =============================================================================

import 'server-only';
import type { CallStartParams, CallStartResult } from '@/shared/call-types';
import type { InboundCall, InboundPickupResult } from '@/shared/inbound-types';

const RELAY_SERVER_URL = process.env.RELAY_SERVER_URL || 'http://localhost:8000';
const RELAY_API_KEY = process.env.RELAY_API_KEY;

function relayHeaders(): HeadersInit {
  return {
    'Content-Type': 'application/json',
    ...(RELAY_API_KEY ? { 'X-Wigvo-API-Key': RELAY_API_KEY } : {}),
  };
}

function relayUserHeaders(accessToken: string): HeadersInit {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${accessToken}`,
  };
}

async function relayError(response: Response): Promise<Error> {
  let detail = `Relay Server error (${response.status})`;
  try {
    const payload = (await response.json()) as { detail?: string };
    if (payload.detail) detail = payload.detail;
  } catch {
    // Keep the status-only message when the response is not JSON.
  }
  return Object.assign(new Error(detail), { status: response.status });
}

export async function listInboundCalls(accessToken: string): Promise<InboundCall[]> {
  const response = await fetch(`${RELAY_SERVER_URL}/relay/inbound/calls`, {
    headers: relayUserHeaders(accessToken),
    cache: 'no-store',
  });
  if (!response.ok) throw await relayError(response);
  const payload = (await response.json()) as { calls: InboundCall[] };
  return payload.calls;
}

export async function pickupInboundCall(
  callId: string,
  accessToken: string,
): Promise<InboundPickupResult> {
  const response = await fetch(
    `${RELAY_SERVER_URL}/relay/inbound/calls/${encodeURIComponent(callId)}/pickup`,
    {
      method: 'POST',
      headers: relayUserHeaders(accessToken),
    },
  );
  if (!response.ok) throw await relayError(response);
  return (await response.json()) as InboundPickupResult;
}

/**
 * Relay Server에 통화 시작 요청을 보냅니다.
 * POST /relay/calls/start
 */
export async function startRelayCall(params: CallStartParams): Promise<CallStartResult> {
  const response = await fetch(`${RELAY_SERVER_URL}/relay/calls/start`, {
    method: 'POST',
    headers: relayHeaders(),
    body: JSON.stringify(params),
  });

  if (!response.ok) {
    const errorText = await response.text();
    console.error('[RelayClient] startRelayCall failed:', response.status, errorText);
    throw new Error(`Relay Server error (${response.status}): ${errorText}`);
  }

  return (await response.json()) as CallStartResult;
}

/**
 * Relay Server에 통화 종료 요청을 보냅니다.
 * POST /relay/calls/{call_id}/end
 */
export async function endRelayCall(callId: string, reason?: string): Promise<void> {
  const response = await fetch(`${RELAY_SERVER_URL}/relay/calls/${callId}/end`, {
    method: 'POST',
    headers: relayHeaders(),
    body: JSON.stringify({ call_id: callId, reason: reason || 'user_hangup' }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    console.error('[RelayClient] endRelayCall failed:', response.status, errorText);
    throw new Error(`Relay Server error (${response.status}): ${errorText}`);
  }
}

/**
 * 입력 전화번호를 E.164 형식으로 정규화합니다 (구분문자 제거, `+`/숫자만 유지).
 *
 * 국가코드를 임의로 주입하지 않습니다 — 사용자가 `+국가코드`를 포함한 국제번호로
 * 입력해야 합니다. 유효성(E.164)은 isValidPhoneNumber / relay 검증이 보증합니다.
 *
 * | 입력                 | 출력             |
 * |----------------------|------------------|
 * | +1 415-555-1234      | +14155551234     |
 * | +82 10-1234-5678     | +821012345678    |
 */
export function formatPhoneToE164(phone: string): string {
  return phone.replace(/[^\d+]/g, '');
}
