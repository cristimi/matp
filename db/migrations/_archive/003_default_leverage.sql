-- Migration 003: default_leverage
-- Adds default_leverage to strategies table.
-- Used when a webhook signal does not specify a leverage value.

ALTER TABLE strategies
  ADD COLUMN IF NOT EXISTS default_leverage INTEGER NOT NULL DEFAULT 1;

-- Verify
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'strategies'
    AND column_name = 'default_leverage'
  ) THEN
    RAISE EXCEPTION 'default_leverage column was not created';
  END IF;
  RAISE NOTICE 'Migration 003 verified OK';
END $$;
