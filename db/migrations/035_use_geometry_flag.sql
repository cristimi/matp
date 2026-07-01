-- Migration 035: add use_geometry boolean to ai_strategy_config.
-- Controls whether the geometry detection pipeline runs for a strategy.
-- Defaults FALSE so all existing strategies are unaffected.

BEGIN;

ALTER TABLE public.ai_strategy_config
    ADD COLUMN IF NOT EXISTS use_geometry boolean DEFAULT false NOT NULL;

COMMIT;

-- Self-verification
DO $$
DECLARE
    col_exists boolean;
    col_default text;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'ai_strategy_config'
          AND column_name  = 'use_geometry'
    ) INTO col_exists;

    IF NOT col_exists THEN
        RAISE EXCEPTION 'Migration 035 FAILED: use_geometry column not found in ai_strategy_config';
    END IF;

    SELECT column_default
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name   = 'ai_strategy_config'
      AND column_name  = 'use_geometry'
    INTO col_default;

    IF col_default IS NULL OR col_default NOT LIKE '%false%' THEN
        RAISE EXCEPTION 'Migration 035 FAILED: use_geometry default is not false (got: %)', col_default;
    END IF;

    RAISE NOTICE 'Migration 035 verified OK: use_geometry column present, default=false';
END $$;
