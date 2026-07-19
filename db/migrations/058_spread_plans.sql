-- 058: cross-venue funding-spread trade plans (feat/spread-harvest phase 1)
-- Armed by the spread monitor when |trailing spread| crosses the enter
-- threshold; expired on cool-down; executed/cancelled in later phases.

CREATE TABLE IF NOT EXISTS spread_plans (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coin                VARCHAR(20)  NOT NULL,
    status              VARCHAR(20)  NOT NULL DEFAULT 'armed',
                        -- armed | executed | expired | cancelled
    trailing_spread_ann NUMERIC      NOT NULL,   -- signed: HL minus Blofin, annualized
    short_venue         VARCHAR(20)  NOT NULL,   -- venue whose funding is higher
    long_venue          VARCHAR(20)  NOT NULL,
    capital_usd         NUMERIC      NOT NULL,
    notional_usd        NUMERIC      NOT NULL,   -- per leg
    leg_leverage        INTEGER      NOT NULL DEFAULT 2,
    hl_price            NUMERIC,
    blofin_price        NUMERIC,
    hl_slippage_bps     NUMERIC,
    blofin_slippage_bps NUMERIC,
    est_daily_usd       NUMERIC,                 -- collect at current trailing spread
    est_roundtrip_usd   NUMERIC,
    breakeven_days      NUMERIC,
    abort_up_price      NUMERIC,                 -- +25% from entry mark (short-leg abort)
    abort_down_price    NUMERIC,                 -- -25% from entry mark (long-leg abort)
    details             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_spread_plans_coin_status ON spread_plans (coin, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_spread_plans_one_armed
    ON spread_plans (coin) WHERE status = 'armed';
