-- 059: open cross-venue spread positions (feat/spread-harvest phases 2-3)
-- One row per executed two-leg episode. Created by order-executor on a
-- confirmed execute; closed by the unwind/abort watcher or operator.

CREATE TABLE IF NOT EXISTS spread_positions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id           UUID REFERENCES spread_plans(id),
    coin              VARCHAR(20)  NOT NULL,
    symbol            VARCHAR(30)  NOT NULL,          -- e.g. BTC-USDT
    status            VARCHAR(20)  NOT NULL DEFAULT 'open',
                      -- open | closed | aborted | leg_failed | close_failed
    short_venue       VARCHAR(20)  NOT NULL,
    long_venue        VARCHAR(20)  NOT NULL,
    short_account_id  VARCHAR(100) NOT NULL,
    long_account_id   VARCHAR(100) NOT NULL,
    notional_usd      NUMERIC      NOT NULL,
    leg_leverage      INTEGER      NOT NULL,
    size              NUMERIC      NOT NULL,          -- base units, both legs
    entry_mark        NUMERIC,
    abort_up_price    NUMERIC      NOT NULL,
    abort_down_price  NUMERIC      NOT NULL,
    short_entry_price NUMERIC,
    long_entry_price  NUMERIC,
    short_order_id    VARCHAR(100),
    long_order_id     VARCHAR(100),
    short_close_price NUMERIC,
    long_close_price  NUMERIC,
    pnl_realized      NUMERIC,
    close_reason      VARCHAR(30),                    -- cooled|abort|manual|leg_failure
    details           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    opened_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    closed_at         TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_spread_pos_status ON spread_positions (status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_spread_pos_one_open
    ON spread_positions (coin) WHERE status = 'open';
