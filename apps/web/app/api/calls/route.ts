// POST /api/calls - create a call
// GET  /api/calls - list the current user's calls

import { NextRequest, NextResponse } from 'next/server';
import { desc, eq } from 'drizzle-orm';
import { db, schema } from '@/lib/db/client';
import { callRowFromDb } from '@/lib/db/mappers';
import { requireUser } from '@/lib/auth/require-user';
import { authErrorResponse } from '@/lib/auth/route-helpers';
import { getConversationById, updateConversationStatus } from '@/lib/db/chat';
import { CreateCallRequest, CollectedData } from '@/shared/types';
import { communicationModeToCallMode } from '@/shared/call-types';
import type { CommunicationMode } from '@/shared/call-types';
import { toCallResponse } from '@/lib/supabase/helpers';

export async function POST(request: NextRequest) {
  try {
    const user = await requireUser();

    const body = (await request.json()) as CreateCallRequest;
    const { conversationId, communicationMode } = body;

    if (!conversationId) {
      return NextResponse.json({ error: 'conversationId is required' }, { status: 400 });
    }

    const conversation = await getConversationById(conversationId);
    if (!conversation || conversation.user_id !== user.id) {
      return NextResponse.json({ error: 'Conversation not found' }, { status: 404 });
    }

    const rejectWith = (code: number, error: string, reason: string) => {
      console.warn('[POST /api/calls] rejected:', {
        userId: user.id,
        conversationId,
        status: conversation.status,
        reason,
      });
      return NextResponse.json({ error }, { status: code });
    };

    switch (conversation.status) {
      case 'COLLECTING':
        return rejectWith(400, 'Conversation is not ready for call', 'status_collecting');
      case 'CALLING':
        return rejectWith(400, 'Call already in progress', 'status_calling');
      case 'COMPLETED':
        return rejectWith(400, 'Conversation already completed', 'status_completed');
      case 'CANCELLED':
        return rejectWith(400, 'Conversation was cancelled', 'status_cancelled');
      case 'READY':
        break;
      default:
        return rejectWith(400, 'Invalid conversation status', `status_unknown:${conversation.status}`);
    }

    const collectedData = conversation.collected_data as CollectedData;
    if (!collectedData.target_phone) {
      return rejectWith(400, 'Target phone number is required', 'no_target_phone');
    }

    const selectedMode: CommunicationMode = communicationMode || 'voice_to_voice';
    const callMode = communicationModeToCallMode(selectedMode);

    const [inserted] = await db
      .insert(schema.calls)
      .values({
        userId: user.id,
        conversationId,
        requestType: collectedData.scenario_type || 'RESERVATION',
        targetName: collectedData.target_name ?? null,
        targetPhone: collectedData.target_phone,
        parsedDate: collectedData.primary_datetime?.split(' ')[0] || null,
        parsedTime: collectedData.primary_datetime?.split(' ')[1] || null,
        parsedService: collectedData.service ?? null,
        sourceLanguage: collectedData.source_language || 'en',
        targetLanguage: collectedData.target_language || 'ko',
        status: 'PENDING',
        callMode,
        communicationMode: selectedMode,
      })
      .returning();

    if (!inserted) {
      return NextResponse.json({ error: 'Failed to create call' }, { status: 500 });
    }

    await updateConversationStatus(conversationId, 'CALLING');

    return NextResponse.json(toCallResponse(callRowFromDb(inserted)), { status: 201 });
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    console.error('Failed to create call:', error);
    return NextResponse.json({ error: 'Failed to create call' }, { status: 500 });
  }
}

export async function GET() {
  try {
    const user = await requireUser();

    const rows = await db
      .select()
      .from(schema.calls)
      .where(eq(schema.calls.userId, user.id))
      .orderBy(desc(schema.calls.createdAt))
      .limit(20);

    return NextResponse.json({
      calls: rows.map((r) => toCallResponse(callRowFromDb(r))),
    });
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    console.error('Failed to get calls:', error);
    return NextResponse.json({ error: 'Failed to get calls' }, { status: 500 });
  }
}
