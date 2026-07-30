-- Migration 072: a per-strategy floor on how close an ADJUSTED stop may sit to
-- the live price.
--
-- ── WHY ──────────────────────────────────────────────────────────────────────
-- .gemini/reports/2026-07-30_sol_closed_by_trailed_stop.md: sol-ai-6486 lost
-- position c2a2a927 to a stop it placed 0.258% away from the live price
-- (sl 73.88 against price 73.69). SOL's own opening stops that month averaged
-- 1.160% and never went below 0.550%. A stop inside a quarter of a percent is
-- not risk management on that symbol — it is a market order with extra steps,
-- and it fired 39 minutes later for -0.48 net.
--
-- This is the same failure the `min_close_move_pct` floor already describes for
-- discretionary exits ("a position sitting inside its own noise band has neither
-- proven nor broken its thesis"). A stop parked inside that same band is the
-- identical mistake wearing a different hat, so this column deliberately carries
-- the SAME 0.30 default — one noise floor, two doors into it.
--
-- ── SCOPE ────────────────────────────────────────────────────────────────────
-- Applies ONLY to the stop-loss leg of the `adjust_stops` action, checked in
-- ai-signal-generator/app/graph/nodes/node_guard.py. It does NOT constrain:
--   * opening stops (those are derived from stop_loss_pct and already bounded
--     by _MIN_SL_TP_PCT / _MAX_SL_TP_PCT),
--   * the take-profit leg (a too-near TP scratches a profit, which is wasteful
--     but not the loss mechanism under investigation),
--   * the exchange-side guaranteed SL injected by order-listener.
--
-- ── CHOOSING A VALUE ─────────────────────────────────────────────────────────
-- Per strategy, because tolerance is symbol- and style-specific. Measured
-- opening-stop distances over the 30 days to 2026-07-30, for calibration:
--
--   bnb-ai-scalper-edbb  min 0.281%  avg 0.534%   <- tightest real strategy
--   hype-breakout-da2e   min 0.215%  avg 1.839%
--   ai-btc-6f8c          min 0.366%  avg 1.311%
--   sol-ai-6486          min 0.550%  avg 1.160%
--   tao-ai-range-...     min 0.550%  avg 1.331%
--
-- A genuine scalper wanting sub-0.3% trailing should LOWER its own row rather
-- than have the default raised — the default protects the swing strategies that
-- are the ones actually being scratched. Set 0 to disable the check entirely.

ALTER TABLE public.ai_strategy_config
    ADD COLUMN IF NOT EXISTS min_stop_distance_pct numeric(5,2) NOT NULL DEFAULT 0.30;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.ai_strategy_config'::regclass
           AND conname  = 'ai_strategy_config_min_stop_distance_chk'
    ) THEN
        ALTER TABLE public.ai_strategy_config
            ADD CONSTRAINT ai_strategy_config_min_stop_distance_chk
            CHECK (min_stop_distance_pct >= 0 AND min_stop_distance_pct <= 20);
    END IF;
END $$;

COMMENT ON COLUMN public.ai_strategy_config.min_stop_distance_pct IS
    'Minimum distance, in percent of the live price, that an adjust_stops stop-loss '
    'must keep from that price. Rejected with gate_rejection_reason '
    '''stop_too_close''. 0 disables the check. Default 0.30 mirrors '
    'min_close_move_pct: both encode the same "inside its own noise band" floor. '
    'Applies to the adjust_stops SL leg only — not to opening stops, not to TP, '
    'and not to the exchange-side guaranteed SL. Added 2026-07-30 after '
    'sol-ai-6486 stopped itself out 0.258% from price (migration 072).';

DO $$
DECLARE
    n_rows  int;
    n_bad   int;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'ai_strategy_config'
           AND column_name  = 'min_stop_distance_pct'
    ) THEN
        RAISE EXCEPTION 'Migration 072 FAILED: ai_strategy_config.min_stop_distance_pct missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.ai_strategy_config'::regclass
           AND conname  = 'ai_strategy_config_min_stop_distance_chk'
    ) THEN
        RAISE EXCEPTION 'Migration 072 FAILED: range check constraint missing';
    END IF;

    SELECT count(*) INTO n_rows FROM public.ai_strategy_config;
    SELECT count(*) INTO n_bad  FROM public.ai_strategy_config
     WHERE min_stop_distance_pct IS NULL;
    IF n_bad > 0 THEN
        RAISE EXCEPTION 'Migration 072 FAILED: % of % rows have a NULL floor', n_bad, n_rows;
    END IF;

    RAISE NOTICE 'Migration 072 verified OK: min_stop_distance_pct present on % strategy rows, default 0.30', n_rows;
END $$;
