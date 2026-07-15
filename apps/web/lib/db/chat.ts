// Chat DB functions — Drizzle-backed replacement for the former
// lib/supabase/chat.ts. Function signatures are preserved verbatim so
// existing callers keep working.

import 'server-only';
import { and, asc, eq } from 'drizzle-orm';
import { db, schema } from './client';
import {
  CollectedData,
  ConversationStatus,
  ScenarioType,
  ScenarioSubType,
  createEmptyCollectedData,
} from '@/shared/types';
import type { CommunicationMode } from '@/shared/call-types';
import { getGreetingMessage } from '@/lib/prompts';
import { getScenarioGreeting } from '@/lib/scenarios/config';
import { CONVERSATION_HISTORY_LIMIT } from '@/lib/constants';

interface ConversationRow {
  id: string;
  tenant_id: string;
  user_id: string;
  status: ConversationStatus;
  collected_data: CollectedData;
  created_at: string;
  updated_at: string;
}

interface MessageRow {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

function toIso(d: Date): string {
  return d.toISOString();
}

function toConvRow(row: typeof schema.conversations.$inferSelect): ConversationRow {
  return {
    id: row.id,
    tenant_id: row.tenantId,
    user_id: row.userId,
    status: row.status,
    collected_data: row.collectedData as CollectedData,
    created_at: toIso(row.createdAt as unknown as Date),
    updated_at: toIso(row.updatedAt as unknown as Date),
  };
}

function toMsgRow(row: typeof schema.messages.$inferSelect): MessageRow {
  return {
    id: row.id,
    conversation_id: row.conversationId,
    role: row.role,
    content: row.content,
    metadata: row.metadata,
    created_at: toIso(row.createdAt as unknown as Date),
  };
}

export async function createConversation(
  userId: string,
  tenantId: string,
  scenarioType?: ScenarioType,
  subType?: ScenarioSubType,
  communicationMode?: CommunicationMode,
  sourceLang?: string,
  targetLang?: string,
  locale?: string,
) {
  const initialCollectedData = createEmptyCollectedData();
  if (scenarioType) initialCollectedData.scenario_type = scenarioType;
  if (subType) initialCollectedData.scenario_sub_type = subType;
  if (sourceLang) initialCollectedData.source_language = sourceLang;
  if (targetLang) initialCollectedData.target_language = targetLang;

  const [inserted] = await db
    .insert(schema.conversations)
    .values({
      userId,
      tenantId,
      status: 'COLLECTING',
      collectedData: initialCollectedData,
    })
    .returning();

  if (!inserted) {
    throw new Error('Failed to create conversation');
  }

  const greeting =
    scenarioType && subType
      ? getScenarioGreeting(scenarioType, subType, communicationMode, locale)
      : getGreetingMessage(locale);

  await db.insert(schema.messages).values({
    conversationId: inserted.id,
    role: 'assistant',
    content: greeting,
    metadata: {},
  });

  return {
    conversation: toConvRow(inserted),
    greeting,
  };
}

export async function getConversationHistory(conversationId: string) {
  const rows = await db
    .select({
      role: schema.messages.role,
      content: schema.messages.content,
      created_at: schema.messages.createdAt,
    })
    .from(schema.messages)
    .where(eq(schema.messages.conversationId, conversationId))
    .orderBy(asc(schema.messages.createdAt))
    .limit(CONVERSATION_HISTORY_LIMIT);

  return rows.map((r) => ({
    role: r.role,
    content: r.content,
    created_at: toIso(r.created_at as unknown as Date),
  }));
}

export async function saveMessage(
  conversationId: string,
  role: 'user' | 'assistant',
  content: string,
  metadata: Record<string, unknown> = {},
) {
  const [inserted] = await db
    .insert(schema.messages)
    .values({
      conversationId,
      role,
      content,
      metadata,
    })
    .returning();

  if (!inserted) {
    throw new Error('Failed to save message');
  }

  return toMsgRow(inserted);
}

export async function updateCollectedData(
  conversationId: string,
  collectedData: CollectedData,
  status?: ConversationStatus,
) {
  const updateSet: {
    collectedData: CollectedData;
    updatedAt: Date;
    status?: ConversationStatus;
  } = {
    collectedData,
    updatedAt: new Date(),
  };
  if (status) updateSet.status = status;

  await db
    .update(schema.conversations)
    .set(updateSet)
    .where(eq(schema.conversations.id, conversationId));
}

export async function getConversation(conversationId: string) {
  const [conversation] = await db
    .select()
    .from(schema.conversations)
    .where(eq(schema.conversations.id, conversationId))
    .limit(1);

  if (!conversation) return null;

  const messageRows = await db
    .select({
      id: schema.messages.id,
      role: schema.messages.role,
      content: schema.messages.content,
      created_at: schema.messages.createdAt,
    })
    .from(schema.messages)
    .where(eq(schema.messages.conversationId, conversationId))
    .orderBy(asc(schema.messages.createdAt));

  return {
    ...toConvRow(conversation),
    messages: messageRows.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      created_at: toIso(m.created_at as unknown as Date),
    })),
  };
}

export async function getConversationById(conversationId: string) {
  const [conversation] = await db
    .select()
    .from(schema.conversations)
    .where(eq(schema.conversations.id, conversationId))
    .limit(1);
  return conversation ? toConvRow(conversation) : null;
}

export async function updateConversationStatus(
  conversationId: string,
  status: ConversationStatus,
) {
  await db
    .update(schema.conversations)
    .set({ status, updatedAt: new Date() })
    .where(eq(schema.conversations.id, conversationId));
}

// Helper for API routes that need ownership checks.
export async function getOwnedConversation(conversationId: string, userId: string) {
  const [conversation] = await db
    .select()
    .from(schema.conversations)
    .where(
      and(
        eq(schema.conversations.id, conversationId),
        eq(schema.conversations.userId, userId),
      ),
    )
    .limit(1);
  return conversation ? toConvRow(conversation) : null;
}
