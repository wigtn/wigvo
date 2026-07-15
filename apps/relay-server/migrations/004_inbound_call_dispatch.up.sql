-- WI-6 B: tenant-routed inbound dispatch and atomic pickup state.
-- The relay's server-side Postgres connection is the only data path. Browser
-- clients use authenticated relay endpoints; the Supabase Data API stays shut.

BEGIN;

ALTER TABLE tenant_call_config
  ADD COLUMN IF NOT EXISTS inbound_number text;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_call_config_inbound_number
  ON tenant_call_config (inbound_number)
  WHERE inbound_number IS NOT NULL AND inbound_number <> '';

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
    state IN (
      'RINGING',
      'WAITING_FOR_AGENT',
      'CLAIMED',
      'SESSION_STARTING',
      'CONNECTED',
      'ENDED',
      'CANCELLED',
      'TIMEOUT',
      'REJECTED'
    )
  ),
  CONSTRAINT inbound_call_dispatch_claim_check CHECK (
    (state IN ('CLAIMED', 'SESSION_STARTING', 'CONNECTED')
      AND claimed_by IS NOT NULL)
    OR
    (state NOT IN ('CLAIMED', 'SESSION_STARTING', 'CONNECTED'))
  ),
  CONSTRAINT inbound_call_dispatch_end_check CHECK (
    (state IN ('ENDED', 'CANCELLED', 'TIMEOUT', 'REJECTED')
      AND ended_at IS NOT NULL AND end_reason IS NOT NULL)
    OR
    (state NOT IN ('ENDED', 'CANCELLED', 'TIMEOUT', 'REJECTED'))
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

DROP TRIGGER IF EXISTS trg_inbound_call_dispatch_updated_at
  ON inbound_call_dispatch;
CREATE TRIGGER trg_inbound_call_dispatch_updated_at
  BEFORE UPDATE ON inbound_call_dispatch
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE inbound_call_dispatch ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE inbound_call_dispatch FROM anon, authenticated;

-- WI-3 closed the tenant-bearing tables, but these related public tables were
-- still reachable through the exposed schema. They are also server-only.
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE place_search_cache ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE messages, conversation_entities, place_search_cache
  FROM anon, authenticated;

ALTER FUNCTION public.set_updated_at() SET search_path = '';

CREATE OR REPLACE FUNCTION public.cleanup_expired_cache() RETURNS integer AS $$
DECLARE
  deleted integer;
BEGIN
  DELETE FROM public.place_search_cache WHERE expires_at < now();
  GET DIAGNOSTICS deleted = ROW_COUNT;
  RETURN deleted;
END;
$$ LANGUAGE plpgsql
SET search_path = '';

REVOKE EXECUTE ON FUNCTION public.cleanup_expired_cache() FROM PUBLIC, anon, authenticated;

COMMIT;
