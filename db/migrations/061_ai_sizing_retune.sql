-- Migration 061: make risk_per_trade actually govern AI position size,
-- and widen the BNB scalper.
--
-- Diagnosis (2026-07-25). node_guard._resolve_entry_sizing caps notional at
-- margin_per_trade x leverage. With margin_per_trade=10-20 that cap bound on
-- EVERY risk-mode signal — 22 of 22 in the log window:
--
--   risk sizing clamped: target risk $5.00 needs notional 1724.14 but margin
--   cap allows 100.00 (margin_per_trade=10.00 x lev=10) — effective risk $0.29
--
-- Effective risk came out at $0.29-$0.70 against a $5.00 target. The one
-- strategy whose target notional fit under its cap (HYPE: needs $332, cap
-- $400) was also the only one with meaningful per-trade magnitude, which
-- confirms the mechanism.
--
-- Fix: raise margin_per_trade to $50 so the cap becomes what migration 054
-- intended — a collateral SAFETY BOUND rather than the binding constraint —
-- and put every AI strategy on risk-unit sizing with a uniform $5 target
-- (5% of the ~$100 per-strategy allocation).
--
-- At $50 x 20 = $1000 the cap now binds only below a 0.5% stop (below 1.0%
-- for the 10x strategies). At typical stops the notional lands far under it:
-- BTC's 1.84% stop sizes to $272 notional / $13.60 margin. When the cap does
-- bind the loss at the stop is still <= $5 — only the collateral posted is
-- larger, never the risk.
--
-- Reversible: see the rollback block at the foot of this file.

BEGIN;

-- ── 1. Risk-unit sizing, uniform $5 target, $50 collateral ceiling ──────
UPDATE public.ai_strategy_config c
   SET sizing_mode    = 'risk',
       risk_per_trade = 5.00,
       updated_at     = now(),
       updated_by     = 'migration_061'
  FROM public.strategies s
 WHERE s.id = c.strategy_id
   AND s.strategy_source = 'ai_engine'
   AND s.is_deleted = false;

UPDATE public.strategies
   SET margin_per_trade = 50,
       updated_at       = now()
 WHERE strategy_source = 'ai_engine'
   AND is_deleted = false;

-- ── 2. Widen the BNB scalper ────────────────────────────────────────────
-- 0.476% stops against 0.790% targets could not clear fees plus spread: 7
-- wins / 18 losses, and 17 of 25 exits were signal_close averaging -0.372%.
-- flow_swing keeps the order-flow entry edge but moves it to a 1.0-2.0% stop
-- with a 2:1 minimum, and lengthens the time stop from 2h to 12h. The cycle
-- intervals widen with it — re-deciding every 15 minutes on a 12-hour hold is
-- 48 chances to talk itself out of the position.
UPDATE public.ai_strategy_config
   SET template_id            = 'flow_swing',
       interval_no_position   = '1h',
       interval_position_open = '1h',
       cooldown_entry_minutes = 120,
       at_risk_threshold_pct  = 1.00,
       updated_at             = now(),
       updated_by             = 'migration_061'
 WHERE template_id = 'scalper'
   AND strategy_id IN (
       SELECT id FROM public.strategies
        WHERE strategy_source = 'ai_engine' AND is_deleted = false
   );

COMMIT;

-- Self-verification
DO $$
DECLARE
    bad_sizing int;
    bad_margin int;
    bad_bnb    int;
BEGIN
    SELECT count(*) INTO bad_sizing
      FROM public.ai_strategy_config c
      JOIN public.strategies s ON s.id = c.strategy_id
     WHERE s.strategy_source = 'ai_engine' AND s.is_deleted = false
       AND (c.sizing_mode <> 'risk' OR c.risk_per_trade IS DISTINCT FROM 5.00);

    SELECT count(*) INTO bad_margin
      FROM public.strategies
     WHERE strategy_source = 'ai_engine' AND is_deleted = false
       AND margin_per_trade <> 50;

    SELECT count(*) INTO bad_bnb
      FROM public.ai_strategy_config
     WHERE template_id = 'scalper';

    IF bad_sizing > 0 THEN
        RAISE EXCEPTION 'Migration 061 FAILED: % ai_engine strategies not on risk/$5 sizing', bad_sizing;
    END IF;
    IF bad_margin > 0 THEN
        RAISE EXCEPTION 'Migration 061 FAILED: % ai_engine strategies not at margin_per_trade=50', bad_margin;
    END IF;
    IF bad_bnb > 0 THEN
        RAISE EXCEPTION 'Migration 061 FAILED: % strategies still on the scalper template', bad_bnb;
    END IF;

    RAISE NOTICE 'Migration 061 verified OK: risk sizing + margin ceiling + flow_swing applied';
END $$;

-- ── Rollback ────────────────────────────────────────────────────────────
-- To restore the pre-061 configuration:
--
--   UPDATE ai_strategy_config c SET sizing_mode='margin', risk_per_trade=NULL
--     FROM strategies s WHERE s.id=c.strategy_id AND s.strategy_source='ai_engine';
--   UPDATE ai_strategy_config SET sizing_mode='risk', risk_per_trade=10.00
--     WHERE strategy_id='ai-btc-6f8c';
--   UPDATE ai_strategy_config SET sizing_mode='risk', risk_per_trade=5.00
--     WHERE strategy_id IN ('bnb-ai-scalper-edbb','hype-breakout-da2e');
--   UPDATE strategies SET margin_per_trade=10 WHERE strategy_source='ai_engine';
--   UPDATE strategies SET margin_per_trade=20 WHERE id='hype-breakout-da2e';
--   UPDATE ai_strategy_config SET template_id='scalper', interval_no_position='15m',
--          interval_position_open='15m', cooldown_entry_minutes=60,
--          at_risk_threshold_pct=1.50 WHERE strategy_id='bnb-ai-scalper-edbb';
