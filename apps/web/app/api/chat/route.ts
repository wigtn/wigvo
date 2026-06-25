// POST /api/chat - process a user message + LLM turn for a conversation

import { NextRequest, NextResponse } from 'next/server';
import { requireUser } from '@/lib/auth/require-user';
import { authErrorResponse } from '@/lib/auth/route-helpers';
import {
  getConversationHistory,
  saveMessage,
  updateCollectedData,
  getConversationById,
} from '@/lib/db/chat';
import { extractAndSaveEntities } from '@/lib/db/entities';
import { ChatRequestSchema, validateRequest } from '@/lib/validation';
import { processChat, isReadyForCall } from '@/lib/services/chat-service';
import { CollectedData, mergeCollectedData } from '@/shared/types';

export async function POST(request: NextRequest) {
  try {
    const user = await requireUser();

    const body = await request.json();
    const validation = validateRequest(ChatRequestSchema, body);
    if (!validation.success) {
      return NextResponse.json({ error: validation.error }, { status: 400 });
    }

    const { conversationId, message, communicationMode, locale } = validation.data;

    const conversation = await getConversationById(conversationId);
    if (!conversation || conversation.user_id !== user.id) {
      return NextResponse.json({ error: 'Conversation not found' }, { status: 404 });
    }

    await saveMessage(conversationId, 'user', message);
    const history = await getConversationHistory(conversationId);
    const existingData = conversation.collected_data as CollectedData;

    let chatResult;
    try {
      chatResult = await processChat({
        existingData,
        history,
        userMessage: message,
        communicationMode,
        locale,
      });
    } catch (llmError) {
      console.error('OpenAI API error:', llmError);
      return NextResponse.json({
        message: '죄송합니다, 잠시 오류가 발생했어요. 다시 말씀해주세요.',
        collected: conversation.collected_data,
        is_complete: false,
        conversation_status: conversation.status,
      });
    }

    const mergedData = mergeCollectedData(existingData, chatResult.collected, true);

    const savedMessage = await saveMessage(
      conversationId,
      'assistant',
      chatResult.message,
      { collected: chatResult.collected, is_complete: chatResult.is_complete },
    );

    if (chatResult.collected && savedMessage?.id) {
      try {
        await extractAndSaveEntities(
          conversationId,
          savedMessage.id,
          chatResult.collected as CollectedData,
        );
      } catch (entityError) {
        console.warn('[Entity] Failed to save entities:', entityError);
      }
    }

    const { ready, forceReady } = isReadyForCall(
      mergedData,
      chatResult.is_complete,
      communicationMode,
    );
    const newStatus = ready ? 'READY' : 'COLLECTING';
    const effectiveComplete = chatResult.is_complete || forceReady;

    await updateCollectedData(conversationId, mergedData, newStatus);

    return NextResponse.json({
      message: chatResult.message,
      collected: mergedData,
      is_complete: effectiveComplete,
      conversation_status: newStatus,
    });
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    console.error('Failed to process chat:', error);
    return NextResponse.json({ error: 'Failed to process chat' }, { status: 500 });
  }
}
