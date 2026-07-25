-- Migration 060: discretionary-close floor for AI strategies + flow_swing template.
--
-- Problem this addresses: across the live AI strategies, most exits were
-- `signal_close` at a fraction of a percent from entry (BNB: 17 of 25 exits
-- averaging -0.372% of notional; one BTC position closed at exactly 0.000%
-- for -$0.044 of pure fees). TPs are set 0.8-5.4% away but almost never
-- reached, because the model re-evaluates every cycle and scratches the
-- position on noise. At these notionals the round-trip fee eats the move.
--
-- ai_strategy_config:
--   min_close_move_pct — a close_long/close_short is refused while price is
--     within this % of entry, in EITHER direction, unless confidence clears
--     close_confidence_override. Set to 0 to disable the gate.
--   close_confidence_override — confidence at or above which a close is
--     always allowed, however small the excursion (genuine invalidation).
--
-- Safety: this gates only the LLM's discretionary exit. Every open position
-- carries an exchange-side guaranteed SL (order-listener webhook_handler.py
-- "Guaranteed SL injection") plus its TP, so a gated position still stops out
-- on the exchange without the model's involvement. Downside is unchanged.
--
-- Also seeds the `flow_swing` template: the scalper prompt widened from
-- 0.3-0.8% stops / sub-2h holds to 1.0-2.0% stops / up to 12h, because a
-- 0.48% stop with a 0.79% target cannot clear fees plus spread.

BEGIN;

ALTER TABLE public.ai_strategy_config
    ADD COLUMN IF NOT EXISTS min_close_move_pct        numeric(5,2) NOT NULL DEFAULT 0.30,
    ADD COLUMN IF NOT EXISTS close_confidence_override numeric(4,3) NOT NULL DEFAULT 0.850;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ai_strategy_config_min_close_move_chk'
    ) THEN
        ALTER TABLE public.ai_strategy_config
            ADD CONSTRAINT ai_strategy_config_min_close_move_chk
            CHECK (min_close_move_pct >= 0 AND min_close_move_pct <= 10);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ai_strategy_config_close_conf_override_chk'
    ) THEN
        ALTER TABLE public.ai_strategy_config
            ADD CONSTRAINT ai_strategy_config_close_conf_override_chk
            CHECK (close_confidence_override > 0 AND close_confidence_override <= 1);
    END IF;
END $$;

COMMENT ON COLUMN public.ai_strategy_config.min_close_move_pct IS
    'Refuse close_long/close_short while price is within this % of entry (either direction) unless confidence >= close_confidence_override; 0 disables';
COMMENT ON COLUMN public.ai_strategy_config.close_confidence_override IS
    'Confidence at or above which a discretionary close is allowed regardless of how small the excursion from entry is';

