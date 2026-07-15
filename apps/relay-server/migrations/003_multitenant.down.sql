-- WI-3 rollback. This intentionally removes only WI-3 objects/columns.
-- Run only while active call count is zero.

DROP INDEX IF EXISTS idx_calls_tenant_id;
DROP INDEX IF EXISTS idx_conversations_tenant_id;
DROP INDEX IF EXISTS idx_users_tenant_id;

ALTER TABLE calls DROP CONSTRAINT IF EXISTS calls_tenant_id_fkey;
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_tenant_id_fkey;
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_tenant_id_fkey;

ALTER TABLE calls DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE conversations DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE users DROP COLUMN IF EXISTS tenant_id;

DROP TABLE IF EXISTS tenant_call_config;
DROP TABLE IF EXISTS tenants;
