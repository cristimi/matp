-- MATP Database Schema
-- Version 1.0

-- Orders: every webhook signal received
CREATE TABLE IF NOT EXISTS orders (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol            VARCHAR(20) NOT NULL,
    side              VARCHAR(10) NOT NULL,
    signal            VARCHAR(20) NOT NULL,
    order_type        VARCHAR(20) NOT NULL,
    size              NUMERIC NOT NULL,
    price             NUMERIC,
    leverage          INTEGER,
    margin_mode       VARCHAR(10),
    tp_price          NUMERIC,
    sl_price          NUMERIC,
    platform          VARCHAR(20) NOT NULL,
    strategy_id       VARCHAR(100),
    status            VARCHAR(20) NOT NULL DEFAULT 'received',
    exchange_order_id VARCHAR(100),
    pnl               NUMERIC,
    raw_webhook       JSONB NOT NULL,
    raw_response      JSONB,
    error_msg         TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS orders_received_at_idx ON orders (received_at DESC);
CREATE INDEX IF NOT EXISTS orders_status_idx ON orders (status);
CREATE INDEX IF NOT EXISTS orders_strategy_id_idx ON orders (strategy_id);
CREATE INDEX IF NOT EXISTS orders_platform_idx ON orders (platform);

-- Dead letter queue: failed / rejected orders
CREATE TABLE IF NOT EXISTS dead_letter_orders (
    id          BIGSERIAL PRIMARY KEY,
    order_id    UUID NOT NULL REFERENCES orders(id),
    failed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason      TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_retry  TIMESTAMPTZ
);

-- Order audit trail
CREATE TABLE IF NOT EXISTS order_events (
    id          BIGSERIAL PRIMARY KEY,
    order_id    UUID NOT NULL REFERENCES orders(id),
    event_time  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    from_status VARCHAR(20),
    to_status   VARCHAR(20) NOT NULL,
    message     TEXT
);

CREATE INDEX IF NOT EXISTS order_events_order_id_idx ON order_events (order_id);

-- System configuration (active platform, encrypted credentials, etc.)
CREATE TABLE IF NOT EXISTS config (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Default configuration
INSERT INTO config (key, value) VALUES
    ('active_platform', 'blofin'),
    ('max_order_size_btc', '1.0'),
    ('max_order_size_eth', '10.0')
ON CONFLICT (key) DO NOTHING;

-- Strategies registry
CREATE TABLE IF NOT EXISTS strategies (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    class       VARCHAR(100) NOT NULL,
    symbol      VARCHAR(20) NOT NULL,
    interval    VARCHAR(10) NOT NULL,
    platform    VARCHAR(20) NOT NULL DEFAULT 'auto',
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    config_yaml TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Function to auto-update updated_at timestamps
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_strategies_updated_at
    BEFORE UPDATE ON strategies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
