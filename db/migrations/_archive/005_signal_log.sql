-- Migration 005: Signal Logger
-- Creates signal_log and order_execution_log tables.
-- Safe to run multiple times (IF NOT EXISTS / CREATE INDEX IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS signal_log (
    id           BIGSERIAL    PRIMARY KEY,
    received_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    source_ip    INET,
    strategy_id  VARCHAR(100),
    http_status  INTEGER,
    outcome      VARCHAR(30),
    error_detail TEXT,
    raw_body     JSONB,
    duration_ms  INTEGER
);
CREATE INDEX IF NOT EXISTS signal_log_strategy_time_idx ON signal_log (strategy_id, received_at DESC);
CREATE INDEX IF NOT EXISTS signal_log_outcome_idx       ON signal_log (outcome);

CREATE TABLE IF NOT EXISTS order_execution_log (
    id                BIGSERIAL    PRIMARY KEY,
    signal_log_id     BIGINT       REFERENCES signal_log(id),
    account_id        VARCHAR(100) REFERENCES exchange_accounts(id),
    exchange          VARCHAR(30)  NOT NULL,
    exchange_order_id VARCHAR(100),
    client_order_id   VARCHAR(100) UNIQUE NOT NULL,
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
CREATE INDEX IF NOT EXISTS oel_signal_log_idx    ON order_execution_log (signal_log_id);
CREATE INDEX IF NOT EXISTS oel_exchange_oid_idx  ON order_execution_log (exchange_order_id);

-- Verify
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'signal_log') THEN
    RAISE EXCEPTION 'signal_log table was not created';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'order_execution_log') THEN
    RAISE EXCEPTION 'order_execution_log table was not created';
  END IF;
  RAISE NOTICE 'Migration 005 verified OK';
END $$;
