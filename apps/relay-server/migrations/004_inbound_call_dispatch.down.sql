-- WI-6 B rollback. Run only with no inbound calls in progress.

BEGIN;

DROP TABLE IF EXISTS inbound_call_dispatch;
DROP INDEX IF EXISTS uq_tenant_call_config_inbound_number;
ALTER TABLE tenant_call_config DROP COLUMN IF EXISTS inbound_number;

COMMIT;
