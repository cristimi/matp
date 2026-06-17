-- Migration 021: drop max_position_size from strategies and max_position_size_pct from ai_risk_config
-- Guard 2 (raw-size reject) was removed; the margin clamp handles oversized orders.
-- The LLM no longer receives a position-size % — sizing is fully margin-clamp driven.

ALTER TABLE public.strategies
  DROP COLUMN IF EXISTS max_position_size;

ALTER TABLE public.ai_risk_config
  DROP COLUMN IF EXISTS max_position_size_pct;

DO $$
DECLARE
  strat_cols INT;
  risk_cols  INT;
BEGIN
  SELECT COUNT(*) INTO strat_cols
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name   = 'strategies'
    AND column_name  = 'max_position_size';

  SELECT COUNT(*) INTO risk_cols
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name   = 'ai_risk_config'
    AND column_name  = 'max_position_size_pct';

  IF strat_cols > 0 THEN
    RAISE EXCEPTION 'Migration 021: max_position_size still present in public.strategies';
  END IF;
  IF risk_cols > 0 THEN
    RAISE EXCEPTION 'Migration 021: max_position_size_pct still present in public.ai_risk_config';
  END IF;
  RAISE NOTICE 'Migration 021 verified OK — max_position_size gone from strategies, max_position_size_pct gone from ai_risk_config';
END $$;
