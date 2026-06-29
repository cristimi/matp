-- Migration 032: add stop_reason to strategies
-- Records why a strategy was disabled: 'user' | 'drawdown' | NULL (unknown/re-enabled)
-- No CHECK constraint so future values are allowed without a migration.

BEGIN;

ALTER TABLE public.strategies
    ADD COLUMN IF NOT EXISTS stop_reason VARCHAR;

COMMIT;

-- Self-verification
DO $$
DECLARE
    cnt INT;
BEGIN
    SELECT COUNT(*) INTO cnt
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name   = 'strategies'
      AND column_name  = 'stop_reason';
    IF cnt = 0 THEN
        RAISE EXCEPTION 'Migration 032 FAILED: strategies.stop_reason column not found';
    END IF;
    RAISE NOTICE 'Migration 032 verified OK';
END $$;
