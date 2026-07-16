// POST /api/calls/[id]/start
// Kick off an outbound call via the relay server. Pulls call + conversation
// rows from local Postgres, updates state through the lifecycle, and reports
// the relay's response back to the client.

import { NextRequest, NextResponse } from 'next/server';
import { and, eq } from 'drizzle-orm';
import { db, schema } from '@/lib/db/client';
import { requireUser } from '@/lib/auth/require-user';
import { authErrorResponse } from '@/lib/auth/route-helpers';
import { generateDynamicPrompt } from '@/lib/prompt-generator';
import { startRelayCall, formatPhoneToE164 } from '@/lib/relay-client';
import { isValidPhoneNumber } from '@/lib/validation';
import type { CallMode, CommunicationMode } from '@/shared/call-types';
import type { CollectedData } from '@/shared/types';

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id: callId } = await params;

  try {
    const user = await requireUser();

    // 1. Load the call (owner-scoped) joined with its conversation.
    const [callRow] = await db
      .select({
        call: schema.calls,
        convCollected: schema.conversations.collectedData,
        convStatus: schema.conversations.status,
      })
      .from(schema.calls)
      .leftJoin(
        schema.conversations,
        eq(schema.conversations.id, schema.calls.conversationId),
      )
      .where(
        and(
          eq(schema.calls.id, callId),
          eq(schema.calls.userId, user.id),
          eq(schema.calls.tenantId, user.tenantId),
        ),
      )
      .limit(1);

    if (!callRow) {
      return NextResponse.json({ error: 'Call not found' }, { status: 404 });
    }
    const call = callRow.call;

    if (call.status !== 'PENDING') {
      return NextResponse.json(
        { error: `Call is already in status: ${call.status}` },
        { status: 400 },
      );
    }

    // 2. Build the collected_data context. Prefer the conversation snapshot;
    // fall back to call-row fields for legacy rows without a conversation.
    const collectedData: CollectedData =
      (callRow.convCollected as CollectedData | null) ?? ({
        target_name: call.targetName,
        target_phone: call.targetPhone,
        scenario_type: null,
        scenario_sub_type: null,
        primary_datetime: null,
        service: call.parsedService,
        customer_name: null,
        party_size: null,
        fallback_datetimes: [],
        fallback_action: null,
        special_request: null,
        source_language: null,
        target_language: null,
      } as unknown as CollectedData);

    if (!call.targetPhone) {
      return NextResponse.json({ error: 'Target phone is missing' }, { status: 400 });
    }

    // Normalize + validate E.164 BEFORE transitioning to CALLING so an invalid
    // number doesn't leave the call in a half-started state.
    const phoneNumber = formatPhoneToE164(call.targetPhone);
    if (!isValidPhoneNumber(phoneNumber)) {
      return NextResponse.json(
        {
          error:
            '국가코드를 포함한 국제번호 형식(E.164, 예: +1 415 555 1234)으로 입력해 주세요.',
        },
        { status: 400 },
      );
    }

    // 3. Transition both call and conversation to CALLING before issuing
    // the relay request so the UI reflects in-flight state.
    const [claimedCall] = await db
      .update(schema.calls)
      .set({ status: 'CALLING', updatedAt: new Date() })
      .where(
        and(
          eq(schema.calls.id, callId),
          eq(schema.calls.userId, user.id),
          eq(schema.calls.tenantId, user.tenantId),
          eq(schema.calls.status, 'PENDING'),
        ),
      )
      .returning({ id: schema.calls.id });

    if (!claimedCall) {
      return NextResponse.json(
        { error: 'Call is already starting or started' },
        { status: 409 },
      );
    }

    if (call.conversationId) {
      await db
        .update(schema.conversations)
        .set({ status: 'CALLING', updatedAt: new Date() })
        .where(
          and(
            eq(schema.conversations.id, call.conversationId),
            eq(schema.conversations.tenantId, user.tenantId),
          ),
        );
    }

    const callMode: CallMode = (call.callMode as CallMode | null) || 'relay';
    const communicationMode: CommunicationMode =
      (call.communicationMode as CommunicationMode | null) || 'voice_to_voice';

    let systemPromptOverride: string | undefined;
    if (callMode === 'agent') {
      const { systemPrompt } = generateDynamicPrompt(collectedData);
      systemPromptOverride = systemPrompt;
    }

    let relayResult;
    try {
      relayResult = await startRelayCall({
        call_id: callId,
        tenant_id: user.tenantId,
        phone_number: phoneNumber,
        mode: callMode,
        source_language:
          collectedData.source_language || call.sourceLanguage || 'ko',
        target_language:
          collectedData.target_language || call.targetLanguage || 'en',
        vad_mode: callMode === 'relay' ? 'client' : 'server',
        collected_data: collectedData as unknown as Record<string, unknown>,
        system_prompt_override: systemPromptOverride,
        communication_mode: communicationMode,
      });
    } catch (err) {
      console.error('[Start] Relay Server call failed:', err);
      await markCallFailed(
        callId,
        call.conversationId,
        user.tenantId,
        err instanceof Error ? err.message : 'Relay Server call initiation failed',
      );
      return NextResponse.json({ error: 'Failed to start call' }, { status: 500 });
    }

    // 4. Promote call to IN_PROGRESS with the relay websocket attached.
    await db
      .update(schema.calls)
      .set({
        status: 'IN_PROGRESS',
        relayWsUrl: relayResult.relay_ws_url,
        callMode,
        updatedAt: new Date(),
      })
      .where(
        and(
          eq(schema.calls.id, callId),
          eq(schema.calls.userId, user.id),
          eq(schema.calls.tenantId, user.tenantId),
          eq(schema.calls.status, 'CALLING'),
        ),
      );

    return NextResponse.json({
      success: true,
      callId,
      relayWsUrl: relayResult.relay_ws_url,
      callSid: relayResult.call_sid,
    });
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    console.error('[Start] Unexpected error:', error);
    return NextResponse.json({ error: 'Failed to start call' }, { status: 500 });
  }
}

async function markCallFailed(
  callId: string,
  conversationId: string | null,
  tenantId: string,
  message: string,
): Promise<void> {
  try {
    const [failedCall] = await db
      .update(schema.calls)
      .set({
        status: 'FAILED',
        result: 'ERROR',
        summary: message,
        completedAt: new Date(),
        updatedAt: new Date(),
      })
      .where(
        and(
          eq(schema.calls.id, callId),
          eq(schema.calls.tenantId, tenantId),
          eq(schema.calls.status, 'CALLING'),
        ),
      )
      .returning({ id: schema.calls.id });
    if (failedCall && conversationId) {
      try {
        await db
          .update(schema.conversations)
          .set({ status: 'COMPLETED', updatedAt: new Date() })
          .where(
            and(
              eq(schema.conversations.id, conversationId),
              eq(schema.conversations.tenantId, tenantId),
            ),
          );
      } catch (convErr) {
        console.error('[Helper] Conversation update failed (non-critical):', convErr);
      }
    }
  } catch (err) {
    console.error('[Helper] Failed to update call as FAILED:', err);
  }
}
