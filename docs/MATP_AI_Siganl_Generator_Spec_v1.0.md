# MATP AI Signal Generator — Complete Architectural Specification
**Document Version:** 1.0.0  
**Status:** Approved for Implementation  
**Target:** `ai-signal-generator/` — New isolated service, Docker Compose addition  
**Implementation Tool:** Claude Code  

---

## 0. Guiding Principles

1. The AI module is a **signal source only** — it has zero exchange access and zero execution API keys
2. Every LLM call is wrapped by deterministic Python guards — the LLM reasons, Python enforces
3. The module produces payloads that are **byte-for-byte compatible** with the existing MATP `WebhookPayload` schema
4. All configuration is stored in PostgreSQL — no hard-coded thresholds anywhere
5. Dry-run mode is ON by default and must be explicitly disabled per strategy
6. The existing `order-listener`, `order-executor`, and all other MATP services are **not modified** by this spec

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  ai-signal-generator                        │
│                  (New Docker service)                       │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────┐  │
│  │  Scheduler   │   │ Price Monitor│   │  Event Watcher │  │
│  │  (LangGraph) │   │ (Pure Python)│   │  (Pure Python) │  │
│  └──────┬───────┘   └──────┬───────┘   └───────┬────────┘  │
│         │                  │                   │            │
│         └──────────────────┴───────────────────┘            │
│                            │                                │
│                    ┌───────▼────────┐                       │
│                    │ LangGraph      │                       │
│                    │ State Machine  │                       │
│                    │                │                       │
│                    │ Node 1: Ingest │                       │
│                    │ Node 2: Analyze│                       │
│                    │ Node 3: Guard  │                       │
│                    │ Node 4: Dispatch│                      │
│                    └───────┬────────┘                       │
│                            │                                │
└────────────────────────────┼────────────────────────────────┘
                             │ HTTP POST (HMAC signed)
                             ▼
              ┌──────────────────────────────┐
              │  order-listener:8001         │
              │  POST /webhook/{strategy_id} │
              │  (existing, unmodified)      │
              └──────────────────────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │  order-executor:8004         │
              │  (existing, unmodified)      │
              └──────────────────────────────┘
```

### Services that do NOT change
- `order-listener` — receives webhooks as-is
- `order-executor` — executes orders as-is
- `dashboard-api` — extended with new endpoints (§8)
- `dashboard-ui` — extended with new UI components (§9)
- `postgres`, `redis`, `nginx` — no changes

---

## 2. New Database Tables

Add to `db/migrations/004_ai_signal_generator.sql`:

```sql
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
```

---

## 3. Webhook Payload Contract

The AI module must produce a payload that passes `WebhookPayload` validation in `order-listener/app/models.py` **without modification**. The exact schema is:

```python
# From order-listener/app/models.py — DO NOT CHANGE THIS FILE
class WebhookPayload(BaseModel):
    base_asset:      str                          # e.g. "BTC"
    quote_asset:     str                          # e.g. "USDT"
    side:            Literal["buy", "sell"]
    order_type:      Literal["market", "limit"] = "market"
    size:            Decimal                      # resolved from size_pct (see §4)
    price:           Optional[Decimal] = None
    leverage:        Optional[int] = None
    margin_mode:     Optional[Literal["cross", "isolated"]] = None
    tp_price:        Optional[Decimal] = None
    sl_price:        Optional[Decimal] = None
    signal:          Literal["open_long", "close_long", "open_short", "close_short"]
    target_position: Optional[Literal["long", "short", "flat"]] = None
    timestamp:       datetime
    token:           str                          # strategy webhook_secret
    signal_source:   Optional[str] = "ai_engine" # always "ai_engine" for AI signals
    signal_metadata: Optional[dict] = {}         # includes reasoning, confidence, trigger
    indicator_price: Optional[Decimal] = None
