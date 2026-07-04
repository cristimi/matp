-- Migration 039: add entry_trigger to strategies (public + tester).
-- entry_trigger: 'bar_close' | 'intrabar' — when a strategy evaluates ENTRIES.
--   bar_close (default) = evaluate entries only on closed bars (current behavior).
--   intrabar            = evaluate entries on the near-tick forming-candle feed.
-- PHASE 1 WIRES THE FLAG ONLY. Intrabar evaluation is not implemented yet, so the
-- value is inert until a later phase. Default 'bar_close' leaves every existing
-- strategy behaving exactly as before.

BEGIN;

ALTER TABLE public.strategies
    ADD COLUMN IF NOT EXISTS entry_trigger varchar(16) DEFAULT 'bar_close' NOT NULL;
ALTER TABLE tester.strategies
    ADD COLUMN IF NOT EXISTS entry_trigger varchar(16) DEFAULT 'bar_close' NOT NULL;

ALTER TABLE public.strategies
    ADD CONSTRAINT strategies_entry_trigger_chk CHECK (entry_trigger IN ('bar_close','intrabar'));
ALTER TABLE tester.strategies
    ADD CONSTRAINT strategies_entry_trigger_chk CHECK (entry_trigger IN ('bar_close','intrabar'));

COMMIT;

-- Self-verification
DO $$
DECLARE n_cols int; n_chks int; ok_default boolean;
BEGIN
    SELECT count(*) INTO n_cols FROM information_schema.columns
     WHERE table_name='strategies' AND column_name='entry_trigger'
       AND table_schema IN ('public','tester');
    IF n_cols <> 2 THEN
        RAISE EXCEPTION 'Migration 039 FAILED: entry_trigger expected on 2 tables, found %', n_cols;
    END IF;

    SELECT count(*) INTO n_chks FROM information_schema.table_constraints
     WHERE constraint_name='strategies_entry_trigger_chk' AND table_schema IN ('public','tester');
    IF n_chks <> 2 THEN
        RAISE EXCEPTION 'Migration 039 FAILED: expected 2 CHECK constraints, found %', n_chks;
    END IF;

    SELECT (column_default LIKE '%bar_close%') INTO ok_default
     FROM information_schema.columns
     WHERE table_schema='public' AND table_name='strategies' AND column_name='entry_trigger';
    IF NOT COALESCE(ok_default,false) THEN
        RAISE EXCEPTION 'Migration 039 FAILED: default is not bar_close';
    END IF;

    RAISE NOTICE 'Migration 039 OK: entry_trigger on public+tester strategies, default bar_close';
END $$;
