-- Migration 071: the numeric market state at each AI decision instant.
--
-- docs/process/reports/2026-07-28-paper-trade-forensics.md §3.5 could not slice
-- results by market regime at all, and said so:
--
--   "No market-regime data is persisted per trade. volatility_regime and
--    funding_rate were fetched for the prompt but never written to ai_signal_log
--    — only the NAMES of the sources are stored, not their values. Regime slicing
--    is impossible with the current schema."
--
-- ai_signal_log already carries data_sources_used (which sources the strategy
-- CONFIG asked for) and missing_inputs (which of them came back empty). Neither
-- records what the numbers actually were, so "does this system only work in low
-- volatility / negative funding / fear?" has been unanswerable.
--
-- This column stores the values themselves, captured from the same fetched state
-- that feeds missing_inputs, so requested / delivered / value stay consistent with
-- each other on every row.
--
-- ── THE THREE-STATE CONVENTION — THIS IS THE POINT OF THE COLUMN ──────────────
-- For every field, the DISTINCTION between "never asked for" and "asked for and
-- did not arrive" is preserved, because those two mean very different things when
-- reading a result:
--
--   key ABSENT          -> the strategy never enabled this source. Its absence
--                          says nothing about the market.
--   key PRESENT, null   -> the strategy asked for it and it did NOT arrive. The
--                          model made this decision blind to it. Matches an entry
--                          in missing_inputs on the same row.
--   key PRESENT, value  -> delivered, and this is what the model was shown.
--
-- A reader MUST use `?` / IS NULL rather than ->> alone to tell these apart:
--
--   WHERE regime_snapshot ? 'funding_rate'                  -- was it requested
--   WHERE regime_snapshot->'funding_rate' = 'null'::jsonb   -- requested, missing
--   WHERE regime_snapshot->'funding_rate'->>'rate' IS NOT NULL  -- delivered
--
-- ── WHAT IS CAPTURED ─────────────────────────────────────────────────────────
-- A compact numeric summary, not the whole payload — the prompt text is not
-- reconstructable from this and is not meant to be:
--
--   volatility_regime : atr_percentile, bb_width_percentile, squeeze_flag
--   funding_rate      : rate, interpretation
--   fear_greed        : value, label
--   open_interest     : change_24h_pct, long_short_ratio
--   cvd               : cvd_window_usd, cvd_trend, cvd_divergence
--   mtf_structure     : {timeframe: trend_direction}, e.g. {"1h":"up","4h":"down"}
--   btc_dominance     : value, trend
--
-- ── ON "BTC TREND" ───────────────────────────────────────────────────────────
-- There is no global BTC-trend field in this system. The two nearest things are
-- both captured and neither is a drop-in:
--   * btc_dominance is BTC's share of total market cap, not its trend, and it has
--     been disabled on every strategy since 2026-07-05 (196 signals, all before
--     that date), so expect the key to be absent on current rows.
--   * mtf_structure is the trend of the STRATEGY'S OWN symbol across 1h/4h/1d. For
--     the BTC strategies that is BTC's trend; for eth-ai-34d2 it is ETH's.
-- Do not read mtf_structure as a market-wide regime. If a true BTC-trend regime
-- field is wanted, it has to be fetched — it does not exist today.
--
-- Existing rows stay NULL. Not backfillable: the fetched values were never stored
-- and the sources are point-in-time.
--
-- Write-only telemetry. Nothing reads this column to make a trading decision.

ALTER TABLE public.ai_signal_log
    ADD COLUMN IF NOT EXISTS regime_snapshot jsonb;

COMMENT ON COLUMN public.ai_signal_log.regime_snapshot IS
    'Numeric market state at this decision instant, captured from the same fetched '
    'payload that produces missing_inputs. Three states per key: key absent = the '
    'strategy never requested that source; key present with null = requested but not '
    'delivered (the model decided blind to it, and it also appears in missing_inputs); '
    'key present with a value = delivered and shown to the model. Use jsonb ? ''key'' '
    'to tell absent from null. NULL for the whole column means the row predates '
    'migration 071 (2026-07-28) — not backfillable.';

-- Partial: only rows that actually carry a snapshot, which is every row from
-- 2026-07-28 onward and none before.
CREATE INDEX IF NOT EXISTS ai_sl_regime_snapshot_idx
    ON public.ai_signal_log USING gin (regime_snapshot)
    WHERE regime_snapshot IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'ai_signal_log'
           AND column_name  = 'regime_snapshot'
           AND data_type    = 'jsonb'
    ) THEN
        RAISE EXCEPTION 'Migration 071 FAILED: ai_signal_log.regime_snapshot missing or not jsonb';
    END IF;

    -- The column is only meaningful next to these two; guard against a future
    -- migration dropping them and leaving the snapshot uninterpretable.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'ai_signal_log'
           AND column_name = 'missing_inputs'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'ai_signal_log'
           AND column_name = 'data_sources_used'
    ) THEN
        RAISE EXCEPTION 'Migration 071 FAILED: missing_inputs / data_sources_used must still exist';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public' AND indexname = 'ai_sl_regime_snapshot_idx'
    ) THEN
        RAISE EXCEPTION 'Migration 071 FAILED: ai_sl_regime_snapshot_idx missing';
    END IF;

    RAISE NOTICE 'Migration 071 verified OK: ai_signal_log.regime_snapshot present (existing rows intentionally left NULL — not backfillable)';
END $$;