```

### LLM Structured Output Schema (Pydantic, Agent 3 output)

This is what the LLM produces. Agent 4 (pure Python) transforms it into the `WebhookPayload` above:

```python
class LLMSignalOutput(BaseModel):
    action: Literal[
        "open_long",
        "open_short", 
        "close_long",
        "close_short",
        "hold",
        "partial_close",
        "adjust_stops"
    ]
    confidence:        float       # 0.0 – 0.95 (hard cap, never 1.0)
    size_pct:          float       # % of account balance (0.1 – 20.0)
    stop_loss_pct:     float       # distance from entry as % (e.g. 1.5 = 1.5%)
    take_profit_pct:   float       # distance from entry as % (e.g. 3.0 = 3.0%)
    new_sl_price:      Optional[float] = None   # only for adjust_stops
    new_tp_price:      Optional[float] = None   # only for adjust_stops
    reasoning:         str         # 50–500 chars, must cite specific indicator values
```

### Action to Signal Mapping (Agent 4 responsibility)

```python
ACTION_TO_SIGNAL = {
    "open_long":    ("buy",  "open_long"),
    "open_short":   ("sell", "open_short"),
    "close_long":   ("sell", "close_long"),
    "close_short":  ("buy",  "close_short"),
    "partial_close": ...  # determined by current position side
}
# "hold" and "adjust_stops" → no webhook fired
```

---

## 4. LangGraph State Machine

### State Definition

```python
from typing import TypedDict, Optional, List
from datetime import datetime

class AgentState(TypedDict):
    # Identity
    strategy_id:        str
    strategy_config:    dict      # from strategies + ai_strategy_config
    risk_config:        dict      # from ai_risk_config

    # Trigger context
    trigger_reason:     str       # 'scheduled' | 'news_event' | 'volume_spike' | etc.
    cycle_interval:     str       # '4h' | '15m' | '5m' | 'immediate'
    triggered_at:       datetime

    # Position context (from MATP DB)
    position_open:      bool
    position_side:      Optional[str]      # 'long' | 'short'
    position_entry_price: Optional[float]
    position_size:      Optional[float]
    position_unrealized_pnl_pct: Optional[float]
    position_opened_at: Optional[datetime]
    original_reasoning: Optional[str]      # reasoning from entry signal

    # Ingested data (Node 1 output)
    ohlcv_data:         Optional[dict]
    technical_indicators: Optional[dict]
    sentiment_data:     Optional[dict]
    news_digest:        Optional[str]
    market_context:     Optional[dict]
    data_fetch_errors:  List[str]          # non-fatal data source failures

    # LLM output (Node 2 output)
    llm_signal:         Optional[dict]     # LLMSignalOutput as dict
    context_tokens:     Optional[int]

    # Gate result (Node 3 output)
    gate_passed:        bool
    gate_rejection_reason: Optional[str]
    resolved_size:      Optional[float]    # size_pct resolved to base asset quantity
    resolved_sl_price:  Optional[float]
    resolved_tp_price:  Optional[float]

    # Dispatch result (Node 4 output)
    webhook_fired:      bool
    webhook_status:     Optional[int]
    order_id:           Optional[str]
    signal_log_id:      Optional[int]
```

### Node Definitions

**Node 1 — Data Ingestion (Pure Python)**

Fetches from all enabled data sources. Failures are non-fatal — logged to `data_fetch_errors`, cycle continues with available data.

```
Inputs:  strategy_config, position context
Outputs: ohlcv_data, technical_indicators, sentiment_data, news_digest, market_context

Data sources and libraries:
- OHLCV:         ccxt (already in project ecosystem)
- Indicators:    pandas-ta (RSI, MACD, EMA, BB, VWAP, ATR)
- Fear & Greed:  requests → https://api.alternative.me/fng/ (free, no key)
- Funding Rate:  ccxt (blofin/hyperliquid native)
- Open Interest: ccxt
- News:          requests → https://cryptopanic.com/api/v1/posts/ (free tier)
- BTC Dominance: requests → CoinGecko global endpoint (free)
- DXY/US10Y:     yfinance (^DXY, ^TNX)
- Econ Calendar: requests → investing.com or finnhub.io (free tier)

