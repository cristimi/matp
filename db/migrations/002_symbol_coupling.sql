-- Migration 002: Symbol Coupling
-- Adds allow_quote_variants and allow_cross_charting flags to strategies.
-- Safe to run multiple times (IF NOT EXISTS / DO NOTHING pattern).

ALTER TABLE strategies
  ADD COLUMN IF NOT EXISTS allow_quote_variants BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE strategies
  ADD COLUMN IF NOT EXISTS allow_cross_charting BOOLEAN NOT NULL DEFAULT FALSE;

-- Verify
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'strategies'
    AND column_name = 'allow_quote_variants'
  ) THEN
    RAISE EXCEPTION 'allow_quote_variants column was not created';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'strategies'
    AND column_name = 'allow_cross_charting'
  ) THEN
    RAISE EXCEPTION 'allow_cross_charting column was not created';
  END IF;
  RAISE NOTICE 'Migration 002 verified OK';
END $$;
