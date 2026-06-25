// POST /api/conversations - start a conversation
// GET  /api/conversations - list the current user's recent conversations

import { NextRequest, NextResponse } from 'next/server';
import { and, desc, eq, inArray, or } from 'drizzle-orm';
import { db, schema } from '@/lib/db/client';
import { requireUser } from '@/lib/auth/require-user';
import { authErrorResponse } from '@/lib/auth/route-helpers';
import { createConversation } from '@/lib/db/chat';
import type { CollectedData, ScenarioType, ScenarioSubType } from '@/shared/types';
import type { CommunicationMode } from '@/shared/call-types';

export async function POST(request: NextRequest) {
  try {
    const user = await requireUser();

    let scenarioType: ScenarioType | undefined;
    let subType: ScenarioSubType | undefined;
    let communicationMode: CommunicationMode | undefined;
    let sourceLang: string | undefined;
    let targetLang: string | undefined;
    let locale: string | undefined;

    try {
      const body = await request.json();
      scenarioType = body.scenarioType;
      subType = body.subType;
      communicationMode = body.communicationMode;
      sourceLang = body.sourceLang;
      targetLang = body.targetLang;
      locale = body.locale;
    } catch {
      /* body optional */
    }

    const { conversation, greeting } = await createConversation(
      user.id,
      scenarioType,
      subType,
      communicationMode,
      sourceLang,
      targetLang,
      locale,
    );

    return NextResponse.json(
      {
        id: conversation.id,
        userId: conversation.user_id,
        status: conversation.status,
        collectedData: conversation.collected_data,
        greeting,
        createdAt: conversation.created_at,
      },
      { status: 201 },
    );
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    console.error('Failed to create conversation:', error);
    return NextResponse.json({ error: 'Failed to create conversation' }, { status: 500 });
  }
}

export async function GET() {
  try {
    const user = await requireUser();

    // Recent 20 conversations for this user.
    const convRows = await db
      .select({
        id: schema.conversations.id,
        status: schema.conversations.status,
        collectedData: schema.conversations.collectedData,
        createdAt: schema.conversations.createdAt,
      })
      .from(schema.conversations)
      .where(eq(schema.conversations.userId, user.id))
      .orderBy(desc(schema.conversations.createdAt))
      .limit(20);

    if (convRows.length === 0) {
      return NextResponse.json({ conversations: [] });
    }

    const convIds = convRows.map((c) => c.id);

    // Most recent message per conversation (for the sidebar snippet).
    const msgs = await db
      .select({
        conversationId: schema.messages.conversationId,
        content: schema.messages.content,
        createdAt: schema.messages.createdAt,
      })
      .from(schema.messages)
      .where(inArray(schema.messages.conversationId, convIds))
      .orderBy(desc(schema.messages.createdAt));

    const lastMessageByConv = new Map<string, string>();
    for (const m of msgs) {
      if (!lastMessageByConv.has(m.conversationId)) {
        lastMessageByConv.set(m.conversationId, m.content);
      }
    }

    // For CALLING conversations, surface effective status (COMPLETED if any
    // associated call has finished).
    const callingIds = convRows.filter((c) => c.status === 'CALLING').map((c) => c.id);
    const callStatusMap = new Map<string, string>();
    if (callingIds.length > 0) {
      const finishedCalls = await db
        .select({
          conversationId: schema.calls.conversationId,
          status: schema.calls.status,
        })
        .from(schema.calls)
        .where(
          and(
            inArray(schema.calls.conversationId, callingIds),
            or(eq(schema.calls.status, 'COMPLETED'), eq(schema.calls.status, 'FAILED')),
          ),
        );
      for (const c of finishedCalls) {
        if (c.conversationId) callStatusMap.set(c.conversationId, 'COMPLETED');
      }
    }

    const summaries = convRows.map((conv) => {
      const collectedData = conv.collectedData as CollectedData | null;
      const effectiveStatus = callStatusMap.get(conv.id) ?? conv.status;
      return {
        id: conv.id,
        status: effectiveStatus,
        targetName: collectedData?.target_name || null,
        lastMessage: (lastMessageByConv.get(conv.id) || '새 대화').slice(0, 50),
        createdAt: (conv.createdAt as unknown as Date).toISOString(),
      };
    });

    return NextResponse.json({ conversations: summaries });
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    console.error('Failed to fetch conversations:', error);
    return NextResponse.json({ error: 'Failed to fetch conversations' }, { status: 500 });
  }
}