Lookback: config.lookback_days of daily OHLCV + last N candles of strategy interval
```

**Node 2 — LLM Analysis (Gemini)**

Assembles the full prompt context and calls Gemini. Returns structured `LLMSignalOutput`.

```
Model:       gemini-1.5-pro (position open) | gemini-1.5-flash (no position, hunting)
Temperature: 0.1 (low, not zero — allows nuanced reasoning)
Max tokens:  1024
Format:      response_mime_type="application/json", response_schema=LLMSignalOutput
```

Cost optimisation: use Flash when no position is open (routine scanning). Use Pro when actively managing an open position (higher stakes reasoning).

**Node 3 — Risk Gate (Pure Python, no LLM)**

All checks are hard Python assertions. If any fails, `gate_passed = False` and cycle ends.

```python
checks = [
    # 1. Confidence threshold
    signal.confidence >= config.confidence_threshold,

    # 2. Action coherence (can't open if already open, can't close if nothing open)
    validate_action_coherence(signal.action, state.position_open),

    # 3. Cooldown (per action type)
    not in_cooldown(strategy_id, signal.action, config),

    # 4. Max position size
    resolved_size <= risk_config.max_position_size_pct,

    # 5. Daily loss cap
    not daily_loss_exceeded(strategy_id, risk_config.max_daily_loss_pct),

    # 6. Max drawdown
    not max_drawdown_exceeded(strategy_id, risk_config.max_drawdown_pct),

    # 7. Dry run gate (if dry_run=True, gate always "passes" but webhook marked dry_run)
    True  # dry_run handled in Node 4, not here
]
```

**Node 4 — Webhook Dispatcher (Pure Python, no LLM)**

Resolves `size_pct` to actual base asset quantity, constructs `WebhookPayload`, signs it, dispatches it.

```python
def resolve_size(size_pct: float, strategy: dict) -> Decimal:
    """
    Fetch account balance from order-executor:8004
    Calculate USDT allocation = balance * size_pct / 100
    Fetch current price from ccxt
    Calculate base quantity = usdt_allocation / current_price
    Round to exchange lot size (from trading_pairs.exchange_meta)
    """

def build_payload(signal: LLMSignalOutput, state: AgentState) -> dict:
    return {
        "base_asset":    state["strategy_config"]["base_asset"],
        "quote_asset":   state["strategy_config"]["quote_asset"],
        "side":          ACTION_TO_SIGNAL[signal.action][0],
        "order_type":    "market",
        "size":          str(state["resolved_size"]),
        "sl_price":      str(state["resolved_sl_price"]) if state["resolved_sl_price"] else None,
        "tp_price":      str(state["resolved_tp_price"]) if state["resolved_tp_price"] else None,
        "signal":        ACTION_TO_SIGNAL[signal.action][1],
        "timestamp":     datetime.utcnow().isoformat() + "Z",
        "token":         state["strategy_config"]["webhook_secret"],
        "signal_source": "ai_engine",
        "signal_metadata": {
            "confidence":     signal.confidence,
            "reasoning":      signal.reasoning,
            "trigger_reason": state["trigger_reason"],
            "template_id":    state["strategy_config"]["template_id"],
            "dry_run":        state["strategy_config"]["dry_run"]
        }
    }

def sign_payload(payload: dict, secret: str) -> str:
    """
    CRITICAL: use json.dumps with sort_keys=True, separators=(',',':')
    to guarantee deterministic byte output before HMAC signing.
    """
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hmac.new(secret.encode('utf-8'), payload_bytes, hashlib.sha256).hexdigest()
```

If `dry_run=True`: build payload, log to `ai_signal_log` with `dry_run=True`, **do not POST to order-listener**. This is the default state for all new AI strategies.

---

## 5. Adaptive Scheduler

```python
class AdaptiveScheduler:
    """
    Runs per strategy. Maintains the current interval and wakes
    the LangGraph state machine on schedule or on event trigger.
    """

    def get_interval_seconds(self, strategy_id: str) -> int:
        config = self.load_config(strategy_id)
        position = self.get_position_state(strategy_id)

        if not position.open:
            return parse_interval(config.interval_no_position)

        unrealized_pnl_pct = abs(position.unrealized_pnl_pct or 0.0)
        if unrealized_pnl_pct >= config.at_risk_threshold_pct:
            return parse_interval(config.interval_at_risk)

        return parse_interval(config.interval_position_open)
