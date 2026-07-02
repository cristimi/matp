-- Migration 037: add candle_close_buffer_seconds to ai_strategy_config.
-- Number of seconds past a candle-close wall-clock boundary the scheduler waits
-- before waking, to give the exchange time to finalize the candle.
-- Default 150s (2.5min). Bounded [0, 600]: 0 means "wake exactly at the boundary"
-- (no safety margin, allowed but not recommended); 600s (10min) is a generous
-- upper bound — beyond that the buffer would eat a meaningful fraction of even
-- the shortest supported interval (1m/5m polling).

BEGIN;

ALTER TABLE public.ai_strategy_config
    ADD COLUMN IF NOT EXISTS candle_close_buffer_seconds integer DEFAULT 150 NOT NULL;

ALTER TABLE public.ai_strategy_config
    ADD CONSTRAINT ai_strategy_config_candle_close_buffer_chk
        CHECK (candle_close_buffer_seconds >= 0 AND candle_close_buffer_seconds <= 600);

COMMIT;

-- Self-verification
DO $$
DECLARE
    col_exists boolean;
    col_default text;
    chk_exists boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'ai_strategy_config'
          AND column_name  = 'candle_close_buffer_seconds'
    ) INTO col_exists;

    IF NOT col_exists THEN
        RAISE EXCEPTION 'Migration 037 FAILED: candle_close_buffer_seconds column not found in ai_strategy_config';
    END IF;

    SELECT column_default
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name   = 'ai_strategy_config'
      AND column_name  = 'candle_close_buffer_seconds'
    INTO col_default;

    IF col_default IS NULL OR col_default NOT LIKE '%150%' THEN
        RAISE EXCEPTION 'Migration 037 FAILED: candle_close_buffer_seconds default is not 150 (got: %)', col_default;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ai_strategy_config_candle_close_buffer_chk'
    ) INTO chk_exists;

    IF NOT chk_exists THEN
        RAISE EXCEPTION 'Migration 037 FAILED: bound check constraint missing';
    END IF;

    RAISE NOTICE 'Migration 037 verified OK: candle_close_buffer_seconds column present, default=150, bound check [0,600] present';
END $$;
