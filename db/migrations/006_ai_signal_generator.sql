-- ── AI Strategy Configuration ─────────────────────────────────────────
-- Per-strategy AI operational parameters (one row per AI strategy)
CREATE TABLE IF NOT EXISTS ai_strategy_config (
    strategy_id             VARCHAR(100) PRIMARY KEY REFERENCES strategies(id) ON DELETE CASCADE,

    -- Scheduling
    interval_no_position    VARCHAR(10)  NOT NULL DEFAULT '4h',   -- '1h','2h','4h','8h','1d'
    interval_position_open  VARCHAR(10)  NOT NULL DEFAULT '15m',  -- '5m','15m','30m'
    interval_at_risk        VARCHAR(10)  NOT NULL DEFAULT '5m',   -- '1m','5m','10m'
    at_risk_threshold_pct   NUMERIC(5,2) NOT NULL DEFAULT 1.50,   -- trigger tighter interval

    -- Data sources (toggles)
    use_technical           BOOLEAN NOT NULL DEFAULT TRUE,
    use_fear_greed          BOOLEAN NOT NULL DEFAULT TRUE,
    use_funding_rate        BOOLEAN NOT NULL DEFAULT TRUE,
    use_open_interest       BOOLEAN NOT NULL DEFAULT TRUE,
    use_news                BOOLEAN NOT NULL DEFAULT TRUE,
    use_economic_calendar   BOOLEAN NOT NULL DEFAULT FALSE,
    use_btc_dominance       BOOLEAN NOT NULL DEFAULT FALSE,
    use_macro               BOOLEAN NOT NULL DEFAULT FALSE,

    -- Technical indicators (array of enabled indicators)
    indicators              TEXT[]  NOT NULL DEFAULT ARRAY['RSI','MACD','EMA50','EMA200','BB','VWAP'],
    lookback_days           INTEGER NOT NULL DEFAULT 90,

    -- Signal quality gates
    confidence_threshold    NUMERIC(4,3) NOT NULL DEFAULT 0.720,
    cooldown_entry_minutes  INTEGER NOT NULL DEFAULT 240,
    cooldown_increase_minutes INTEGER NOT NULL DEFAULT 60,
    cooldown_stop_adj_minutes INTEGER NOT NULL DEFAULT 30,

    -- Prompt
    template_id             VARCHAR(50) NOT NULL DEFAULT 'trend_following',
    custom_instructions     TEXT,

    -- Event triggers
    trigger_news_high       BOOLEAN NOT NULL DEFAULT TRUE,
    trigger_volume_spike    BOOLEAN NOT NULL DEFAULT TRUE,
    trigger_funding_spike   BOOLEAN NOT NULL DEFAULT TRUE,
    trigger_key_level       BOOLEAN NOT NULL DEFAULT TRUE,
    trigger_liquidation     BOOLEAN NOT NULL DEFAULT FALSE,
    volume_spike_threshold  NUMERIC(6,1) NOT NULL DEFAULT 300.0,  -- % above average
    funding_spike_threshold NUMERIC(6,4) NOT NULL DEFAULT 0.0500, -- %

    -- Safety
    dry_run                 BOOLEAN NOT NULL DEFAULT TRUE,
    emergency_exit_pct      NUMERIC(5,2) NOT NULL DEFAULT 2.50,   -- exit outside LLM cycle

    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by              VARCHAR(100)
);

-- ── Risk Configuration ────────────────────────────────────────────────
-- Hard portfolio limits, configurable via UI but enforced in Python
CREATE TABLE IF NOT EXISTS ai_risk_config (
    strategy_id             VARCHAR(100) PRIMARY KEY REFERENCES strategies(id) ON DELETE CASCADE,

    max_position_size_pct   NUMERIC(5,2) NOT NULL DEFAULT 5.00,   -- % of account balance
    max_daily_loss_pct      NUMERIC(5,2) NOT NULL DEFAULT 3.00,   -- % of account balance
    max_drawdown_pct        NUMERIC(5,2) NOT NULL DEFAULT 8.00,   -- % of account balance
    max_concurrent_trades   INTEGER      NOT NULL DEFAULT 1,

    -- Immutable floor values — never overridden by UI
    -- These are enforced server-side in the API endpoint, not stored here
    -- Floor: max_position_size_pct <= 20.0
    -- Floor: max_daily_loss_pct >= 0.5
    -- Floor: max_drawdown_pct >= 1.0

    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by              VARCHAR(100)
);

-- ── Risk Config Audit Log ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_risk_config_audit (
    id              BIGSERIAL PRIMARY KEY,
    strategy_id     VARCHAR(100) NOT NULL,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    changed_by      VARCHAR(100),
    field_name      VARCHAR(100) NOT NULL,
    old_value       TEXT,
    new_value       TEXT
);