```

### Event Triggers (Pure Python monitors)

These run continuously alongside the scheduler. When a trigger fires, the LangGraph cycle runs immediately regardless of the timer.

```python
TRIGGER_MONITORS = {
    "news_high_impact": lambda: cryptopanic_has_high_impact_news(),
    "volume_spike":     lambda cfg: current_volume_pct_above_avg() > cfg.volume_spike_threshold,
    "funding_spike":    lambda cfg: abs(current_funding_rate()) > cfg.funding_spike_threshold,
    "key_level_breach": lambda: price_crossed_support_resistance(),
    "liquidation_cascade": lambda: liquidation_volume_spike_detected(),
}
```

---

## 6. Price Monitor (Independent of LangGraph)

Runs every 60 seconds per open position. No LLM involved. Fires emergency close if unrealized loss exceeds `emergency_exit_pct`.

```python
async def price_monitor_loop(strategy_id: str):
    while True:
        await asyncio.sleep(60)
        position = get_open_position(strategy_id)
        if not position:
            continue

        config = get_risk_config(strategy_id)
        current_price = await fetch_current_price(strategy_id)
        unrealized_pct = calculate_unrealized_pct(position, current_price)

        if unrealized_pct < -config.emergency_exit_pct:
            logger.warning(
                f"Emergency exit triggered for {strategy_id}: "
                f"unrealized={unrealized_pct:.2f}% > threshold={-config.emergency_exit_pct:.2f}%"
            )
            await fire_emergency_close(strategy_id, reason="emergency_price_monitor")
