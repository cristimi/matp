-- Migration 054: risk-unit position sizing for AI strategies.
--
-- ai_strategy_config:
--   sizing_mode — 'margin' (default, existing behavior: notional =
--     margin_per_trade × leverage) or 'risk' (notional = risk_per_trade /
--     SL distance, so a stop-out loses ≈ risk_per_trade dollars).
--   risk_per_trade — target $ loss at the stop when sizing_mode='risk'.
--
-- In risk mode, strategies.margin_per_trade is reinterpreted as the HARD
-- COLLATERAL CAP: the guard clamps notional at margin_per_trade × leverage,
-- and the order-listener's independent margin clamp enforces the same bound.

BEGIN;

ALTER TABLE public.ai_strategy_config
    ADD COLUMN IF NOT EXISTS sizing_mode    varchar(10) NOT NULL DEFAULT 'margin',
    ADD COLUMN IF NOT EXISTS risk_per_trade numeric(12,2);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ai_strategy_config_sizing_mode_chk'
    ) THEN
        ALTER TABLE public.ai_strategy_config
            ADD CONSTRAINT ai_strategy_config_sizing_mode_chk
            CHECK (sizing_mode IN ('margin', 'risk'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ai_strategy_config_risk_per_trade_chk'
    ) THEN
        ALTER TABLE public.ai_strategy_config
            ADD CONSTRAINT ai_strategy_config_risk_per_trade_chk
            CHECK (sizing_mode = 'margin' OR (risk_per_trade IS NOT NULL AND risk_per_trade > 0));
    END IF;
END $$;

COMMENT ON COLUMN public.ai_strategy_config.sizing_mode IS
    'margin: notional = margin_per_trade x leverage; risk: notional = risk_per_trade / SL distance, capped by margin_per_trade x leverage';
COMMENT ON COLUMN public.ai_strategy_config.risk_per_trade IS
    'Target $ loss at the stop-loss when sizing_mode=risk';

COMMIT;

-- Self-verification
DO $$
DECLARE
    missing text := '';
    col text;
BEGIN
    FOREACH col IN ARRAY ARRAY['sizing_mode', 'risk_per_trade'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'ai_strategy_config'
              AND column_name  = col
        ) THEN
            missing := missing || ' ai_strategy_config.' || col;
        END IF;
    END LOOP;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_strategy_config_sizing_mode_chk') THEN
        missing := missing || ' constraint:sizing_mode_chk';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_strategy_config_risk_per_trade_chk') THEN
        missing := missing || ' constraint:risk_per_trade_chk';
    END IF;

    IF missing <> '' THEN
        RAISE EXCEPTION 'Migration 054 FAILED: missing%', missing;
    END IF;

    RAISE NOTICE 'Migration 054 verified OK: risk-unit sizing columns exist';
END $$;
