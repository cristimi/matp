-- Migration 017: drop daily-loss cap + old AI-gate drawdown from ai_risk_config
-- The cumulative drawdown stop now lives on strategies.max_drawdown_pct (migration 016).
-- The daily-loss cap concept is removed entirely.

ALTER TABLE public.ai_risk_config
  DROP COLUMN IF EXISTS max_daily_loss_pct,
  DROP COLUMN IF EXISTS max_drawdown_pct;

ALTER TABLE tester.ai_risk_config
  DROP COLUMN IF EXISTS max_daily_loss_pct,
  DROP COLUMN IF EXISTS max_drawdown_pct;

DO $$
DECLARE
  pub_cols INT;
  tst_cols INT;
BEGIN
  SELECT COUNT(*) INTO pub_cols
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name   = 'ai_risk_config'
    AND column_name  IN ('max_daily_loss_pct', 'max_drawdown_pct');

  SELECT COUNT(*) INTO tst_cols
  FROM information_schema.columns
  WHERE table_schema = 'tester'
    AND table_name   = 'ai_risk_config'
    AND column_name  IN ('max_daily_loss_pct', 'max_drawdown_pct');

  IF pub_cols > 0 THEN
    RAISE EXCEPTION 'Migration 017: % column(s) still present in public.ai_risk_config', pub_cols;
  END IF;
  IF tst_cols > 0 THEN
    RAISE EXCEPTION 'Migration 017: % column(s) still present in tester.ai_risk_config', tst_cols;
  END IF;
  RAISE NOTICE 'Migration 017 verified OK — daily-loss columns gone from both schemas';
END $$;