```

The `fire_emergency_close` function bypasses the LangGraph cycle entirely and posts directly to the order-listener webhook with `signal: close_long` or `close_short`.

---

## 7. Prompt Context Template

This is the full context assembled and injected into the LLM at every cycle. Python fills the `{{variables}}` before sending.

```
═══════════════════════════════════════════════════════════
MATP AI ANALYSIS — {{base_asset}}-{{quote_asset}} — {{interval}}
Generated: {{timestamp}} UTC
Analysis Trigger: {{trigger_reason}}
{{#if trigger_detail}}Trigger Detail: {{trigger_detail}}{{/if}}
═══════════════════════════════════════════════════════════

{{#if position_open}}
⚠️  ACTIVE POSITION — EXIT EVALUATION MODE
Direction:     {{position_side}}
Entry Price:   {{entry_price}}
Current P&L:   {{unrealized_pnl_pct}}%
Time Open:     {{time_open_hours}}h {{time_open_minutes}}m
Original Thesis: "{{original_reasoning}}"
Current SL:    {{current_sl_price}}
Current TP:    {{current_tp_price}}
{{/if}}

{{#if use_technical}}
TECHNICAL INDICATORS ({{interval}} timeframe):
Current Price:    {{current_price}}
24h Change:       {{price_change_24h}}%
7d Change:        {{price_change_7d}}%

RSI(14):          {{rsi_14}} — {{rsi_interpretation}}
MACD:             hist {{macd_hist}}, signal cross {{macd_signal_bars}} bars ago
EMA 50/200:       {{ema_cross_status}}
BB:               {{bb_interpretation}}
VWAP:             price {{vwap_deviation}}% {{vwap_direction}} VWAP
ATR(14):          {{atr_14}} ({{atr_pct_of_price}}% of price)

Key Levels:
  Nearest Support:    {{support_1}} ({{support_1_strength}} tests)
  Nearest Resistance: {{resistance_1}} ({{resistance_1_strength}} tests)
  Pattern:            {{chart_pattern}}
{{/if}}

{{#if use_fear_greed}}
SENTIMENT:
Fear & Greed Index:   {{fear_greed_value}} ({{fear_greed_label}})
{{/if}}
{{#if use_funding_rate}}
Funding Rate:         {{funding_rate}}% ({{funding_rate_interpretation}})
{{/if}}
{{#if use_open_interest}}
Open Interest:        ${{open_interest_b}}B ({{oi_change_24h}}% 24h)
Long/Short Ratio:     {{long_short_ratio}} ({{ls_interpretation}})
{{/if}}

{{#if use_news}}
NEWS DIGEST (last {{news_lookback_hours}} hours):
{{#each news_items}}
[{{severity}}] {{headline}}
{{/each}}
{{#if no_news}}No significant news in the lookback window.{{/if}}
{{/if}}

{{#if use_btc_dominance}}
BTC Dominance:        {{btc_dominance}}% ({{btc_dom_trend}})
{{/if}}
{{#if use_macro}}
DXY:                  {{dxy}} ({{dxy_trend}})
US10Y:                {{us10y}}% ({{us10y_trend}})
{{/if}}

PORTFOLIO CONTEXT:
Account Balance:      {{account_balance}} USDT
Today's P&L:          {{pnl_today_pct}}%  (cap: {{max_daily_loss_pct}}%)
Max Position Size:    {{max_position_size_pct}}%

{{#if no_position}}
Last Signal:          {{last_signal_action}} ({{last_signal_hours}}h ago, confidence {{last_signal_confidence}})
{{/if}}

STRATEGY INSTRUCTIONS:
{{system_prompt}}

{{#if custom_instructions}}
ADDITIONAL RULES:
{{custom_instructions}}
{{/if}}

═══════════════════════════════════════════════════════════
YOUR TASK:
{{#if position_open}}
Evaluate whether the original thesis for this {{position_side}} position is still valid.
Consider all new data since the position was opened.
If the thesis is intact: output "hold" or "adjust_stops" with updated levels.
If the thesis is weakening: output "partial_close".
If the thesis is invalidated or a new risk is present: output "close_{{position_side}}".
If the position is showing strong continuation: output "increase" (only if within size limits).
{{else}}
Identify whether current market conditions present a high-conviction trade setup.
If a setup exists: output "open_long" or "open_short" with full parameters.
If conditions are unclear or insufficient confluence: output "hold".
{{/if}}

CONFIDENCE SCALE:
0.50-0.65: Speculative — below threshold, will be rejected
0.65-0.75: Moderate — meets minimum threshold
0.75-0.85: High conviction — clear confluence
0.85-0.95: Exceptional setup — multiple independent signals aligned
Never output confidence above 0.95.

OUTPUT: Structured JSON only. reasoning field must cite specific indicator values.
═══════════════════════════════════════════════════════════
```

---

## 8. New API Endpoints (dashboard-api)

Add to `dashboard-api/src/routes/`. All endpoints require the existing auth pattern.

```
# AI Strategy Config
GET    /api/strategies/:id/ai-config          → returns ai_strategy_config row
PUT    /api/strategies/:id/ai-config          → update config, validate floors
GET    /api/strategies/:id/ai-config/preview-prompt → returns assembled prompt (no LLM call)

# Risk Config  
GET    /api/strategies/:id/risk-config        → returns ai_risk_config row
PUT    /api/strategies/:id/risk-config        → update with floor enforcement + audit log

# AI Signal Log
GET    /api/strategies/:id/ai-signals         → paginated ai_signal_log
GET    /api/strategies/:id/ai-signals/stats   → aggregate stats (win rate by confidence tier etc.)

# Dry Run Control
POST   /api/strategies/:id/ai-config/enable-live  → sets dry_run=false (requires confirmation token)
POST   /api/strategies/:id/ai-config/enable-dry   → sets dry_run=true

# Manual trigger (for testing)
POST   /api/strategies/:id/ai-trigger         → manually trigger one analysis cycle (dry_run forced)
```

### Floor enforcement in PUT /risk-config

```typescript
const RISK_FLOORS = {
    max_position_size_pct: { max: 20.0 },
    max_daily_loss_pct:    { min: 0.5 },
    max_drawdown_pct:      { min: 1.0 },
    max_concurrent_trades: { min: 1, max: 5 }
}
// Reject 400 if any floor is violated
// Write audit log row for every changed field
```

---

## 9. UI Components (dashboard-ui)

### 9.1 Add Strategy Modal — AI Type

Add `strategy_type` toggle to the existing Add Strategy modal:

```
○ TradingView Signal    ● AI Autonomous
```

When AI Autonomous is selected, the form renders four sections:

**Section 1: Identity** (same fields as TV strategy — name, asset, exchange, account, leverage, margin mode)

**Section 2: Operational Parameters**
```
Analysis Intervals
  No Position      [4H ▾]
  Position Open    [15M ▾]
  At Risk          [5M ▾]
  At-Risk Threshold [1.50] %

Data Sources
  [✓] Technical Indicators
  [✓] Fear & Greed Index
  [✓] Funding Rate & Open Interest
  [✓] Crypto News (CryptoPanic)
  [ ] Economic Calendar
  [ ] BTC Dominance
  [ ] Macro (DXY, US10Y)

Indicators (when Technical enabled)
  [RSI] [MACD] [EMA50] [EMA200] [BB] [VWAP] [+ Add]
  Lookback Period  [90] days
```

**Section 3: Strategy Prompt**
```
Base Template   [Trend Following ▾]
                (description of selected template shown here)

Custom Instructions
┌─────────────────────────────────────────┐
│ Additional rules appended to the base  │
│ template. E.g. "Avoid signals during   │
│ weekends" or "Only trade in bull market"│
└─────────────────────────────────────────┘
[Preview Full Prompt]  ← calls GET /ai-config/preview-prompt
```

**Section 4: Risk Config**
```
Max Position Size    [5.0] % of balance
Daily Loss Cap       [3.0] %
Max Drawdown         [8.0] %
Max Concurrent       [1] trade(s)

Emergency Exit       [2.5] % (price monitor threshold)
Confidence Threshold [0.72] (0.50 – 0.95)
Entry Cooldown       [240] minutes

Dry Run Mode         [● ON  ○ OFF]  ← always ON for new strategies
```

### 9.2 Strategy Card — AI Type Indicator

On the Strategies screen, AI strategies show a `[AI]` badge in `c-tech` style next to the strategy name. The card also shows:
- Interval pills instead of "Last signal"
- `[DRY RUN]` badge in orange/failed-color if dry_run=true

### 9.3 Strategy Detail Page — AI Tab

On the strategy detail page, add an "AI Signals" tab that shows `ai_signal_log` entries with:
- Confidence badge (color-coded: green ≥0.75, orange 0.65-0.75, red <0.65)
- Action taken / gate rejection reason
- Expandable reasoning drawer
- Dry Run indicator on each entry

### 9.4 Risk Config Edit

On the strategy detail page, "Risk Config" section with inline editing. Every save shows a confirmation modal: *"Confirm new daily loss cap: 3.0% → 2.0%"*. Changes are written to `ai_risk_config_audit`.

---

## 10. Docker Service Definition

Add to `docker-compose.yml`:

```yaml
  ai-signal-generator:
    build: ./ai-signal-generator
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_URL: redis://redis:6379
      GEMINI_API_KEY: ${GEMINI_API_KEY}
      CRYPTOPANIC_API_KEY: ${CRYPTOPANIC_API_KEY}
      MATP_LISTENER_URL: http://order-listener:8001
      MATP_EXECUTOR_URL: http://order-executor:8004
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      order-listener:
        condition: service_healthy
      order-executor:
        condition: service_healthy
    networks: [matp_net]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import requests; requests.get('http://localhost:8005/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
```

Add to `.env`:
```
GEMINI_API_KEY=
CRYPTOPANIC_API_KEY=   # free tier at cryptopanic.com
```

---

## 11. New Python Service Structure

```
ai-signal-generator/
├── Dockerfile
├── requirements.txt
├── app/
│   ├── main.py                    # FastAPI app, health endpoint :8005
│   ├── config.py                  # settings from env
│   ├── database.py                # asyncpg pool (same pattern as order-listener)
│   ├── scheduler.py               # AdaptiveScheduler, per-strategy loops
│   ├── price_monitor.py           # Emergency exit monitor
│   ├── event_watcher.py           # Event trigger monitors
│   ├── graph/
│   │   ├── state.py               # AgentState TypedDict
│   │   ├── graph.py               # LangGraph StateGraph definition
│   │   ├── nodes/
│   │   │   ├── node_ingest.py     # Node 1: data ingestion
│   │   │   ├── node_analyze.py    # Node 2: Gemini LLM call
│   │   │   ├── node_guard.py      # Node 3: risk gate (pure Python)
│   │   │   └── node_dispatch.py   # Node 4: webhook dispatch
│   │   └── checkpointer.py        # LangGraph PostgreSQL checkpointer
│   ├── data/
│   │   ├── ohlcv.py               # ccxt OHLCV fetcher
│   │   ├── indicators.py          # pandas-ta indicator computation
│   │   ├── sentiment.py           # Fear & Greed, funding rate, OI
│   │   ├── news.py                # CryptoPanic fetcher
│   │   └── macro.py               # DXY, BTC dominance
│   ├── prompt/
│   │   ├── builder.py             # Assembles full prompt from template + data
│   │   └── templates.py           # Prompt template loader from DB
│   └── webhook/
│       ├── signer.py              # HMAC signing (sort_keys=True)
│       └── dispatcher.py          # HTTP POST to order-listener

requirements.txt:
  fastapi
  uvicorn
  asyncpg
  langgraph>=0.2.0
  langchain-google-genai
  google-generativeai
  ccxt
  pandas
  pandas-ta
  pydantic>=2.0
  httpx
  python-dotenv
  yfinance
```

---

## 12. strategies Table Additions

Two new columns needed on the existing `strategies` table (migration):

```sql
ALTER TABLE strategies 
  ADD COLUMN IF NOT EXISTS strategy_source VARCHAR(20) NOT NULL DEFAULT 'tradingview'
    CHECK (strategy_source IN ('tradingview', 'ai_engine', 'manual'));

ALTER TABLE strategies
  ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
```

`strategy_source = 'ai_engine'` is how the dashboard distinguishes AI strategies from TV strategies for rendering the correct card variant.

---

## 13. signal_log Table Additions

Two columns to extend the existing `signal_log` table (migration):

```sql
ALTER TABLE signal_log
  ADD COLUMN IF NOT EXISTS ai_reasoning TEXT,
  ADD COLUMN IF NOT EXISTS ai_confidence NUMERIC(4,3);
```

When `signal_source = 'ai_engine'`, the order-listener webhook handler populates these from `signal_metadata.reasoning` and `signal_metadata.confidence` in the incoming payload.

This requires a **small addition to `webhook_handler.py`** — the only file in the existing services that needs touching:

```python
# In _finalize_signal_log, extract ai fields from signal_metadata if present
ai_reasoning   = body_dict.get("signal_metadata", {}).get("reasoning")
ai_confidence  = body_dict.get("signal_metadata", {}).get("confidence")
# Write to signal_log.ai_reasoning, signal_log.ai_confidence
```

---

## 14. Implementation Order for Claude Code

Work through in this sequence. Each session is independently verifiable.

**Session 1 — Database migrations**
- Create `db/migrations/004_ai_signal_generator.sql`
- Run migration, verify tables created
- Insert the 5 prompt templates
- Verification: `SELECT id, name FROM ai_prompt_templates;`

**Session 2 — New service skeleton**
- Create `ai-signal-generator/` directory structure
- `Dockerfile`, `requirements.txt`, `app/main.py` (health endpoint only)
- Add service to `docker-compose.yml`
- Verification: `curl http://localhost:8005/health` returns 200

**Session 3 — Data ingestion (Node 1)**
- Implement `data/ohlcv.py`, `data/indicators.py`, `data/sentiment.py`, `data/news.py`
- Unit test each fetcher independently
- Verification: run each fetcher in isolation, confirm output schema

**Session 4 — Prompt builder**
- Implement `prompt/builder.py` assembling full context string
- Implement `prompt/templates.py` loading from DB
- Verification: `GET /api/strategies/:id/ai-config/preview-prompt` returns assembled prompt

**Session 5 — LangGraph graph (Nodes 1-4)**
- Implement `graph/state.py`, `graph/graph.py`, all four nodes
- Implement `webhook/signer.py` and `webhook/dispatcher.py`
- Test with `dry_run=True` end-to-end
- Verification: trigger cycle manually, confirm `ai_signal_log` row written, no webhook fired

**Session 6 — Scheduler + monitors**
- Implement `scheduler.py`, `price_monitor.py`, `event_watcher.py`
- Verification: confirm interval switches when position opens/closes

**Session 7 — API endpoints**
- Add all endpoints from §8 to `dashboard-api`
- Verification: curl each endpoint, confirm CRUD operations on config tables

**Session 8 — UI components**
- Add AI toggle to Add Strategy modal
- Add AI badge to strategy cards
- Add AI Signals tab to strategy detail page
- Add Risk Config edit section
- Verification: visual check against spec §9

**Session 9 — signal_log extension + webhook_handler patch**
- Migration adding `ai_reasoning` and `ai_confidence` columns
- Webhook handler patch to populate them when `signal_source = 'ai_engine'`
- Verification: fire a dry-run signal, confirm columns populated in signal_log

**Session 10 — End-to-end dry-run validation**
- Run full cycle with `dry_run=True` for BTC-USDT AI strategy
- Confirm `ai_signal_log` row written with reasoning
- Confirm no order created in `orders` table
- Confirm signal appears in dashboard AI Signals tab
- Verification: complete curl + DB query checklist

---

## 15. Verification Checklist (Final)

Before any `dry_run=False` operation:

```
Database
[ ] ai_strategy_config table exists with all columns
[ ] ai_risk_config table exists with all columns
[ ] ai_risk_config_audit table exists
[ ] ai_signal_log table exists with all columns
[ ] ai_prompt_templates has 5 rows
[ ] signal_log has ai_reasoning and ai_confidence columns
[ ] strategies table has strategy_source column

Service
[ ] ai-signal-generator container starts and /health returns 200
[ ] LangGraph state machine runs without errors in dry_run mode
[ ] All 4 data source fetchers return valid data
[ ] Prompt assembles correctly and is readable
[ ] HMAC signing produces consistent signatures (sort_keys=True verified)
[ ] Webhook payload passes WebhookPayload Pydantic validation

Guards
[ ] Confidence below threshold → gate rejected, no webhook
[ ] Position already open + open signal → gate rejected
[ ] Daily loss cap exceeded → gate rejected
[ ] Max position size exceeded → gate rejected
[ ] Cooldown active → gate rejected

Dry Run
[ ] dry_run=True → ai_signal_log written, orders table unchanged
[ ] dry_run=False → webhook fired to order-listener, order created

Adaptive Scheduling
[ ] No position → 4H interval confirmed
[ ] Position open → 15M interval confirmed
[ ] Position at risk → 5M interval confirmed
[ ] Event trigger → immediate cycle confirmed

UI
[ ] AI strategy add modal renders correctly
[ ] AI badge appears on strategy cards
[ ] AI Signals tab shows log entries with reasoning
[ ] Risk config edit saves with audit log entry
[ ] Floor enforcement rejects invalid values
[ ] Dry Run badge visible on strategy card
```

---

## 16. Key Decisions Locked In

| Decision | Rationale |
|---|---|
| LangGraph (not OpenAgents/CrewAI) | Sequential pipeline, PostgreSQL checkpointing, deterministic node boundaries |
| Gemini Flash for entry hunting, Pro for position management | Cost optimisation without sacrificing quality on high-stakes decisions |
| temperature=0.1 (not 0.0) | Allows nuanced reasoning while remaining near-deterministic |
| sort_keys=True in HMAC signing | Prevents intermittent signature failures from dict ordering |
| dry_run=True as default | Forces shadow period before live capital exposure |
| size_pct resolved in Python (not LLM) | LLM never sees or derives actual USDT/BTC quantities |
| Risk limits in DB + floors in API | Configurable without code deploy; floors prevent UI misconfiguration |
| strategy_source column | Distinguishes AI vs TV strategies in dashboard without schema conflict |
| Emergency exit in Python (not LLM) | Millisecond-grade protection independent of LLM cycle latency |
| original_reasoning stored and re-injected | Enables thesis consistency evaluation across cycles |
```
