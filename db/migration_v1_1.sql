-- Migration: v1.1
-- Purpose: Add strategy columns and tables for webhook monitoring and position tracking.

-- Bug 1: strategies table columns
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS webhook_secret VARCHAR(255);
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS webhook_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS max_daily_signals INTEGER NOT NULL DEFAULT 1000;
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS platform_override VARCHAR(20);

-- Bug 2: orders table columns
ALTER TABLE orders ADD COLUMN IF NOT EXISTS signal_source VARCHAR(50);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS signal_metadata JSONB;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS indicator_price NUMERIC;

-- Bug 3: strategy_webhook_calls table
CREATE TABLE IF NOT EXISTS strategy_webhook_calls (
    id              BIGSERIAL PRIMARY KEY,
    strategy_id     VARCHAR(100) NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    http_status     INTEGER NOT NULL,
    error_message   TEXT
);
CREATE INDEX IF NOT EXISTS swc_strategy_id_idx ON strategy_webhook_calls (strategy_id);
CREATE INDEX IF NOT EXISTS swc_received_at_idx ON strategy_webhook_calls (received_at DESC);

-- Bug 4: strategy_positions table
CREATE TABLE IF NOT EXISTS strategy_positions (
    id               BIGSERIAL PRIMARY KEY,
    strategy_id      VARCHAR(100) NOT NULL,
    exchange         VARCHAR(20) NOT NULL,
    symbol           VARCHAR(20) NOT NULL,
    side             VARCHAR(10) NOT NULL,
    entry_price      NUMERIC NOT NULL,
    size             NUMERIC NOT NULL,
    leverage         INTEGER,
    margin_mode      VARCHAR(10),
    opening_order_id UUID REFERENCES orders(id),
    status           VARCHAR(20) NOT NULL DEFAULT 'open',
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS sp_strategy_id_idx ON strategy_positions (strategy_id);
CREATE INDEX IF NOT EXISTS sp_status_idx ON strategy_positions (status);
