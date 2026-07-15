-- WI-3: multi-tenant foundation (forward migration)
-- Run only while active call count is zero.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenants (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_call_config (
  tenant_id        uuid        PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  outbound_number  text        NOT NULL DEFAULT '',
  provider         text        NOT NULL DEFAULT 'twilio',
  prompt_overrides jsonb       NOT NULL DEFAULT '{}'::jsonb,
  languages        jsonb       NOT NULL DEFAULT '[]'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Stable PoC tenant used only to backfill the pre-tenant dataset and to
-- auto-provision existing single-tenant users. Operators should set its
-- outbound_number to TWILIO_PHONE_NUMBER before enabling tenant enforcement.
INSERT INTO tenants (id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'WIGVO Default')
ON CONFLICT (id) DO NOTHING;

INSERT INTO tenant_call_config (tenant_id)
VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (tenant_id) DO NOTHING;

-- Expand → backfill → constrain. Keeping the phases explicit avoids a table
-- rewrite with a volatile default and makes failed backfills visible.
ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS tenant_id uuid;

UPDATE users
SET tenant_id = '00000000-0000-0000-0000-000000000001'
WHERE tenant_id IS NULL;

UPDATE conversations c
SET tenant_id = COALESCE(u.tenant_id, '00000000-0000-0000-0000-000000000001')
FROM users u
WHERE c.user_id = u.id AND c.tenant_id IS NULL;

UPDATE conversations
SET tenant_id = '00000000-0000-0000-0000-000000000001'
WHERE tenant_id IS NULL;

UPDATE calls c
SET tenant_id = conv.tenant_id
FROM conversations conv
WHERE c.conversation_id = conv.id AND c.tenant_id IS NULL;

UPDATE calls c
SET tenant_id = u.tenant_id
FROM users u
WHERE c.user_id = u.id AND c.tenant_id IS NULL;

UPDATE calls
SET tenant_id = '00000000-0000-0000-0000-000000000001'
WHERE tenant_id IS NULL;

ALTER TABLE users ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE conversations ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE calls ALTER COLUMN tenant_id SET NOT NULL;

ALTER TABLE users
  ADD CONSTRAINT users_tenant_id_fkey
  FOREIGN KEY (tenant_id) REFERENCES tenants(id);
ALTER TABLE conversations
  ADD CONSTRAINT conversations_tenant_id_fkey
  FOREIGN KEY (tenant_id) REFERENCES tenants(id);
ALTER TABLE calls
  ADD CONSTRAINT calls_tenant_id_fkey
  FOREIGN KEY (tenant_id) REFERENCES tenants(id);

CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users (tenant_id);
CREATE INDEX IF NOT EXISTS idx_conversations_tenant_id ON conversations (tenant_id);
CREATE INDEX IF NOT EXISTS idx_calls_tenant_id ON calls (tenant_id);
