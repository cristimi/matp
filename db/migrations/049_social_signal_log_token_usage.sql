-- Migration 049: actual LLM token usage per social-listener extraction call.
-- Mirrors migration 047 (ai_signal_log) — provider-reported usage_metadata
-- (input, output incl. thinking, total), not a chars/4 estimate.
-- NULL on historical rows and on calls that failed before a response.

BEGIN;

ALTER TABLE public.social_signal_log
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
          AND table_name   = 'social_signal_log'
          AND column_name  = t.col
    );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 049 FAILED: missing columns: %', missing;
    END IF;

    RAISE NOTICE 'Migration 049 verified OK: token-usage columns present on social_signal_log';
END $$;
