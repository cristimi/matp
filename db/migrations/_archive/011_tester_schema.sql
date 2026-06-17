-- ============================================================
-- Migration 011: Strategy Tester schema (v1.1)
-- Creates the tester schema and all tester-specific tables.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS tester;

-- ── tester.strategies ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.strategies (
    id                         VARCHAR(100) PRIMARY KEY,
    name                       VARCHAR(100) NOT NULL,
    class                      VARCHAR(100) NOT NULL DEFAULT 'webhook',
    symbol                     VARCHAR(50)  NOT NULL,
    interval                   VARCHAR(10)  NOT NULL DEFAULT '1h',
    platform                   VARCHAR(20)  NOT NULL DEFAULT 'auto',
    enabled                    BOOLEAN      NOT NULL DEFAULT TRUE,
    type                       VARCHAR(20)  NOT NULL DEFAULT 'internal',
    config_yaml                TEXT         NOT NULL DEFAULT '',
    config                     JSONB        NOT NULL DEFAULT '{}',
    webhook_secret             VARCHAR(255) NOT NULL DEFAULT encode(gen_random_bytes(16), 'hex'),
    webhook_enabled            BOOLEAN               DEFAULT FALSE,
    description                TEXT,
    platform_override          VARCHAR(20),
    max_daily_signals          INTEGER               DEFAULT 500,
    max_position_size          NUMERIC               DEFAULT 1.0,
    max_leverage               INTEGER               DEFAULT 10,
    max_daily_drawdown_percent NUMERIC               DEFAULT 20,
    capital_allocation_percent NUMERIC               DEFAULT 100,
    signals_today              INTEGER               DEFAULT 0,
    pnl_today                  NUMERIC               DEFAULT 0,
    pnl_total                  NUMERIC               DEFAULT 0,
    win_count                  INTEGER               DEFAULT 0,
    loss_count                 INTEGER               DEFAULT 0,
    last_signal_at             TIMESTAMPTZ,
    tags                       TEXT[]                DEFAULT '{}',
    account_id                 VARCHAR(100),
    pair_id                    INTEGER,
    allow_quote_variants       BOOLEAN      NOT NULL DEFAULT FALSE,
    allow_cross_charting       BOOLEAN      NOT NULL DEFAULT FALSE,
    default_leverage           INTEGER      NOT NULL DEFAULT 1,
    margin_mode                VARCHAR(10)  NOT NULL DEFAULT 'isolated',
    is_deleted                 BOOLEAN      NOT NULL DEFAULT FALSE,
    blofin_token               TEXT,
    source_matp_id             VARCHAR(100),
    created_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── tester.ai_strategy_config ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.ai_strategy_config (
    id                        BIGSERIAL PRIMARY KEY,
    strategy_id               VARCHAR(100) NOT NULL REFERENCES tester.strategies(id) ON DELETE CASCADE,
    template_id               VARCHAR(100) NOT NULL DEFAULT 'trend_following',
    llm_provider              VARCHAR(50)  NOT NULL DEFAULT 'google',
    llm_model                 VARCHAR(100) NOT NULL DEFAULT 'gemini-2.0-flash',
    use_technical             BOOLEAN      NOT NULL DEFAULT TRUE,
    use_fear_greed            BOOLEAN      NOT NULL DEFAULT FALSE,
    use_funding_rate          BOOLEAN      NOT NULL DEFAULT FALSE,
    use_open_interest         BOOLEAN      NOT NULL DEFAULT FALSE,
    use_news                  BOOLEAN      NOT NULL DEFAULT FALSE,
    use_btc_dominance         BOOLEAN      NOT NULL DEFAULT FALSE,
    use_macro                 BOOLEAN      NOT NULL DEFAULT FALSE,
    indicators                TEXT[]       NOT NULL DEFAULT '{RSI,MACD,EMA50,EMA200,BB,VWAP}',
    lookback_days             INTEGER      NOT NULL DEFAULT 90,
    confidence_threshold      NUMERIC      NOT NULL DEFAULT 0.72,
    cooldown_entry_minutes    INTEGER      NOT NULL DEFAULT 240,
    cooldown_increase_minutes INTEGER      NOT NULL DEFAULT 60,
    cooldown_stop_adj_minutes INTEGER      NOT NULL DEFAULT 30,
    interval_no_position      VARCHAR(10)  NOT NULL DEFAULT '4h',
    interval_position_open    VARCHAR(10)  NOT NULL DEFAULT '1h',
    interval_at_risk          VARCHAR(10)  NOT NULL DEFAULT '15m',
    at_risk_threshold_pct     NUMERIC      NOT NULL DEFAULT 3.0,
    dry_run                   BOOLEAN      NOT NULL DEFAULT TRUE,
    emergency_exit_pct        NUMERIC               DEFAULT 5.0,
    custom_instructions       TEXT,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id)
);

