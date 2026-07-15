-- WIGVO Local Postgres Schema
-- Reconstructed from the previous Supabase project (tkwalyeezmhaizpgpmqt).
-- Auth (auth.users) lives in the wigsso Supabase project; we only store user_id
-- as a UUID reference, no FK. RLS is intentionally omitted — application-layer
-- access control runs in the Next.js API routes.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenants (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_call_config (
  tenant_id        uuid        PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  outbound_number  text        NOT NULL DEFAULT '',
  inbound_number   text,
  provider         text        NOT NULL DEFAULT 'twilio',
  prompt_overrides jsonb       NOT NULL DEFAULT '{}'::jsonb,
  languages        jsonb       NOT NULL DEFAULT '[]'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_call_config_inbound_number
  ON tenant_call_config (inbound_number)
  WHERE inbound_number IS NOT NULL AND inbound_number <> '';

INSERT INTO tenants (id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'WIGVO Default')
ON CONFLICT (id) DO NOTHING;

INSERT INTO tenant_call_config (tenant_id)
VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (tenant_id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- users — service-level user table (mirrors wigex pattern).
-- id matches wigsso auth.users.id. Rows are auto-provisioned on first request
-- with a valid wigsso JWT (see web auth guard). Use deleted_at to revoke
-- access without touching wigsso.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id          uuid        PRIMARY KEY,
  tenant_id   uuid        NOT NULL REFERENCES tenants(id),
  email       text,
  name        text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users (tenant_id);

-- ----------------------------------------------------------------------------
-- conversations
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid        NOT NULL REFERENCES tenants(id),
  user_id         uuid        NOT NULL,
  status          text        NOT NULL DEFAULT 'COLLECTING',
  collected_data  jsonb       NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_id    ON conversations (user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_tenant_id  ON conversations (tenant_id);
CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations (created_at DESC);

-- ----------------------------------------------------------------------------
-- messages
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id  uuid        NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role             text        NOT NULL,
  content          text        NOT NULL,
  metadata         jsonb       NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages (conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at      ON messages (created_at);

-- ----------------------------------------------------------------------------
-- calls
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calls (
  id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             uuid        NOT NULL REFERENCES tenants(id),
  conversation_id       uuid        REFERENCES conversations(id) ON DELETE SET NULL,
  user_id               uuid        NOT NULL,
  request_type          text        NOT NULL DEFAULT 'RESERVATION',
  target_phone          text,
  target_name           text,
  parsed_date           text,
  parsed_time           text,
  parsed_service        text,
  status                text        NOT NULL DEFAULT 'PENDING',
  result                text,
  summary               text,
  call_id               text,
  call_mode             text        NOT NULL DEFAULT 'agent',
  relay_ws_url          text,
  call_sid              text,
  source_language       text        NOT NULL DEFAULT 'en',
  target_language       text        NOT NULL DEFAULT 'ko',
  communication_mode    text,
  transcript_bilingual  jsonb       NOT NULL DEFAULT '[]'::jsonb,
  cost_tokens           jsonb       NOT NULL DEFAULT '{}'::jsonb,
  guardrail_events      jsonb       NOT NULL DEFAULT '[]'::jsonb,
  recovery_events       jsonb       NOT NULL DEFAULT '[]'::jsonb,
  function_call_logs    jsonb       NOT NULL DEFAULT '[]'::jsonb,
  call_result           text,
  call_result_data      jsonb       NOT NULL DEFAULT '{}'::jsonb,
  auto_ended            boolean     NOT NULL DEFAULT false,
  duration_s            real,
  total_tokens          integer     NOT NULL DEFAULT 0,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  completed_at          timestamptz
);

CREATE INDEX IF NOT EXISTS idx_calls_user_id         ON calls (user_id);
CREATE INDEX IF NOT EXISTS idx_calls_tenant_id       ON calls (tenant_id);
CREATE INDEX IF NOT EXISTS idx_calls_conversation_id ON calls (conversation_id);
CREATE INDEX IF NOT EXISTS idx_calls_call_mode       ON calls (call_mode);
CREATE INDEX IF NOT EXISTS idx_calls_created_at      ON calls (created_at DESC);

-- ----------------------------------------------------------------------------
-- inbound_call_dispatch — WI-6 tenant FIFO + atomic pickup source of truth
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inbound_call_dispatch (
  call_id           uuid        PRIMARY KEY,
  tenant_id         uuid        NOT NULL REFERENCES tenants(id),
  provider_call_sid text        UNIQUE,
  state             text        NOT NULL DEFAULT 'RINGING',
  claimed_by        uuid        REFERENCES users(id),
  claim_expires_at  timestamptz,
  connected_at      timestamptz,
  ended_at          timestamptz,
  end_reason        text,
  version           integer     NOT NULL DEFAULT 0,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT inbound_call_dispatch_state_check CHECK (
    state IN ('RINGING', 'WAITING_FOR_AGENT', 'CLAIMED', 'SESSION_STARTING',
      'CONNECTED', 'ENDED', 'CANCELLED', 'TIMEOUT', 'REJECTED')
  ),
  CONSTRAINT inbound_call_dispatch_claim_check CHECK (
    (state IN ('CLAIMED', 'SESSION_STARTING', 'CONNECTED') AND claimed_by IS NOT NULL)
    OR (state NOT IN ('CLAIMED', 'SESSION_STARTING', 'CONNECTED'))
  ),
  CONSTRAINT inbound_call_dispatch_end_check CHECK (
    (state IN ('ENDED', 'CANCELLED', 'TIMEOUT', 'REJECTED')
      AND ended_at IS NOT NULL AND end_reason IS NOT NULL)
    OR (state NOT IN ('ENDED', 'CANCELLED', 'TIMEOUT', 'REJECTED'))
  )
);

CREATE INDEX IF NOT EXISTS idx_inbound_dispatch_tenant_waiting_fifo
  ON inbound_call_dispatch (tenant_id, created_at, call_id)
  WHERE state = 'WAITING_FOR_AGENT';
CREATE INDEX IF NOT EXISTS idx_inbound_dispatch_claim_expiry
  ON inbound_call_dispatch (claim_expires_at)
  WHERE state = 'CLAIMED';
CREATE INDEX IF NOT EXISTS idx_inbound_dispatch_tenant_id
  ON inbound_call_dispatch (tenant_id);

-- ----------------------------------------------------------------------------
-- conversation_entities
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation_entities (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id   uuid        NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  entity_type       text        NOT NULL,
  entity_value      text        NOT NULL,
  confidence        double precision NOT NULL DEFAULT 1.0,
  source_message_id uuid        REFERENCES messages(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_entities_conversation_id ON conversation_entities (conversation_id);
CREATE INDEX IF NOT EXISTS idx_entities_type            ON conversation_entities (entity_type);

-- (conversation_id, entity_type) uniqueness mirrors the application-level
-- upsert key used by extractAndSaveEntities().
CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_conv_type
  ON conversation_entities (conversation_id, entity_type);

-- ----------------------------------------------------------------------------
-- place_search_cache
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS place_search_cache (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  query_hash  text        NOT NULL UNIQUE,
  query_text  text        NOT NULL,
  results     jsonb       NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL DEFAULT (now() + interval '7 days')
);

CREATE INDEX IF NOT EXISTS idx_place_cache_hash       ON place_search_cache (query_hash);
CREATE INDEX IF NOT EXISTS idx_place_cache_expires_at ON place_search_cache (expires_at);

-- ----------------------------------------------------------------------------
-- updated_at trigger
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
SET search_path = '';

DROP TRIGGER IF EXISTS trg_users_updated_at                ON users;
DROP TRIGGER IF EXISTS trg_conversations_updated_at        ON conversations;
DROP TRIGGER IF EXISTS trg_calls_updated_at                ON calls;
DROP TRIGGER IF EXISTS trg_conversation_entities_updated_at ON conversation_entities;
DROP TRIGGER IF EXISTS trg_inbound_call_dispatch_updated_at ON inbound_call_dispatch;

CREATE TRIGGER trg_users_updated_at
  BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_conversations_updated_at
  BEFORE UPDATE ON conversations
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_calls_updated_at
  BEFORE UPDATE ON calls
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_conversation_entities_updated_at
  BEFORE UPDATE ON conversation_entities
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_inbound_call_dispatch_updated_at
  BEFORE UPDATE ON inbound_call_dispatch
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ----------------------------------------------------------------------------
-- Cache cleanup (was a Supabase cron RPC; can be invoked manually or via cron)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cleanup_expired_cache() RETURNS integer AS $$
DECLARE
  deleted integer;
BEGIN
  DELETE FROM public.place_search_cache WHERE expires_at < now();
  GET DIAGNOSTICS deleted = ROW_COUNT;
  RETURN deleted;
END;
$$ LANGUAGE plpgsql
SET search_path = '';
