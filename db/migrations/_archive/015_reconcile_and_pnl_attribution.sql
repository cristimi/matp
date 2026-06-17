-- Migration 015: reconciler miss-streak + attribute close orders to positions
ALTER TABLE strategy_positions
  ADD COLUMN IF NOT EXISTS reconcile_miss_count INTEGER NOT NULL DEFAULT 0;

-- Every closing order (full or partial) records which position it closed,
-- so realized PnL can be summed per position (partial-safe).
ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS closes_position_id UUID REFERENCES strategy_positions(id);

CREATE INDEX IF NOT EXISTS idx_orders_closes_position
  ON orders (closes_position_id) WHERE closes_position_id IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='strategy_positions' AND column_name='reconcile_miss_count')
     OR NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='orders' AND column_name='closes_position_id') THEN
    RAISE EXCEPTION 'Migration 015 columns missing';
  END IF;
  RAISE NOTICE 'Migration 015 verified OK';
END $$;
