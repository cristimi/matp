-- Migration 020: drop ai_strategy_config.emergency_exit_pct
-- The price_monitor emergency-exit feature was removed in commit e3c9efc.
-- This column has been dead (no readers/writers) since that commit.

ALTER TABLE public.ai_strategy_config
  DROP COLUMN IF EXISTS emergency_exit_pct;

ALTER TABLE tester.ai_strategy_config
  DROP COLUMN IF EXISTS emergency_exit_pct;

DO $$
DECLARE
  pub_cols INT;
  tst_cols INT;
BEGIN
  SELECT COUNT(*) INTO pub_cols
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name   = 'ai_strategy_config'
    AND column_name  = 'emergency_exit_pct';

  SELECT COUNT(*) INTO tst_cols
  FROM information_schema.columns
  WHERE table_schema = 'tester'
    AND table_name   = 'ai_strategy_config'
    AND column_name  = 'emergency_exit_pct';

  IF pub_cols > 0 THEN
    RAISE EXCEPTION 'Migration 020: emergency_exit_pct still present in public.ai_strategy_config';
  END IF;
  IF tst_cols > 0 THEN
    RAISE EXCEPTION 'Migration 020: emergency_exit_pct still present in tester.ai_strategy_config';
  END IF;
  RAISE NOTICE 'Migration 020 verified OK — emergency_exit_pct gone from both schemas';
END $$;
