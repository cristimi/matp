-- Migration 042: add fired_at to shadow_signals — the actual timestamp the engine
-- emitted the signal, distinct from signal_bar_time (which bar it belongs to).
-- For intrabar entries, signal_bar_time is just the hour's open-time, so every
-- intrabar entry in an hour looked identical to the diff harness. fired_at gives
-- entries a real fire-time to match on, same as cmd_exits already does for closes.

BEGIN;

ALTER TABLE public.shadow_signals
    ADD COLUMN IF NOT EXISTS fired_at timestamptz;

-- Backfill existing rows: coarse (bar time) but honest — only new rows get a
-- precise fire-time from the writer going forward.
UPDATE public.shadow_signals
    SET fired_at = signal_bar_time
    WHERE fired_at IS NULL;

ALTER TABLE public.shadow_signals
    ALTER COLUMN fired_at SET DEFAULT now();

ALTER TABLE public.shadow_signals
    ALTER COLUMN fired_at SET NOT NULL;

COMMIT;

-- Self-verification
DO $$
DECLARE
    col_exists    boolean;
    col_default   text;
    col_nullable  text;
    null_count    bigint;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'shadow_signals'
          AND column_name  = 'fired_at'
    ) INTO col_exists;

    IF NOT col_exists THEN
        RAISE EXCEPTION 'Migration 042 FAILED: fired_at column not found in shadow_signals';
    END IF;

    SELECT column_default, is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name   = 'shadow_signals'
      AND column_name  = 'fired_at'
    INTO col_default, col_nullable;

    IF col_default IS NULL OR col_default NOT LIKE '%now(%' THEN
        RAISE EXCEPTION 'Migration 042 FAILED: fired_at default is not now() (got: %)', col_default;
    END IF;

    IF col_nullable <> 'NO' THEN
        RAISE EXCEPTION 'Migration 042 FAILED: fired_at is nullable, expected NOT NULL';
    END IF;

    SELECT count(*) INTO null_count FROM public.shadow_signals WHERE fired_at IS NULL;
    IF null_count <> 0 THEN
        RAISE EXCEPTION 'Migration 042 FAILED: % rows still have NULL fired_at', null_count;
    END IF;

    RAISE NOTICE 'Migration 042 verified OK: fired_at column present, NOT NULL, default=now(), 0 NULL rows';
END $$;
