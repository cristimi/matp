-- Migration 018: drop strategies.max_daily_drawdown_percent
-- Guard 4 (daily drawdown stop) has been removed; the cumulative stop on
-- strategies.max_drawdown_pct (migration 016) is the only drawdown mechanism.

ALTER TABLE public.strategies
  DROP COLUMN IF EXISTS max_daily_drawdown_percent;

ALTER TABLE tester.strategies
  DROP COLUMN IF EXISTS max_daily_drawdown_percent;

DO $$
DECLARE
  pub_cols INT;
  tst_cols INT;
BEGIN
  SELECT COUNT(*) INTO pub_cols
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name   = 'strategies'
    AND column_name  = 'max_daily_drawdown_percent';

  SELECT COUNT(*) INTO tst_cols
  FROM information_schema.columns
  WHERE table_schema = 'tester'
    AND table_name   = 'strategies'
    AND column_name  = 'max_daily_drawdown_percent';

  IF pub_cols > 0 THEN
    RAISE EXCEPTION 'Migration 018: max_daily_drawdown_percent still present in public.strategies';
  END IF;
  IF tst_cols > 0 THEN
    RAISE EXCEPTION 'Migration 018: max_daily_drawdown_percent still present in tester.strategies';
  END IF;
  RAISE NOTICE 'Migration 018 verified OK — max_daily_drawdown_percent gone from both schemas';
END $$;
