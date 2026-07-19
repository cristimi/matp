-- 057: funding-harvest trade plans (feat/funding-harvest phase 1)
-- Armed by the planner when the funding regime fires; executed/expired later.

CREATE TABLE IF NOT EXISTS funding_harvest_plans (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coin               VARCHAR(20)  NOT NULL,             -- base coin, e.g. BTC
    status             VARCHAR(20)  NOT NULL DEFAULT 'armed',
                       -- armed | executed | expired | cancelled
    trailing_ann       NUMERIC      NOT NULL,             -- Binance 3d trailing, annualized (signal)
    hl_funding_ann     NUMERIC,                           -- HL live hourly funding, annualized (income)
    spot_pair          VARCHAR(30)  NOT NULL,             -- HL spot pair, e.g. UBTC/USDC (@142)
    perp_symbol        VARCHAR(20)  NOT NULL,             -- HL perp coin, e.g. BTC
    capital_usd        NUMERIC      NOT NULL,
    notional_usd       NUMERIC      NOT NULL,             -- per leg
    spot_qty           NUMERIC      NOT NULL,
    spot_price         NUMERIC      NOT NULL,
    perp_price         NUMERIC      NOT NULL,
    perp_leverage      INTEGER      NOT NULL DEFAULT 2,
    spot_slippage_bps  NUMERIC,                           -- book-walk to notional at plan time
    perp_slippage_bps  NUMERIC,
    est_entry_cost_usd NUMERIC,                           -- fees + slippage, entry only
    est_roundtrip_usd  NUMERIC,                           -- entry + exit
    est_daily_funding_usd NUMERIC,                        -- at HL live funding
    breakeven_days     NUMERIC,
    details            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fh_plans_coin_status ON funding_harvest_plans (coin, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fh_plans_one_armed
    ON funding_harvest_plans (coin) WHERE status = 'armed';
