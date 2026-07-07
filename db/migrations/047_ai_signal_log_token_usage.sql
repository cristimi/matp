-- Migration 047: actual LLM token usage per signal cycle.
-- context_tokens was a chars/4 input ESTIMATE; these are the provider-reported
-- actuals (usage_metadata) — input, output (incl. thinking), and total — so
-- spend can be traced per call, per strategy, and in aggregate.
-- NULL on historical rows and on calls that failed before a response.

BEGIN;

ALTER TABLE public.ai_signal_log
    ADD COLUMN IF NOT EXISTS input_tokens  integer,
    ADD COLUMN IF NOT EXISTS output_tokens integer,
    ADD COLUMN IF NOT EXISTS total_tokens  integer;

COMMIT;

-- Self-verification
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(t.col, ', ')
    INTO missing
    FROM (VALUES ('input_tokens'), ('output_tokens'), ('total_tokens')) AS t(col)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'ai_signal_log'
          AND column_name  = t.col
    );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 047 FAILED: missing columns: %', missing;
    END IF;

    RAISE NOTICE 'Migration 047 verified OK: token-usage columns present on ai_signal_log';
END $$;
