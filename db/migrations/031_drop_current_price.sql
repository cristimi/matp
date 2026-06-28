-- Migration 031: Drop strategy_positions.current_price
-- The column was set to entry_price at insert time and never refreshed.
-- Live mark prices come from the executor; all dashboard readers now fall
-- back to entry_price. The order-listener INSERT was updated in the same
-- commit to omit this column.

BEGIN;

ALTER TABLE public.strategy_positions
    DROP COLUMN IF EXISTS current_price;

COMMIT;

-- Self-verification: assert the column is gone
DO $$
DECLARE
    exists_count INT;
BEGIN
    SELECT COUNT(*) INTO exists_count
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name   = 'strategy_positions'
      AND column_name  = 'current_price';
    IF exists_count > 0 THEN
        RAISE EXCEPTION 'Migration 031 FAILED: strategy_positions.current_price still exists';
    END IF;
    RAISE NOTICE 'Migration 031 verified OK';
END $$;