-- ── AI Signal Log ─────────────────────────────────────────────────────
-- Extended signal log for AI-originated signals (supplements signal_log)
CREATE TABLE IF NOT EXISTS ai_signal_log (
    id              BIGSERIAL PRIMARY KEY,
    strategy_id     VARCHAR(100) NOT NULL REFERENCES strategies(id),
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger_reason  VARCHAR(50) NOT NULL, -- 'scheduled','news_event','volume_spike','funding_spike','key_level','at_risk'
    cycle_interval  VARCHAR(10),          -- which interval was active: '4h','15m','5m','immediate'

    -- LLM inputs snapshot (for debugging)
    prompt_template VARCHAR(50),
    data_sources_used TEXT[],
    context_tokens  INTEGER,

    -- LLM output
    proposed_action VARCHAR(20),          -- 'open_long','close_long','open_short','close_short','hold','partial_close','adjust_stops'
    confidence      NUMERIC(4,3),
    reasoning       TEXT,

    -- Gate outcome
    gate_passed     BOOLEAN NOT NULL DEFAULT FALSE,
    gate_rejection_reason TEXT,

    -- Webhook outcome (null if gate rejected or dry_run)
    webhook_fired   BOOLEAN NOT NULL DEFAULT FALSE,
    webhook_status  INTEGER,
    order_id        UUID REFERENCES orders(id),
    dry_run         BOOLEAN NOT NULL DEFAULT TRUE,

    -- Performance tracking (filled retroactively by analytics job)
    outcome_pnl     NUMERIC,
    outcome_pct     NUMERIC,
    outcome_filled_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ai_sl_strategy_id_idx ON ai_signal_log (strategy_id);
CREATE INDEX IF NOT EXISTS ai_sl_triggered_at_idx ON ai_signal_log (triggered_at DESC);
CREATE INDEX IF NOT EXISTS ai_sl_proposed_action_idx ON ai_signal_log (proposed_action);
CREATE INDEX IF NOT EXISTS ai_sl_confidence_idx ON ai_signal_log (confidence);

-- ── Prompt Templates ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_prompt_templates (
    id          VARCHAR(50) PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    description TEXT,
    system_prompt TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO ai_prompt_templates (id, name, description, system_prompt) VALUES
(
    'trend_following',
    'Trend Following',
    'Identifies and trades sustained directional momentum using EMA crossovers and MACD confirmation.',
    'You are a quantitative crypto analyst specializing in trend-following strategies on perpetual futures.
Your primary signals are EMA crossovers (50/200), MACD histogram direction, and volume confirmation.
You prefer high-confidence setups with clear directional bias. You avoid counter-trend trades.
In ranging markets, output HOLD. Only recommend a trade when multiple indicators align.'
),
(
    'mean_reversion',
    'Mean Reversion',
    'Identifies overextended price moves and trades the return to equilibrium.',
    'You are a quantitative crypto analyst specializing in mean-reversion strategies on perpetual futures.
Your primary signals are RSI extremes (oversold <30, overbought >70), Bollinger Band squeezes, and VWAP deviation.
You trade against extended moves, expecting price to return toward the mean.
You require confirmation that momentum is slowing before recommending entry. You use tight stop losses.'
),
(
    'breakout',
    'Breakout Hunter',
    'Identifies and trades volume-confirmed breakouts above key structural levels.',
    'You are a quantitative crypto analyst specializing in breakout strategies on perpetual futures.
Your primary signals are price breaking above/below consolidation zones with volume confirmation (>150% average).
You look for compression patterns (BB squeeze, low ATR) followed by expansion.
You require the breakout candle to close convincingly beyond the level. False breakouts without volume are HOLD.'
),
(
    'scalper',
    'Scalper',
    'High-frequency short-duration trades on lower timeframes with tight risk management.',
    'You are a quantitative crypto analyst specializing in scalping strategies on perpetual futures.
You trade on short timeframes (15m-1H). Your primary signals are VWAP positioning, order flow imbalance, and momentum bursts.
You use very tight stop losses (0.3-0.8%). You close positions quickly — target hold time under 2 hours.
You avoid entering during low-volume periods or major news events.'
),
(
    'conservative',
    'Conservative',
    'Low-frequency, high-conviction trades only. Capital preservation priority.',
    'You are a conservative quantitative crypto analyst specializing in low-frequency, high-conviction setups on perpetual futures.
You require confluence of at least 4 independent signals before recommending a trade.
You express confidence above 0.85 only when the setup is exceptional. You default to HOLD when uncertain.
You give significant weight to macro conditions and sentiment data. Capital preservation always overrides opportunity.'
)
ON CONFLICT (id) DO NOTHING;
