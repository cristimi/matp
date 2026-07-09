-- Migration 050: reduce candle_close_buffer_seconds default 150 -> 10.
-- The buffer only exists to let the exchange finalize the just-closed candle
-- before the cycle fetches OHLCV. Investigation of the 2026-07-09 instant-stop
-- incident showed exchanges publish the closed candle within ~1-2s of the
-- boundary, and ohlcv.py's _split_closed_candles() already drops the
-- still-forming candle by timestamp — the 150s buffer added pure decision-to-
-- order latency for no correctness benefit. 10s keeps a comfortable margin.

BEGIN;

ALTER TABLE public.ai_strategy_config
    ALTER COLUMN candle_close_buffer_seconds SET DEFAULT 10;

-- Move existing strategies still on the old default down to the new one.
-- Anything deliberately set to a non-150 value is left alone.
UPDATE public.ai_strategy_config
   SET candle_close_buffer_seconds = 10
 WHERE candle_close_buffer_seconds = 150;

COMMIT;

-- Self-verification
DO $$
DECLARE
    col_default text;
    remaining integer;
BEGIN
    SELECT column_default
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name   = 'ai_strategy_config'
      AND column_name  = 'candle_close_buffer_seconds'
    INTO col_default;

    IF col_default IS NULL OR col_default NOT LIKE '%10%' OR col_default LIKE '%150%' THEN
        RAISE EXCEPTION 'Migration 050 FAILED: candle_close_buffer_seconds default is not 10 (got: %)', col_default;
    END IF;

    SELECT count(*) FROM public.ai_strategy_config
     WHERE candle_close_buffer_seconds = 150
    INTO remaining;

    IF remaining > 0 THEN
        RAISE EXCEPTION 'Migration 050 FAILED: % row(s) still at the old 150s buffer', remaining;
    END IF;

    RAISE NOTICE 'Migration 050 verified OK: default=10, no rows left at 150s';
END $$;