-- ── tester.ai_risk_config ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.ai_risk_config (
    id                     BIGSERIAL PRIMARY KEY,
    strategy_id            VARCHAR(100) NOT NULL REFERENCES tester.strategies(id) ON DELETE CASCADE,
    max_position_size_pct  NUMERIC      NOT NULL DEFAULT 5.0,
    max_daily_loss_pct     NUMERIC      NOT NULL DEFAULT 3.0,
    max_drawdown_pct       NUMERIC      NOT NULL DEFAULT 8.0,
    max_concurrent_trades  INTEGER      NOT NULL DEFAULT 1,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id)
);

-- ── tester.backtest_runs ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.backtest_runs (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id         VARCHAR(100) NOT NULL REFERENCES tester.strategies(id),
    timeframe           VARCHAR(10)  NOT NULL,
    date_from           DATE         NOT NULL,
    date_to             DATE         NOT NULL,
    lookback_days       INTEGER      NOT NULL DEFAULT 90,
    initial_balance     NUMERIC      NOT NULL DEFAULT 1000.0,
    slippage_pct        NUMERIC      NOT NULL DEFAULT 0.05,
    fee_pct             NUMERIC      NOT NULL DEFAULT 0.02,
    status              VARCHAR(40)  NOT NULL DEFAULT 'pending',
    candles_processed   INTEGER               DEFAULT 0,
    total_candles       INTEGER,
    total_signals       INTEGER,
    gate_passed         INTEGER,
    llm_failures        INTEGER               DEFAULT 0,
    llm_failure_rate    NUMERIC(5,2),
    total_trades        INTEGER,
    winning_trades      INTEGER,
    losing_trades       INTEGER,
    win_rate            NUMERIC(5,2),
    total_pnl           NUMERIC(18,8),
    total_pnl_pct       NUMERIC(8,4),
    profit_factor       NUMERIC(10,4),
    max_drawdown_pct    NUMERIC(8,4),
    sharpe_approx       NUMERIC(8,4),
    long_count          INTEGER,
    short_count         INTEGER,
    avg_win             NUMERIC(18,8),
    avg_loss            NUMERIC(18,8),
    largest_win         NUMERIC(18,8),
    largest_loss        NUMERIC(18,8),
    total_fees_paid     NUMERIC(18,8),
    llm_provider        VARCHAR(50),
    llm_model           VARCHAR(100),
    estimated_cost_usd  NUMERIC(10,6),
    actual_tokens_used  INTEGER,
    error_message       TEXT,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CHECK (status IN ('pending','running','completed','failed','cancelled','aborted_high_failure_rate'))
);

