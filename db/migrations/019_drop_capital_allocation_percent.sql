-- Migration 019: drop strategies.capital_allocation_percent
-- Superseded by capital_allocation ($ bankroll, migration 016).
-- total_return in dashboard-api now divides by capital_allocation instead.

ALTER TABLE public.strategies
  DROP COLUMN IF EXISTS capital_allocation_percent;

ALTER TABLE tester.strategies
  DROP COLUMN IF EXISTS capital_allocation_percent;

DO $$
DECLARE
  pub_cols INT;
  tst_cols INT;
BEGIN
  SELECT COUNT(*) INTO pub_cols
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name   = 'strategies'
    AND column_name  = 'capital_allocation_percent';

  SELECT COUNT(*) INTO tst_cols
  FROM information_schema.columns
  WHERE table_schema = 'tester'
    AND table_name   = 'strategies'
    AND column_name  = 'capital_allocation_percent';

  IF pub_cols > 0 THEN
    RAISE EXCEPTION 'Migration 019: capital_allocation_percent still present in public.strategies';
  END IF;
  IF tst_cols > 0 THEN
    RAISE EXCEPTION 'Migration 019: capital_allocation_percent still present in tester.strategies';
  END IF;
  RAISE NOTICE 'Migration 019 verified OK — capital_allocation_percent gone from both schemas';
END $$;
