-- Migration 044: add geometry_data to ai_signal_log for diagnostics.
-- Persists the exact geometry_data dict the pipeline had on each cycle (as produced
-- by detect_geometry() in node_ingest), so the state of the GEOMETRIC PATTERN input
-- can be inspected from the log alone, on every row including HOLD/rejected ones.
-- The full prompt is deliberately NOT persisted here — it would bloat the table every
-- cycle; geometry_data alone closes the diagnostic gap.

BEGIN;

ALTER TABLE public.ai_signal_log ADD COLUMN geometry_data jsonb;

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
          AND column_name  = 'geometry_data'
    ) INTO col_exists;

    IF NOT col_exists THEN
        RAISE EXCEPTION 'Migration 044 FAILED: ai_signal_log.geometry_data column not found';
    END IF;

    RAISE NOTICE 'Migration 044 verified OK: ai_signal_log.geometry_data column exists';
END $$;
