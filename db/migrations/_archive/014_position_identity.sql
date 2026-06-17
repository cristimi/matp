-- Migration 011: enforce one open position per (strategy_id, symbol, side)
CREATE UNIQUE INDEX IF NOT EXISTS uq_strat_pos_one_open
  ON strategy_positions (strategy_id, symbol, side)
  WHERE status = 'open';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'uq_strat_pos_one_open'
  ) THEN
    RAISE EXCEPTION 'uq_strat_pos_one_open was not created';
  END IF;
  RAISE NOTICE 'Migration 011 verified OK';
END $$;
