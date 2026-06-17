-- 001_add_strategy_webhooks.sql

-- 1. Alter strategies table to add webhook configuration and metadata
ALTER TABLE strategies 
    ADD COLUMN webhook_secret       VARCHAR(255) UNIQUE,
    ADD COLUMN webhook_enabled      BOOLEAN DEFAULT TRUE,
    ADD COLUMN description          TEXT,
    ADD COLUMN platform_override    VARCHAR(20),
    ADD COLUMN max_daily_signals    INTEGER DEFAULT 500,
    ADD COLUMN created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Backfill webhook_secret (requires pgcrypto extension for gen_random_bytes)
CREATE EXTENSION IF NOT EXISTS pgcrypto;
UPDATE strategies SET webhook_secret = encode(gen_random_bytes(32), 'hex') WHERE webhook_secret IS NULL;

-- Set NOT NULL after backfill
ALTER TABLE strategies 
    ALTER COLUMN webhook_secret SET NOT NULL;

CREATE INDEX idx_strategies_webhook_secret ON strategies(webhook_secret);
CREATE INDEX idx_strategies_enabled ON strategies(webhook_enabled);

-- 2. Create strategy_performance table
CREATE TABLE strategy_performance (
    id                  BIGSERIAL PRIMARY KEY,
    strategy_id         VARCHAR(100) NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    period_type         VARCHAR(20) NOT NULL,
    period_date         DATE,
    
    total_signals       INTEGER DEFAULT 0,
    filled_orders       INTEGER DEFAULT 0,
    failed_orders       INTEGER DEFAULT 0,
    rejected_orders     INTEGER DEFAULT 0,
    
    winning_trades      INTEGER DEFAULT 0,
    losing_trades       INTEGER DEFAULT 0,
    neutral_trades      INTEGER DEFAULT 0,
    win_rate            DECIMAL(5, 2),
    
    total_pnl           DECIMAL(18, 8),
    avg_pnl             DECIMAL(18, 8),
    median_pnl          DECIMAL(18, 8),
    max_win             DECIMAL(18, 8),
    max_loss            DECIMAL(18, 8),
    
    consecutive_wins    INTEGER DEFAULT 0,
    consecutive_losses  INTEGER DEFAULT 0,
    profit_factor       DECIMAL(10, 4),
    
    largest_drawdown    DECIMAL(5, 2),
    
    calculated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE(strategy_id, period_type, period_date)
);

CREATE INDEX idx_strat_perf_strategy ON strategy_performance(strategy_id);
CREATE INDEX idx_strat_perf_period ON strategy_performance(period_type, period_date DESC);

-- 3. Create strategy_webhook_calls table
CREATE TABLE strategy_webhook_calls (
    id              BIGSERIAL PRIMARY KEY,
    strategy_id     VARCHAR(100) NOT NULL REFERENCES strategies(id),
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    http_status     INTEGER,
    error_message   TEXT,
    source_ip       INET
);

CREATE INDEX idx_webhook_calls_strategy ON strategy_webhook_calls(strategy_id, received_at DESC);
CREATE INDEX idx_webhook_calls_status ON strategy_webhook_calls(http_status);
