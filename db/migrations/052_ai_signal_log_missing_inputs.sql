-- Migration 052: ai_signal_log.missing_inputs — enabled data sources that came
-- back empty on a given cycle (the gap between data_sources_used, i.e. what was
-- REQUESTED via use_* config flags, and what actually made it into the prompt).
-- Computed in node_dispatch.py::_missing_inputs(). NULL/empty on historical rows
-- and on cycles where every enabled source resolved.

BEGIN;

ALTER TABLE public.ai_signal_log ADD COLUMN IF NOT EXISTS missing_inputs text[];

COMMIT;

-- Self-verification
DO $$
DECLARE
    col_exists boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'ai_signal_log'
          AND column_name  = 'missing_inputs'
    ) INTO col_exists;

    IF NOT col_exists THEN
        RAISE EXCEPTION 'Migration 052 FAILED: ai_signal_log.missing_inputs column not found';
    END IF;

    RAISE NOTICE 'Migration 052 verified OK: ai_signal_log.missing_inputs column exists';
END $$;