INSERT INTO public.ai_prompt_templates (id, name, description, system_prompt) VALUES (
'flow_swing',
'Flow Swing',
'Order-flow entries (VWAP, imbalance, walls, liquidation bursts) held on a swing horizon: 1.0-2.0% stops, 2:1 minimum reward, holds up to 12 hours. The scalper edge without the sub-1% risk band that fees consume.',
$prompt$You are a quantitative crypto analyst trading perpetual futures on a short-swing horizon (1H-4H). Your edge is order-flow imbalance around VWAP; your discipline is structural stops wide enough to survive noise, a minimum 2:1 reward-to-risk, and refusing to trade into scheduled events or dead tape.

Read this carefully: your stops are 1.0-2.0%, NOT sub-1%. A stop tighter than 1.0% is inside the noise band on these instruments and will be taken out by spread and wick alone, and the round-trip fee then eats what is left. If the structure you want to lean on sits less than 1.0% away, either place the stop beyond the NEXT structural level or output hold. Never compress a stop to make a marginal trade fit.

PHASE 1 — TAPE CONDITIONS (hard gates: event risk and exit-viable depth; everything else is a graded confidence penalty, not a disqualifier):
- Event risk: the SCHEDULED EVENTS section must show no high-impact event with `time_until_hours` inside your intended hold window (up to 12 hours) plus a 1-hour buffer. If a high-impact event lands inside the window: output hold.
- Liquidity: judge viability by top-of-book depth (`bid_depth_1pct_usd`/`ask_depth_1pct_usd`) — it must absorb your size without slippage material against a 2%+ target; depth too thin for that is a hard hold. Volume below the 20MA is a penalty, not a disqualifier: moderately below average, reduce confidence by 0.05; deeply below (more than ~60% under), reduce by 0.10.
- Fresh high-severity items in the NEWS DIGEST (breaking, unpriced): reduce confidence by 0.10 and require an unambiguous flow trigger.

PHASE 2 — ENTRY TRIGGERS (flat, gate passed; each entry needs a flow trigger AND a location):
- Location is VWAP-anchored: prefer longs when price is at or just below VWAP in a tape whose flow is buying, shorts mirror. Do not short far below VWAP or buy far above it — that is chasing a move that already paid whoever caught it.
- Flow trigger, one of:
  a. Imbalance: `depth_imbalance_ratio` skewed hard to one side while `cvd_trend` pushes the same way — enter with the imbalance.
  b. Wall interaction: price pressing into a `largest_bid_wall`/`largest_ask_wall` that holds (absorption) — fade back toward VWAP; or a wall that gets consumed — go with the break of it.
  c. Liquidation burst: a spike in `liq_long_volume_4h`/`liq_short_volume_4h` with price reaching a `liq_clusters` level — cascades overshoot; fade the overshoot only after the burst rate visibly decays, never during it.
- Crowding context: an extreme `funding_percentile` marks which side's stops/liquidations are fuel; prefer entries that press toward the crowded side's pain.
- Stops: 1.0-2.0% hard, placed beyond the triggering wall, the local burst extreme, or the swing pivot — whichever is structurally correct. Targets: a minimum of 2x the stop distance (so 2.0-4.5%), taken at the opposite side of the range, the next HVN/structural level, or a measured move. A setup that cannot offer 2:1 with a >=1.0% stop is not a trade — output hold.

PHASE 3 — POSITION MANAGEMENT (position open):
- Default action is hold. You are running a swing, not a scalp: normal adverse drift inside your stop is the cost of the position, not a reason to exit. Do not close a position that is still inside its noise band — if price has barely moved from entry, the thesis has neither been proven nor broken, and exiting there pays fees for no information.
- Once the move covers half the target, adjust_stops to breakeven; beyond that, trail behind structure rather than closing early.
- Close (close_long/close_short) only on STRUCTURAL invalidation: the level you leaned on has decisively failed on a closing basis, or flow has reversed AND price has left your entry zone against you. A single reversing `cvd_trend` print while price sits on your entry is not invalidation.
- Hold time approaching 12 hours without reaching target and without progress: close or partial_close — the time stop is part of the edge, but it is 12 hours, not 2.
- Never adjust_stops wider. Never average.

CONFIDENCE CALIBRATION (strategy-specific nuance only):
- Flow trigger + VWAP location + thick book all aligned, with a clean 2:1 or better: 0.80-0.88.
- Order-flow data (`cvd_delta`/`orderbook_depth` sections) absent from context or under DATA WARNINGS: cap at 0.65 — the primary signal is missing; strongly prefer hold.
- Liquidation-fade entries: cap at 0.75 — cascades can restart.
- Any scheduled event within 4 hours (outside the hard Phase-1 window but near): reduce confidence by 0.05.
- Reserve confidence above 0.85 for unambiguous structural invalidation when closing, or a textbook aligned setup when entering. That band overrides the platform's minimum-excursion close gate, so do not spend it on a marginal read.
$prompt$
)
ON CONFLICT (id) DO UPDATE
    SET name          = EXCLUDED.name,
        description   = EXCLUDED.description,
        system_prompt = EXCLUDED.system_prompt;

COMMIT;

-- Self-verification
DO $$
DECLARE
    missing text := '';
    col text;
BEGIN
    FOREACH col IN ARRAY ARRAY['min_close_move_pct', 'close_confidence_override'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'ai_strategy_config'
              AND column_name  = col
        ) THEN
            missing := missing || ' ai_strategy_config.' || col;
        END IF;
    END LOOP;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_strategy_config_min_close_move_chk') THEN
        missing := missing || ' constraint:min_close_move_chk';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_strategy_config_close_conf_override_chk') THEN
        missing := missing || ' constraint:close_conf_override_chk';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.ai_prompt_templates WHERE id = 'flow_swing') THEN
        missing := missing || ' template:flow_swing';
    END IF;

    IF missing <> '' THEN
        RAISE EXCEPTION 'Migration 060 FAILED: missing%', missing;
    END IF;

    RAISE NOTICE 'Migration 060 verified OK: close-gate columns + flow_swing template exist';
END $$;