CREATE INDEX IF NOT EXISTS tester_runs_strategy_idx ON tester.backtest_runs (strategy_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tester_runs_status_idx   ON tester.backtest_runs (status);

-- ── tester.orders ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.orders (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    backtest_run_id   UUID        NOT NULL REFERENCES tester.backtest_runs(id) ON DELETE CASCADE,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    candle_timestamp  TIMESTAMPTZ NOT NULL,
    symbol            VARCHAR(50) NOT NULL,
    side              VARCHAR(10) NOT NULL,
    signal            VARCHAR(20) NOT NULL,
    order_type        VARCHAR(20) NOT NULL DEFAULT 'market',
    size              NUMERIC     NOT NULL,
    price             NUMERIC,
    leverage          INTEGER,
    margin_mode       VARCHAR(10),
    tp_price          NUMERIC,
    sl_price          NUMERIC,
    platform          VARCHAR(20) NOT NULL DEFAULT 'simulated',
    strategy_id       VARCHAR(100) NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'filled',
    actual_fill_price NUMERIC,
    pnl               NUMERIC,
    fee               NUMERIC,
    raw_webhook       JSONB        NOT NULL DEFAULT '{}',
    signal_source     VARCHAR(100) NOT NULL DEFAULT 'ai_signal_generator',
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tester_orders_run_idx      ON tester.orders (backtest_run_id);
CREATE INDEX IF NOT EXISTS tester_orders_strategy_idx ON tester.orders (strategy_id);

-- ── tester.strategy_positions ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.strategy_positions (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    backtest_run_id   UUID        NOT NULL REFERENCES tester.backtest_runs(id) ON DELETE CASCADE,
    strategy_id       VARCHAR(100) NOT NULL,
    exchange          VARCHAR(20)  NOT NULL DEFAULT 'simulated',
    symbol            VARCHAR(50)  NOT NULL,
    side              VARCHAR(10)  NOT NULL,
    entry_price       NUMERIC      NOT NULL,
    current_price     NUMERIC,
    closing_price     NUMERIC,
    size              NUMERIC      NOT NULL,
    leverage          INTEGER,
    margin_mode       VARCHAR(20),
    pnl_unrealized    NUMERIC,
    pnl_realized      NUMERIC      DEFAULT 0,
    fee_open          NUMERIC      DEFAULT 0,
    fee_close         NUMERIC      DEFAULT 0,
    status            VARCHAR(20)  DEFAULT 'open',
    opening_order_id  UUID REFERENCES tester.orders(id) ON DELETE SET NULL,
    closing_order_id  UUID REFERENCES tester.orders(id) ON DELETE SET NULL,
    close_reason      VARCHAR(50),
    opened_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_at         TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tester_pos_run_idx      ON tester.strategy_positions (backtest_run_id);
CREATE INDEX IF NOT EXISTS tester_pos_strategy_idx ON tester.strategy_positions (strategy_id, status);

-- ── tester.ai_signal_log ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.ai_signal_log (
    id                     BIGSERIAL PRIMARY KEY,
    backtest_run_id        UUID         REFERENCES tester.backtest_runs(id) ON DELETE CASCADE,
    strategy_id            VARCHAR(100) NOT NULL,
    triggered_at           TIMESTAMPTZ  NOT NULL,         -- candle close timestamp, NOT NOW()
    trigger_reason         VARCHAR(50),
    cycle_interval         VARCHAR(10),
    prompt_template        VARCHAR(100),
    data_sources_used      TEXT[]       DEFAULT '{}',
    context_tokens         INTEGER,
    proposed_action        VARCHAR(30),
    confidence             NUMERIC,
    reasoning              TEXT,
    gate_passed            BOOLEAN      NOT NULL DEFAULT FALSE,
    gate_rejection_reason  VARCHAR(50),
    dry_run                BOOLEAN      NOT NULL DEFAULT TRUE,
    llm_provider           VARCHAR(50),
    llm_model              VARCHAR(100),
    webhook_fired          BOOLEAN               DEFAULT FALSE,
    webhook_status         INTEGER,
    order_id               UUID REFERENCES tester.orders(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS tester_signal_log_run_idx      ON tester.ai_signal_log (backtest_run_id);
CREATE INDEX IF NOT EXISTS tester_signal_log_strategy_idx ON tester.ai_signal_log (strategy_id, triggered_at DESC);
-- Critical for cooldown checks scoped per run:
CREATE INDEX IF NOT EXISTS tester_signal_log_cooldown_idx
    ON tester.ai_signal_log (backtest_run_id, strategy_id, proposed_action, gate_passed, triggered_at DESC);

-- ── tester.ohlcv_cache ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.ohlcv_cache (
    id          BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(20)  NOT NULL,
    timeframe   VARCHAR(10)  NOT NULL,
    exchange    VARCHAR(30)  NOT NULL DEFAULT 'binance',
    candle_ts   TIMESTAMPTZ  NOT NULL,
    open        NUMERIC      NOT NULL,
    high        NUMERIC      NOT NULL,
    low         NUMERIC      NOT NULL,
    close       NUMERIC      NOT NULL,
    volume      NUMERIC      NOT NULL,
    fetched_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, timeframe, exchange, candle_ts)
);

CREATE INDEX IF NOT EXISTS tester_ohlcv_lookup_idx ON tester.ohlcv_cache (symbol, timeframe, exchange, candle_ts);

-- ── tester.equity_curve (per-candle, includes mark_balance) ──────────────────

CREATE TABLE IF NOT EXISTS tester.equity_curve (
    id               BIGSERIAL PRIMARY KEY,
    backtest_run_id  UUID        NOT NULL REFERENCES tester.backtest_runs(id) ON DELETE CASCADE,
    candle_ts        TIMESTAMPTZ NOT NULL,
    realized_balance NUMERIC     NOT NULL,
    mark_balance     NUMERIC     NOT NULL,
    trade_pnl        NUMERIC,
    drawdown_pct     NUMERIC,
    UNIQUE (backtest_run_id, candle_ts)
);

CREATE INDEX IF NOT EXISTS tester_equity_run_idx ON tester.equity_curve (backtest_run_id, candle_ts);

-- ── Triggers ──────────────────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tester_strategies_updated_at') THEN
        CREATE TRIGGER update_tester_strategies_updated_at
            BEFORE UPDATE ON tester.strategies
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tester_runs_updated_at') THEN
        CREATE TRIGGER update_tester_runs_updated_at
            BEFORE UPDATE ON tester.backtest_runs
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tester_positions_updated_at') THEN
        CREATE TRIGGER update_tester_positions_updated_at
            BEFORE UPDATE ON tester.strategy_positions
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tester_strategy_config_updated_at') THEN
        CREATE TRIGGER update_tester_strategy_config_updated_at
            BEFORE UPDATE ON tester.ai_strategy_config
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tester_risk_config_updated_at') THEN
        CREATE TRIGGER update_tester_risk_config_updated_at
            BEFORE UPDATE ON tester.ai_risk_config
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
