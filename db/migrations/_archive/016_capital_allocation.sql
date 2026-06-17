-- Migration 016: capital allocation foundation
-- Adds fixed-margin sizing and cumulative drawdown stop to strategies.
-- Note: capital_allocation_percent (legacy %) and max_daily_drawdown_percent (daily)
--       remain as-is; the new columns are absolute-$ / cumulative replacements.

ALTER TABLE strategies
  ADD COLUMN IF NOT EXISTS capital_allocation  NUMERIC NOT NULL DEFAULT 100,
  ADD COLUMN IF NOT EXISTS margin_per_trade    NUMERIC NOT NULL DEFAULT 5,
  ADD COLUMN IF NOT EXISTS max_drawdown_pct    NUMERIC NOT NULL DEFAULT 50,
  ADD COLUMN IF NOT EXISTS drawdown_anchor_pnl NUMERIC NOT NULL DEFAULT 0;

DO $$
DECLARE
  cnt INT;
BEGIN
  SELECT COUNT(*) INTO cnt
  FROM information_schema.columns
  WHERE table_name  = 'strategies'
    AND column_name IN ('capital_allocation', 'margin_per_trade', 'max_drawdown_pct', 'drawdown_anchor_pnl');
  IF cnt < 4 THEN
    RAISE EXCEPTION 'Migration 016: only % of 4 new columns present', cnt;
  END IF;
  RAISE NOTICE 'Migration 016 verified OK (4 new columns on strategies)';
END $$;
