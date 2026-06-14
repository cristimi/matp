-- MATP Database Schema
-- Reflects the current live schema as of 2026-06-08.
-- This file is the authoritative baseline for fresh deployments.
-- Subsequent changes are applied via db/migrations/.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ─── Helper function ────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ─── Asset registry ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS assets (
    id     SERIAL PRIMARY KEY,
    symbol VARCHAR(20)  NOT NULL UNIQUE,
    name   VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS trading_pairs (
    id             SERIAL PRIMARY KEY,
    base_asset_id  INTEGER NOT NULL REFERENCES assets(id),
    quote_asset_id INTEGER NOT NULL REFERENCES assets(id),
    exchange_meta  JSONB   NOT NULL DEFAULT '{}',
    UNIQUE(base_asset_id, quote_asset_id)
);

-- ─── Exchange accounts ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS exchange_accounts (
    id          VARCHAR(100) PRIMARY KEY,
    exchange    VARCHAR(30)  NOT NULL,
    mode        VARCHAR(10)  NOT NULL CHECK (mode IN ('live', 'demo')),
    label       VARCHAR(100) NOT NULL,
    credentials BYTEA        NOT NULL,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─── System configuration ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS config (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO config (key, value) VALUES
    ('active_platform',    'blofin'),
    ('max_order_size_btc', '1.0'),
    ('max_order_size_eth', '10.0')
ON CONFLICT (key) DO NOTHING;

-- ─── Strategies ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS strategies (
    id                         VARCHAR(100) PRIMARY KEY,
    name                       VARCHAR(100) NOT NULL,
    class                      VARCHAR(100) NOT NULL,
    symbol                     VARCHAR(50)  NOT NULL,
    interval                   VARCHAR(10)  NOT NULL,
    platform                   VARCHAR(20)  NOT NULL DEFAULT 'auto',
    enabled                    BOOLEAN      NOT NULL DEFAULT TRUE,
    type                       VARCHAR(20)  NOT NULL DEFAULT 'internal'
                                   CHECK (type IN ('internal', 'tradingview')),
    config_yaml                TEXT         NOT NULL,
    config                     JSONB        NOT NULL DEFAULT '{}',
    webhook_secret             VARCHAR(255) NOT NULL UNIQUE,
    webhook_enabled            BOOLEAN               DEFAULT TRUE,
    description                TEXT,
    platform_override          VARCHAR(20),
    max_daily_signals          INTEGER               DEFAULT 500,
    max_position_size          NUMERIC               DEFAULT 1.0,
    max_leverage               INTEGER               DEFAULT 10,
    signals_today              INTEGER               DEFAULT 0,
    pnl_today                  NUMERIC               DEFAULT 0,
    pnl_total                  NUMERIC               DEFAULT 0,
    win_count                  INTEGER               DEFAULT 0,
    loss_count                 INTEGER               DEFAULT 0,
    last_signal_at             TIMESTAMPTZ,
    tags                       TEXT[]                DEFAULT '{}',
    account_id                 VARCHAR(100),
    pair_id                    INTEGER REFERENCES trading_pairs(id),
    allow_quote_variants       BOOLEAN      NOT NULL DEFAULT FALSE,
    allow_cross_charting       BOOLEAN      NOT NULL DEFAULT FALSE,
    default_leverage           INTEGER      NOT NULL DEFAULT 1,
    margin_mode                VARCHAR(10)  NOT NULL DEFAULT 'isolated',
    is_deleted                 BOOLEAN      NOT NULL DEFAULT FALSE,
    strategy_source            VARCHAR(20)  NOT NULL DEFAULT 'tradingview',
    blofin_token               TEXT,
    -- capital allocation foundation (migration 016)
    capital_allocation         NUMERIC      NOT NULL DEFAULT 100,
    margin_per_trade           NUMERIC      NOT NULL DEFAULT 5,
    max_drawdown_pct           NUMERIC      NOT NULL DEFAULT 50,
    drawdown_anchor_pnl        NUMERIC      NOT NULL DEFAULT 0,
    created_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategies_enabled        ON strategies (webhook_enabled);
CREATE INDEX IF NOT EXISTS idx_strategies_webhook_secret ON strategies (webhook_secret);

-- ─── Orders ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS orders (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol            VARCHAR(50) NOT NULL,
    side              VARCHAR(10) NOT NULL,
    signal            VARCHAR(20) NOT NULL,
    order_type        VARCHAR(20) NOT NULL,
    size              NUMERIC     NOT NULL,
    price             NUMERIC,
    leverage          INTEGER,
    margin_mode       VARCHAR(10),
    tp_price          NUMERIC,
    sl_price          NUMERIC,
    platform          VARCHAR(20) NOT NULL,
    strategy_id       VARCHAR(100) NOT NULL,
    account_id        VARCHAR(100) REFERENCES exchange_accounts(id),
    pair_id           INTEGER REFERENCES trading_pairs(id),
    status            VARCHAR(20) NOT NULL DEFAULT 'received',
    exchange_order_id VARCHAR(100),
    actual_fill_price NUMERIC,
    pnl               NUMERIC,
    raw_webhook       JSONB       NOT NULL,
    raw_response      JSONB,
    error_msg         TEXT,
    signal_source     VARCHAR(100) NOT NULL DEFAULT 'unknown',
    signal_metadata     JSONB        DEFAULT '{}',
    indicator_price     NUMERIC(18,8),
    closes_position_id  UUID REFERENCES strategy_positions(id),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS orders_received_at_idx       ON orders (received_at DESC);
CREATE INDEX IF NOT EXISTS orders_status_idx            ON orders (status);
CREATE INDEX IF NOT EXISTS orders_strategy_id_idx       ON orders (strategy_id);
CREATE INDEX IF NOT EXISTS orders_account_id_idx        ON orders (account_id);
CREATE INDEX IF NOT EXISTS orders_platform_idx          ON orders (platform);
CREATE INDEX IF NOT EXISTS orders_pair_id_idx           ON orders (pair_id);
CREATE INDEX IF NOT EXISTS idx_orders_strategy_source   ON orders (strategy_id, signal_source);
CREATE INDEX IF NOT EXISTS idx_orders_closes_position   ON orders (closes_position_id) WHERE closes_position_id IS NOT NULL;

-- ─── Dead letter queue ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dead_letter_orders (
    id          BIGSERIAL PRIMARY KEY,
    order_id    UUID        NOT NULL REFERENCES orders(id),
    failed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason      TEXT,
    retry_count INTEGER     NOT NULL DEFAULT 0,
    last_retry  TIMESTAMPTZ
);

-- ─── Order audit trail ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS order_events (
    id          BIGSERIAL PRIMARY KEY,
    order_id    UUID        NOT NULL REFERENCES orders(id),
    event_time  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    from_status VARCHAR(20),
    to_status   VARCHAR(20) NOT NULL,
    message     TEXT
);

CREATE INDEX IF NOT EXISTS order_events_order_id_idx ON order_events (order_id);

-- ─── Strategy positions ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS strategy_positions (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id       VARCHAR(100) NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
    account_id        VARCHAR(100) REFERENCES exchange_accounts(id),
    exchange          VARCHAR(20)  NOT NULL,
    symbol            VARCHAR(50)  NOT NULL,
    pair_id           INTEGER REFERENCES trading_pairs(id),
    side              VARCHAR(10)  NOT NULL,
    entry_price       NUMERIC      NOT NULL,
    current_price     NUMERIC,
    closing_price     NUMERIC,
    liquidation_price NUMERIC,
    size              NUMERIC      NOT NULL,
    leverage          INTEGER,
    margin_mode       VARCHAR(20),
    pnl_unrealized    NUMERIC,
    pnl_realized          NUMERIC      DEFAULT 0,
    status                VARCHAR(20)  DEFAULT 'open',
    opening_order_id      UUID REFERENCES orders(id) ON DELETE RESTRICT,
    closing_order_id      UUID REFERENCES orders(id) ON DELETE RESTRICT,
    close_reason          VARCHAR(30),
    reconcile_miss_count  INTEGER      NOT NULL DEFAULT 0,
    opened_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_at             TIMESTAMPTZ,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS sp_strategy_id_idx            ON strategy_positions (strategy_id);
CREATE INDEX IF NOT EXISTS sp_status_idx                 ON strategy_positions (status);
CREATE INDEX IF NOT EXISTS sp_pair_id_idx                ON strategy_positions (pair_id);
CREATE INDEX IF NOT EXISTS idx_strat_pos_strategy_status ON strategy_positions (strategy_id, status);
CREATE INDEX IF NOT EXISTS idx_strat_pos_symbol_status   ON strategy_positions (symbol, status);
CREATE INDEX IF NOT EXISTS idx_strat_pos_opened_at       ON strategy_positions (opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_strat_pos_closing_order_id ON strategy_positions (closing_order_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_strat_pos_one_open ON strategy_positions (strategy_id, symbol, side) WHERE status = 'open';

-- ─── Strategy statistics ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS strategy_stats (
    id               BIGSERIAL PRIMARY KEY,
    strategy_id      VARCHAR(100) NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
    period_date      DATE         NOT NULL,
    trades_count     INTEGER      DEFAULT 0,
    trades_won       INTEGER      DEFAULT 0,
    trades_lost      INTEGER      DEFAULT 0,
    win_rate         NUMERIC,
    pnl_total        NUMERIC      DEFAULT 0,
    pnl_avg          NUMERIC,
    max_drawdown     NUMERIC      DEFAULT 0,
    capital_deployed NUMERIC      DEFAULT 0,
    leverage_avg     NUMERIC,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id, period_date)
);

CREATE INDEX IF NOT EXISTS idx_strat_stats_strategy_date ON strategy_stats (strategy_id, period_date DESC);

-- ─── Strategy performance (aggregated) ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS strategy_performance (
    id                  BIGSERIAL PRIMARY KEY,
    strategy_id         VARCHAR(100) NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    period_type         VARCHAR(20)  NOT NULL,
    period_date         DATE,
    total_signals       INTEGER      DEFAULT 0,
    filled_orders       INTEGER      DEFAULT 0,
    failed_orders       INTEGER      DEFAULT 0,
    rejected_orders     INTEGER      DEFAULT 0,
    winning_trades      INTEGER      DEFAULT 0,
    losing_trades       INTEGER      DEFAULT 0,
    neutral_trades      INTEGER      DEFAULT 0,
    win_rate            NUMERIC(5,2),
    total_pnl           NUMERIC(18,8),
    avg_pnl             NUMERIC(18,8),
    median_pnl          NUMERIC(18,8),
    max_win             NUMERIC(18,8),
    max_loss            NUMERIC(18,8),
    consecutive_wins    INTEGER      DEFAULT 0,
    consecutive_losses  INTEGER      DEFAULT 0,
    profit_factor       NUMERIC(10,4),
    largest_drawdown    NUMERIC(5,2),
    calculated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id, period_type, period_date)
);

CREATE INDEX IF NOT EXISTS idx_strat_perf_strategy ON strategy_performance (strategy_id);
CREATE INDEX IF NOT EXISTS idx_strat_perf_period   ON strategy_performance (period_type, period_date DESC);

-- ─── Strategy webhook call log ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS strategy_webhook_calls (
    id            BIGSERIAL PRIMARY KEY,
    strategy_id   VARCHAR(100) NOT NULL REFERENCES strategies(id),
    received_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    http_status   INTEGER,
    error_message TEXT,
    source_ip     INET
);

CREATE INDEX IF NOT EXISTS swc_strategy_id_idx    ON strategy_webhook_calls (strategy_id, received_at DESC);
CREATE INDEX IF NOT EXISTS swc_received_at_idx    ON strategy_webhook_calls (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_calls_status   ON strategy_webhook_calls (http_status);
CREATE INDEX IF NOT EXISTS idx_webhook_calls_strategy ON strategy_webhook_calls (strategy_id, received_at DESC);

-- ─── Signal / execution audit trail ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signal_log (
    id          BIGSERIAL PRIMARY KEY,
    received_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    strategy_id VARCHAR(100) REFERENCES strategies(id) ON DELETE SET NULL,
    source_ip   INET,
    raw_body    JSONB,
    http_status INTEGER,
    outcome     VARCHAR(30),
    error_detail TEXT,
    duration_ms INTEGER
);

CREATE INDEX IF NOT EXISTS signal_log_strategy_time_idx ON signal_log (strategy_id, received_at DESC);
CREATE INDEX IF NOT EXISTS signal_log_outcome_idx       ON signal_log (outcome);

CREATE TABLE IF NOT EXISTS order_execution_log (
    id                BIGSERIAL PRIMARY KEY,
    signal_log_id     BIGINT REFERENCES signal_log(id),
    account_id        VARCHAR(100) REFERENCES exchange_accounts(id),
    exchange          VARCHAR(30)  NOT NULL,
    exchange_order_id VARCHAR(100),
    client_order_id   VARCHAR(100) NOT NULL UNIQUE,
    symbol            VARCHAR(20)  NOT NULL,
    side              VARCHAR(10)  NOT NULL,
    order_type        VARCHAR(20)  NOT NULL,
    requested_size    NUMERIC      NOT NULL,
    requested_price   NUMERIC,
    status            VARCHAR(20)  NOT NULL,
    cumulative_filled NUMERIC      DEFAULT 0,
    avg_fill_price    NUMERIC      DEFAULT 0,
    exchange_fee      NUMERIC      DEFAULT 0,
    error_message     TEXT,
    placed_at         TIMESTAMPTZ,
    filled_at         TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS oel_signal_log_idx   ON order_execution_log (signal_log_id);
CREATE INDEX IF NOT EXISTS oel_exchange_oid_idx ON order_execution_log (exchange_order_id);

-- ─── Triggers ────────────────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_exchange_accounts_updated_at') THEN
        CREATE TRIGGER update_exchange_accounts_updated_at
            BEFORE UPDATE ON exchange_accounts
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_orders_updated_at') THEN
        CREATE TRIGGER update_orders_updated_at
            BEFORE UPDATE ON orders
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_strategies_updated_at') THEN
        CREATE TRIGGER update_strategies_updated_at
            BEFORE UPDATE ON strategies
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_strategy_positions_modtime') THEN
        CREATE TRIGGER update_strategy_positions_modtime
            BEFORE UPDATE ON strategy_positions
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_strategy_stats_modtime') THEN
        CREATE TRIGGER update_strategy_stats_modtime
            BEFORE UPDATE ON strategy_stats
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_config_modtime') THEN
        CREATE TRIGGER update_config_modtime
            BEFORE UPDATE ON config
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
