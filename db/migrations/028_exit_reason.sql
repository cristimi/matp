-- Migration 028: add exit_reason and size_pct to shadow_signals; replace simple unique
-- constraint with a functional index that allows multiple legs per bar/signal combo.
-- exit_reason: "tp1"|"tp2"|"stop"|"be_stop"|"trail" for bracket exits; NULL for entry signals
-- size_pct: fraction of position exited on this leg; NULL for entry signals

ALTER TABLE public.shadow_signals
    ADD COLUMN IF NOT EXISTS exit_reason varchar(20),
    ADD COLUMN IF NOT EXISTS size_pct    numeric;

-- Replace the old 3-column unique constraint with a functional 4-key index so that
-- multiple bracket legs (tp1, tp2, stop…) on the same bar can coexist while still
-- deduplicating re-emits of the same leg on the same bar.
ALTER TABLE public.shadow_signals
    DROP CONSTRAINT IF EXISTS shadow_signals_strategy_id_signal_signal_bar_time_key;

CREATE UNIQUE INDEX IF NOT EXISTS shadow_signals_uniq_exit
    ON public.shadow_signals (strategy_id, signal, signal_bar_time, COALESCE(exit_reason, ''));

-- Verification
DO $$
DECLARE miss INT;
BEGIN
  SELECT COUNT(*) INTO miss FROM (
    SELECT 1 WHERE NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema='public' AND table_name='shadow_signals' AND column_name='exit_reason'
    )
    UNION ALL
    SELECT 1 WHERE NOT EXISTS (
      SELECT 1 FROM pg_indexes
      WHERE schemaname='public' AND tablename='shadow_signals' AND indexname='shadow_signals_uniq_exit'
    )
  ) x;
  IF miss > 0 THEN RAISE EXCEPTION 'Migration 028 verification failed (% missing objects)', miss; END IF;
  RAISE NOTICE 'Migration 028 verified OK';
END $$;
