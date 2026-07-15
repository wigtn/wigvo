// GET /api/conversations/[id] - fetch conversation with messages (recovery view)

import { NextRequest, NextResponse } from 'next/server';
import { and, eq, or } from 'drizzle-orm';
import { db, schema } from '@/lib/db/client';
import { requireUser } from '@/lib/auth/require-user';
import { authErrorResponse } from '@/lib/auth/route-helpers';
import { getConversation } from '@/lib/db/chat';

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const user = await requireUser();

    const conversation = await getConversation(id);
    if (
      !conversation ||
      conversation.user_id !== user.id ||
      conversation.tenant_id !== user.tenantId
    ) {
      return NextResponse.json({ error: 'Conversation not found' }, { status: 404 });
    }

    let effectiveStatus = conversation.status;
    if (conversation.status === 'CALLING') {
      const [call] = await db
        .select({ status: schema.calls.status })
        .from(schema.calls)
        .where(
          and(
            eq(schema.calls.conversationId, id),
            eq(schema.calls.tenantId, user.tenantId),
            or(eq(schema.calls.status, 'COMPLETED'), eq(schema.calls.status, 'FAILED')),
          ),
        )
        .limit(1);
      if (call) {
        effectiveStatus = 'COMPLETED';
      }
    }

    return NextResponse.json({
      id: conversation.id,
      userId: conversation.user_id,
      status: effectiveStatus,
      collectedData: conversation.collected_data,
      messages: conversation.messages.map((msg) => ({
        id: msg.id,
        role: msg.role,
        content: msg.content,
        createdAt: msg.created_at,
      })),
      createdAt: conversation.created_at,
      updatedAt: conversation.updated_at,
    });
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    console.error('Failed to get conversation:', error);
    return NextResponse.json({ error: 'Failed to get conversation' }, { status: 500 });
  }
}
