/* === STEP 1: Enhance the strategies table === */

ALTER TABLE strategies 
    ADD COLUMN IF NOT EXISTS max_position_size NUMERIC DEFAULT 1.0, -- max open position size in base asset (e.g. BTC)
    ADD COLUMN IF NOT EXISTS max_leverage INTEGER DEFAULT 10, -- max allowed leverage for this strategy
    ADD COLUMN IF NOT EXISTS max_daily_drawdown_percent NUMERIC DEFAULT 20, -- stop accepting orders if daily loss exceeds this %
    ADD COLUMN IF NOT EXISTS capital_allocation_percent NUMERIC DEFAULT 100, -- % of total capital this strategy can use
    ADD COLUMN IF NOT EXISTS signals_today INTEGER DEFAULT 0, -- count of signals received today
    ADD COLUMN IF NOT EXISTS pnl_today NUMERIC DEFAULT 0, -- realised P&L today (resets at midnight UTC)
    ADD COLUMN IF NOT EXISTS pnl_total NUMERIC DEFAULT 0, -- total realised P&L all time
    ADD COLUMN IF NOT EXISTS win_count INTEGER DEFAULT 0, -- total winning trades
    ADD COLUMN IF NOT EXISTS loss_count INTEGER DEFAULT 0, -- total losing trades
    ADD COLUMN IF NOT EXISTS last_signal_at TIMESTAMPTZ, -- timestamp of most recent signal received
    ADD COLUMN IF NOT EXISTS description TEXT, -- human-readable description of the strategy
    ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}'; -- tags (e.g. ['trend', 'mean-reversion'])

/* === STEP 2: Create strategy_positions table === */

CREATE TABLE IF NOT EXISTS strategy_positions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id         VARCHAR(100) NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
    exchange            VARCHAR(20) NOT NULL, -- "blofin" | "hyperliquid"
    symbol              VARCHAR(20) NOT NULL,
    side                VARCHAR(10) NOT NULL, -- "long" | "short"
    entry_price         NUMERIC NOT NULL,
    current_price       NUMERIC, -- updated periodically
    size                NUMERIC NOT NULL,
    leverage            INTEGER,
    margin_mode         VARCHAR(20), -- "cross" | "isolated"
    pnl_unrealized      NUMERIC, -- updated periodically
    pnl_realized        NUMERIC DEFAULT 0, -- set on close
    status              VARCHAR(20) DEFAULT 'open', -- "open" | "closed" | "liquidated"
    opening_order_id    UUID REFERENCES orders(id) ON DELETE RESTRICT,
    closing_order_id    UUID REFERENCES orders(id) ON DELETE RESTRICT,
    opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

/* === STEP 3: Create strategy_stats table === */

CREATE TABLE IF NOT EXISTS strategy_stats (
    id                  BIGSERIAL PRIMARY KEY,
    strategy_id         VARCHAR(100) NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
    period_date         DATE NOT NULL, -- the calendar day this row covers
    trades_count        INTEGER DEFAULT 0,
    trades_won          INTEGER DEFAULT 0,
    trades_lost         INTEGER DEFAULT 0,
    win_rate            NUMERIC, -- computed: trades_won / trades_count * 100
    pnl_total           NUMERIC DEFAULT 0,
    pnl_avg             NUMERIC, -- computed: pnl_total / trades_count
    max_drawdown        NUMERIC DEFAULT 0,
    capital_deployed    NUMERIC DEFAULT 0, -- sum of (size * entry_price) for day
    leverage_avg        NUMERIC, -- avg leverage across trades
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id, period_date)
);

/* === STEP 4: Insert default 'test' strategy === */

INSERT INTO strategies (id, name, class, symbol, interval, platform, enabled, description, tags)
VALUES ('test', 'Test Strategy', 'TestStrategy', '*', '1m', 'auto', false, 'Default strategy for smoke test and development orders', '{test,development}')
ON CONFLICT (id) DO NOTHING;

/* === STEP 5: Backfill existing orders === */

UPDATE orders 
SET strategy_id = 'test' 
WHERE strategy_id IS NULL;

/* === STEP 6: Enforce NOT NULL on orders.strategy_id === */

ALTER TABLE orders 
    ALTER COLUMN strategy_id SET NOT NULL;

/* === STEP 7: Create all necessary indexes === */

CREATE INDEX IF NOT EXISTS idx_strat_pos_strategy_status ON strategy_positions (strategy_id, status);
CREATE INDEX IF NOT EXISTS idx_strat_pos_symbol_status ON strategy_positions (symbol, status);
CREATE INDEX IF NOT EXISTS idx_strat_pos_opened_at ON strategy_positions (opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_strat_pos_closing_order_id ON strategy_positions (closing_order_id);
CREATE INDEX IF NOT EXISTS idx_strat_stats_strategy_date ON strategy_stats (strategy_id, period_date DESC);

/* === STEP 8: Create update_timestamp() trigger function and attach === */

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_strategies_modtime') THEN
        CREATE TRIGGER update_strategies_modtime BEFORE UPDATE ON strategies FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_strategy_positions_modtime') THEN
        CREATE TRIGGER update_strategy_positions_modtime BEFORE UPDATE ON strategy_positions FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_strategy_stats_modtime') THEN
        CREATE TRIGGER update_strategy_stats_modtime BEFORE UPDATE ON strategy_stats FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
