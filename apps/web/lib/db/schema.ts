// Drizzle schema for the local wigvo Postgres.
// Mirrors scripts/db/init/001_schema.sql one-for-one. Update both together.

import {
  boolean,
  doublePrecision,
  index,
  integer,
  jsonb,
  pgTable,
  real,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from 'drizzle-orm/pg-core';
import type { CollectedData, ConversationStatus } from '@/shared/types';

export const users = pgTable(
  'users',
  {
    id: uuid('id').primaryKey(),
    email: text('email'),
    name: text('name'),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
    deletedAt: timestamp('deleted_at', { withTimezone: true }),
  },
  (t) => ({
    emailIdx: index('idx_users_email').on(t.email),
  }),
);

export const conversations = pgTable(
  'conversations',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    userId: uuid('user_id').notNull(),
    status: text('status').$type<ConversationStatus>().notNull().default('COLLECTING'),
    collectedData: jsonb('collected_data').$type<CollectedData>().notNull().default({} as CollectedData),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    userIdx: index('idx_conversations_user_id').on(t.userId),
    createdIdx: index('idx_conversations_created_at').on(t.createdAt),
  }),
);

export const messages = pgTable(
  'messages',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    conversationId: uuid('conversation_id')
      .notNull()
      .references(() => conversations.id, { onDelete: 'cascade' }),
    role: text('role').$type<'user' | 'assistant'>().notNull(),
    content: text('content').notNull(),
    metadata: jsonb('metadata').$type<Record<string, unknown>>().notNull().default({}),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    convIdx: index('idx_messages_conversation_id').on(t.conversationId),
    createdIdx: index('idx_messages_created_at').on(t.createdAt),
  }),
);

export const calls = pgTable(
  'calls',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    conversationId: uuid('conversation_id').references(() => conversations.id, {
      onDelete: 'set null',
    }),
    userId: uuid('user_id').notNull(),
    requestType: text('request_type').notNull().default('RESERVATION'),
    targetPhone: text('target_phone'),
    targetName: text('target_name'),
    parsedDate: text('parsed_date'),
    parsedTime: text('parsed_time'),
    parsedService: text('parsed_service'),
    status: text('status').notNull().default('PENDING'),
    result: text('result'),
    summary: text('summary'),
    callId: text('call_id'),
    callMode: text('call_mode').notNull().default('agent'),
    relayWsUrl: text('relay_ws_url'),
    callSid: text('call_sid'),
    sourceLanguage: text('source_language').notNull().default('en'),
    targetLanguage: text('target_language').notNull().default('ko'),
    communicationMode: text('communication_mode'),
    transcriptBilingual: jsonb('transcript_bilingual').notNull().default([]),
    costTokens: jsonb('cost_tokens').notNull().default({}),
    guardrailEvents: jsonb('guardrail_events').notNull().default([]),
    recoveryEvents: jsonb('recovery_events').notNull().default([]),
    functionCallLogs: jsonb('function_call_logs').notNull().default([]),
    callResult: text('call_result'),
    callResultData: jsonb('call_result_data').notNull().default({}),
    autoEnded: boolean('auto_ended').notNull().default(false),
    durationS: real('duration_s'),
    totalTokens: integer('total_tokens').notNull().default(0),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
    completedAt: timestamp('completed_at', { withTimezone: true }),
  },
  (t) => ({
    userIdx: index('idx_calls_user_id').on(t.userId),
    convIdx: index('idx_calls_conversation_id').on(t.conversationId),
    modeIdx: index('idx_calls_call_mode').on(t.callMode),
    createdIdx: index('idx_calls_created_at').on(t.createdAt),
  }),
);

export const conversationEntities = pgTable(
  'conversation_entities',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    conversationId: uuid('conversation_id')
      .notNull()
      .references(() => conversations.id, { onDelete: 'cascade' }),
    entityType: text('entity_type').notNull(),
    entityValue: text('entity_value').notNull(),
    confidence: doublePrecision('confidence').notNull().default(1.0),
    sourceMessageId: uuid('source_message_id').references(() => messages.id, {
      onDelete: 'set null',
    }),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    convIdx: index('idx_entities_conversation_id').on(t.conversationId),
    typeIdx: index('idx_entities_type').on(t.entityType),
    uniqConvType: uniqueIndex('uq_entities_conv_type').on(t.conversationId, t.entityType),
  }),
);

export const placeSearchCache = pgTable(
  'place_search_cache',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    queryHash: text('query_hash').notNull().unique(),
    queryText: text('query_text').notNull(),
    results: jsonb('results').notNull(),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    expiresAt: timestamp('expires_at', { withTimezone: true }).notNull(),
  },
  (t) => ({
    hashIdx: index('idx_place_cache_hash').on(t.queryHash),
    expiresIdx: index('idx_place_cache_expires_at').on(t.expiresAt),
  }),
);

export type Conversation = typeof conversations.$inferSelect;
export type Message = typeof messages.$inferSelect;
export type Call = typeof calls.$inferSelect;
export type ConversationEntity = typeof conversationEntities.$inferSelect;
export type User = typeof users.$inferSelect;
