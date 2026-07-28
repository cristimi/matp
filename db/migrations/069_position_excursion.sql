-- Migration 069: MFE/MAE (maximum favourable / adverse excursion) on positions.
--
-- docs/process/reports/2026-07-28-paper-trade-forensics.md §4 found six of ten
-- diagnostic questions unanswerable for one reason: nothing records how far a
-- trade ran in favour or against before it closed. Without that, "the model
-- called direction wrong" and "the model called direction right and the stop was
-- too tight" are indistinguishable — they produce the identical row.
--
-- The forensics run also showed 15 of 81 AI-engine trades closing at <= -0.9R and
-- 21 sitting in the 0..0.4R band. Which of those were stopped out of a trade that
-- then ran to target cannot be recovered: no price series is stored anywhere
-- (Postgres has no OHLC table; Redis holds only short-lived cvd:* keys), so this
-- CANNOT be backfilled. Existing rows stay NULL. They are not recoverable and
-- must not be invented.
--
-- ── RESOLUTION CAVEAT — READ BEFORE USING THESE COLUMNS ────────────────────────
-- These are populated by sampling the mark price once per reconciler pass
-- (RECONCILE_INTERVAL_SECONDS, default 60s). A wick between two reads is
-- invisible. mfe_price/mae_price are therefore a LOWER BOUND on the true
-- excursion, never the exact extreme:
--
--   * true |MFE| >= |mfe_price - entry|
--   * true |MAE| >= |mae_price - entry|
--
-- A trade whose stop was hit between samples can show an mae_r milder than -1R
-- even though price demonstrably traded through the stop. Read these as "the
-- trade got AT LEAST this far", and always alongside excursion_samples — one
-- sample on a four-hour position says almost nothing; 240 samples says a lot.
--
-- ── SIGN CONVENTION ───────────────────────────────────────────────────────────
-- mfe_r / mae_r are signed in R with FAVOURABLE POSITIVE for both sides:
--   long :  mfe_r = (mfe_price - entry) / risk    mae_r = (mae_price - entry) / risk
--   short:  mfe_r = (entry - mfe_price) / risk    mae_r = (entry - mae_price) / risk
-- so mae_r is usually <= 0 and mfe_r usually >= 0. Both can cross zero, and that is
-- correct rather than a bug:
--   * mfe_r < 0 — the position was never observed in profit at any sampled instant;
--     the best it ever got was still a loss.
--   * mae_r > 0 — the position was never observed at a loss. Expect this on rows
--     whose sampling started mid-life (every position already open on 2026-07-28,
--     the day sampling was switched on) and on trades that ran straight to profit.
-- Compare excursion_first_at against opened_at before reading either number: a row
-- first sampled hours after entry has no record of what happened before that.
--
-- risk = |entry - opening order sl_price|, entry = COALESCE(opening order
-- actual_fill_price, strategy_positions.entry_price). This is deliberately the
-- same R definition the forensics report used, so numbers from before and after
-- this migration stay comparable. Where the opening order or its stop is absent,
-- or the denominator is zero, mfe_r/mae_r stay NULL — the prices are still
-- recorded, only the R normalisation is unavailable.
--
-- Write-only telemetry. Nothing reads these columns to make a trading decision.

ALTER TABLE public.strategy_positions
    ADD COLUMN IF NOT EXISTS mfe_price          numeric,
    ADD COLUMN IF NOT EXISTS mae_price          numeric,
    ADD COLUMN IF NOT EXISTS mfe_r              numeric,
    ADD COLUMN IF NOT EXISTS mae_r              numeric,
    ADD COLUMN IF NOT EXISTS excursion_samples  integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS excursion_first_at timestamptz,
    ADD COLUMN IF NOT EXISTS excursion_last_at  timestamptz;

COMMENT ON COLUMN public.strategy_positions.mfe_price IS
    'Most favourable mark price SEEN while open (higher for a long, lower for a '
    'short). Sampled once per reconciler pass (~60s) — a LOWER BOUND on the true '
    'extreme, not the exact one. NULL = never sampled (all rows before 2026-07-28; '
    'not backfillable, no price history is retained).';
COMMENT ON COLUMN public.strategy_positions.mae_price IS
    'Most adverse mark price SEEN while open (lower for a long, higher for a '
    'short). Same ~60s sampling floor as mfe_price.';
COMMENT ON COLUMN public.strategy_positions.mfe_r IS
    'mfe_price as an R multiple, favourable positive for both sides. '
    'R = |entry - opening order sl_price|, entry = COALESCE(opening order '
    'actual_fill_price, entry_price) — same definition as the forensics report. '
    'NULL when no stop / zero denominator. Can be negative if the trade never '
    'traded in favour at any sampled instant.';
COMMENT ON COLUMN public.strategy_positions.mae_r IS
    'mae_price as an R multiple, favourable positive (so usually <= 0; positive '
    'means the position was never OBSERVED at a loss, which is expected on rows '
    'whose sampling began mid-life). Same R denominator and NULL rules as mfe_r. '
    'A value milder than -1R does NOT prove the stop was never touched — an '
    'inter-sample wick is invisible.';
COMMENT ON COLUMN public.strategy_positions.excursion_samples IS
    'Mark-price reads that contributed to mfe/mae. Failed reads do not count. '
    'ALWAYS read the excursion columns against this: it is the resolution of the '
    'measurement. 0 means the columns carry no information.';
COMMENT ON COLUMN public.strategy_positions.excursion_first_at IS
    'Timestamp of the first successful mark-price sample for this position.';
COMMENT ON COLUMN public.strategy_positions.excursion_last_at IS
    'Timestamp of the most recent successful mark-price sample. Compare against '
    'closed_at to see how much of the position''s life was actually observed.';

CREATE INDEX IF NOT EXISTS strat_pos_excursion_samples_idx
    ON public.strategy_positions (excursion_samples)
    WHERE excursion_samples > 0;

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ')
      INTO missing
      FROM (VALUES
                ('mfe_price'), ('mae_price'), ('mfe_r'), ('mae_r'),
                ('excursion_samples'), ('excursion_first_at'), ('excursion_last_at')
           ) AS want(c)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = 'strategy_positions'
                  AND column_name  = want.c
           );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 069 FAILED: missing column(s) %', missing;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'strategy_positions'
           AND column_name  = 'excursion_samples'
           AND is_nullable  = 'NO'
           AND column_default LIKE '0%'
    ) THEN
        RAISE EXCEPTION 'Migration 069 FAILED: excursion_samples must be NOT NULL DEFAULT 0';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname  = 'strat_pos_excursion_samples_idx'
    ) THEN
        RAISE EXCEPTION 'Migration 069 FAILED: strat_pos_excursion_samples_idx missing';
    END IF;

    RAISE NOTICE 'Migration 069 verified OK: excursion columns present on strategy_positions (existing rows intentionally left NULL — not backfillable)';
END $$;
