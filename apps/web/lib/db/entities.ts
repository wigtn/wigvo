// Entity DB functions — Drizzle-backed replacement for the former
// lib/supabase/entities.ts. Function signatures preserved.

import 'server-only';
import { and, desc, eq } from 'drizzle-orm';
import { db, schema } from './client';
import { CollectedData } from '@/shared/types';

export interface ConversationEntity {
  id: string;
  conversation_id: string;
  entity_type: string;
  entity_value: string;
  confidence: number;
  source_message_id: string | null;
  created_at: string;
  updated_at: string;
}

function toIso(d: Date): string {
  return d.toISOString();
}

function toRow(r: typeof schema.conversationEntities.$inferSelect): ConversationEntity {
  return {
    id: r.id,
    conversation_id: r.conversationId,
    entity_type: r.entityType,
    entity_value: r.entityValue,
    confidence: r.confidence,
    source_message_id: r.sourceMessageId,
    created_at: toIso(r.createdAt as unknown as Date),
    updated_at: toIso(r.updatedAt as unknown as Date),
  };
}

export async function extractAndSaveEntities(
  conversationId: string,
  messageId: string,
  collectedData: Partial<CollectedData>,
): Promise<void> {
  const entries: Array<{ entity_type: string; entity_value: string; confidence: number }> = [];

  for (const [key, value] of Object.entries(collectedData)) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) {
      if (value.length > 0) {
        entries.push({ entity_type: key, entity_value: JSON.stringify(value), confidence: 0.9 });
      }
    } else if (typeof value === 'number') {
      entries.push({ entity_type: key, entity_value: String(value), confidence: 1.0 });
    } else if (typeof value === 'string' && value.trim() !== '') {
      entries.push({ entity_type: key, entity_value: value, confidence: 1.0 });
    }
  }

  for (const e of entries) {
    try {
      await db
        .insert(schema.conversationEntities)
        .values({
          conversationId,
          entityType: e.entity_type,
          entityValue: e.entity_value,
          confidence: e.confidence,
          sourceMessageId: messageId,
        })
        .onConflictDoUpdate({
          target: [schema.conversationEntities.conversationId, schema.conversationEntities.entityType],
          set: {
            entityValue: e.entity_value,
            confidence: e.confidence,
            sourceMessageId: messageId,
            updatedAt: new Date(),
          },
        });
    } catch (err) {
      console.error(`Failed to save entity ${e.entity_type}:`, err);
    }
  }
}

export async function getConversationEntities(
  conversationId: string,
): Promise<ConversationEntity[]> {
  try {
    const rows = await db
      .select()
      .from(schema.conversationEntities)
      .where(eq(schema.conversationEntities.conversationId, conversationId))
      .orderBy(desc(schema.conversationEntities.updatedAt));
    return rows.map(toRow);
  } catch (err) {
    console.error('Failed to get conversation entities:', err);
    return [];
  }
}

export async function getEntityByType(
  conversationId: string,
  entityType: string,
): Promise<ConversationEntity | null> {
  const [row] = await db
    .select()
    .from(schema.conversationEntities)
    .where(
      and(
        eq(schema.conversationEntities.conversationId, conversationId),
        eq(schema.conversationEntities.entityType, entityType),
      ),
    )
    .limit(1);
  return row ? toRow(row) : null;
}

export async function deleteEntity(
  conversationId: string,
  entityType: string,
): Promise<void> {
  try {
    await db
      .delete(schema.conversationEntities)
      .where(
        and(
          eq(schema.conversationEntities.conversationId, conversationId),
          eq(schema.conversationEntities.entityType, entityType),
        ),
      );
  } catch (err) {
    console.error(`Failed to delete entity ${entityType}:`, err);
  }
}

export function entitiesToCollectedData(
  entities: ConversationEntity[],
): Partial<CollectedData> {
  const result: Partial<CollectedData> = {};
  for (const entity of entities) {
    const { entity_type, entity_value } = entity;
    switch (entity_type) {
      case 'target_name':
      case 'target_phone':
      case 'scenario_type':
      case 'primary_datetime':
      case 'service':
      case 'fallback_action':
      case 'customer_name':
      case 'special_request':
        (result as Record<string, string>)[entity_type] = entity_value;
        break;
      case 'party_size':
        result.party_size = parseInt(entity_value, 10) || null;
        break;
      case 'fallback_datetimes':
        try {
          result.fallback_datetimes = JSON.parse(entity_value);
        } catch {
          result.fallback_datetimes = [];
        }
        break;
    }
  }
  return result;
}
