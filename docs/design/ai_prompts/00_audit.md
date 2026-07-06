# Phase 0 Audit — AI Strategy Prompts: Live Templates, Delivered Fields, Desired Fields

> **Status: design artifact.** This audit is the ground truth for the target-state prompt
> drafts (Phase 1) and plumbing specs (Phase 2) in this directory. Nothing in this document
> changes the running system. The live DB, `app/prompt/builder.py`, `app/graph/**`,
> `app/data/**`, schema, and migrations are all unchanged.

Audit date: 2026-07-06. Source of truth for templates is the **live DB**
(`ai_prompt_templates`), not the migrations. Source of truth for delivered fields is
`ai-signal-generator/app/prompt/builder.py` as deployed in this working tree.

---

## 1. Live template inventory (verbatim from the DB)

Command:

```
docker compose exec postgres psql -U matp -d matp -At -c "SELECT id || E'\n----\n' || system_prompt || E'\n====\n' FROM ai_prompt_templates ORDER BY id;"
```

Verbatim output:

```
breakout
----
You are a quantitative crypto analyst specializing in breakout strategies on perpetual futures.
Your primary signals are price breaking above/below consolidation zones with volume confirmation (>150% average).
You look for compression patterns (BB squeeze, low ATR) followed by expansion.
You require the breakout candle to close convincingly beyond the level. False breakouts without volume are HOLD.
====

conservative
----
You are a conservative quantitative crypto analyst specializing in low-frequency, high-conviction setups on perpetual futures.
You require confluence of at least 4 independent signals before recommending a trade.
You express confidence above 0.85 only when the setup is exceptional. You default to HOLD when uncertain.
You give significant weight to macro conditions and sentiment data. Capital preservation always overrides opportunity.
====

geometric_range
----
You are a quantitative crypto analyst specializing in geometry-driven range and breakout strategies on perpetual futures. You work the range with RESTING LIMIT ORDERS rather than market-fading it — each cycle you review the GEOMETRIC PATTERN and OPEN ORDERS sections and choose exactly ONE action: place a resting limit, amend a resting limit, cancel a resting limit, market-trade a confirmed breakout, or hold.

PHASE 1 — PATTERN VALIDITY:
The GEOMETRIC PATTERN section describes the detected price structure. Before acting on it:
- Only place a new resting order for patterns with fit_quality = "strong". A "weak" fit indicates low trendline R² — the structure is unreliable; output HOLD (existing resting orders may still be managed per Phase 4/5 below).
- Require at least 2 touches on each boundary (upper_touches ≥ 2 AND lower_touches ≥ 2) before placing a new order on that boundary.
- Use position_in_range_pct to gauge where price currently sits: 0 = at the lower boundary, 100 = at the upper boundary.

PHASE 2 — WORKING THE RANGE WITH RESTING LIMITS (channels):
For horizontal, ascending, and descending channels with parallel boundaries, check the OPEN ORDERS section first:
- If no resting BUY order exists and the pattern passes Phase 1 checks: output place_limit_long with limit_price set to the lower boundary. Derive stop_loss_pct/take_profit_pct so the stop sits just below the lower boundary (0.5–1x ATR beyond it) and the target sits at the upper boundary (or the midpoint for a smaller target).
- If no resting SELL order exists and the pattern passes Phase 1 checks: output place_limit_short with limit_price set to the upper boundary, stop just above it, target the lower boundary or midpoint.
- If a resting order already exists on a side, do NOT place a duplicate on that side — either hold, or move to Phase 3 if the boundary has moved.
- Never place a limit in the middle of the range (position_in_range_pct 20–80) — the edge is only at the boundaries.

PHASE 3 — RE-FIT: AMEND A STALE BOUNDARY ORDER:
If the OPEN ORDERS section shows a resting order whose price no longer matches the current upper_boundary/lower_boundary (the trendline has re-fit as new bars close), output amend_order with target_order_id set to that order's order_id and limit_price set to the new boundary price. Do not cancel-and-replace with place_limit_* for a re-fit — amend the existing order instead.

PHASE 4 — CONVERGING SHAPES (triangles and wedges):
Ascending triangle, descending triangle, rising wedge, and falling wedge have boundaries that converge. Apply Phase 2/3 rules with these modifications:
- Reduce target distance as pattern_age_bars grows: a pattern that has been forming for 80+ bars is near resolution — tighten targets/stops on new placements.
- Ascending triangle bias: bullish breakout favoured. Prefer place_limit_long at the lower boundary; skip or cancel_order any resting short at the upper boundary instead of amending it.
- Descending triangle bias: mirror of above — prefer place_limit_short at the upper boundary, skip or cancel_order any resting long at the lower boundary.
- Rising wedge: bias is DOWNSIDE on resolution — treat resting longs at the lower boundary cautiously and prioritize Phase 5 breakout handling as pattern_age_bars grows.
- Falling wedge: bias is UPSIDE on resolution — same caution applied to resting shorts at the upper boundary.
- If the pattern has converged to its apex (upper_boundary and lower_boundary within roughly 1x ATR of each other) or fit_quality has degraded to "weak": cancel_order any resting boundary order(s) still open — the structure is no longer tradeable, never leave a fade resting into an apex.

PHASE 5 — BREAKOUT OVERRIDE (overrides Phases 2-4 entirely):
A confirmed breakout occurs when: a candle closes beyond a boundary by more than 0.5x ATR(14) with volume above average, OR two consecutive closes beyond the boundary.
- On any confirmed breakout: cancel_order the resting boundary order on the side being broken through — a fade resting into a confirmed break will get run over. This takes priority over placing or amending anything else this cycle.
- Once the stale resting order is cleared (confirm via OPEN ORDERS in a later cycle), you may market-trade the breakout direction: open_long on a confirmed upside break, open_short on a confirmed downside break, stop beyond the broken boundary (now acting as support/resistance).
- If holding a position fading the broken boundary: close it immediately (close_long/close_short) — do not average down, do not wait for the stop.
- A single-candle wick beyond the boundary without volume confirmation is a false break — maintain the range read, do not cancel resting orders for it.

CONFIDENCE CALIBRATION:
- fit_quality = "strong", upper_touches ≥ 3, lower_touches ≥ 3: may reach 0.85.
- fit_quality = "strong", exactly 2 touches on either side: cap confidence at 0.75.
- pattern_age_bars > 80 in a converging shape (apex is near): reduce confidence by 0.05 — the pattern is unstable.
- Extreme funding rates, major scheduled news, or a VWAP far outside the pattern boundaries all reduce confidence further.
- If fit_quality = "weak" or touch counts fail Phase 1: output HOLD for new placements regardless of other signals (order management in Phases 3-5 still applies to existing resting orders).
====

mean_reversion
----
You are a quantitative crypto analyst specializing in mean-reversion strategies on perpetual futures.
Your primary signals are RSI extremes (oversold <30, overbought >70), Bollinger Band squeezes, and VWAP deviation.
You trade against extended moves, expecting price to return toward the mean.
You require confirmation that momentum is slowing before recommending entry. You use tight stop losses.
====

range_rotation
----
You are a quantitative crypto analyst specializing in range-trading strategies on perpetual futures.

PHASE 1 — RANGE IDENTIFICATION:
A valid range requires: at least 2 touches of support and 2 touches of resistance, flat EMA 50 (no sustained slope), RSI oscillating between roughly 35-65 without pinning at extremes, and price contained within the Bollinger Bands. If no valid range exists, output HOLD.

PHASE 2 — TRADING THE RANGE:
Open SHORT near resistance when: price is within 1.5% of the range high, RSI > 60 and rolling over, and volume is declining on the approach (no breakout pressure).
Open LONG near support when: price is within 1.5% of the range low, RSI < 40 and curling up, and volume is declining on the approach.
Stop loss goes just beyond the range boundary (0.5-1.0% past it). Take profit targets the opposite side of the range or the midpoint (VWAP) for partial exits.
NEVER enter in the middle of the range — the edge is only at the boundaries.

PHASE 3 — BREAK DETECTION (overrides everything):
The range is considered BROKEN when: a candle closes beyond the boundary by more than 0.5x ATR(14) with volume above 150% of average, OR two consecutive closes beyond the boundary.
If holding a position when the range breaks AGAINST you: output close_long or close_short immediately. Do not average down. Do not wait for the stop.
If flat when a confirmed break occurs: you may output a trade in the DIRECTION of the break (open_long on upside break, open_short on downside break), but only with volume confirmation and a retest holding the broken level as new support/resistance. A break without retest or volume is a trap — output HOLD.

RISK POSTURE:
Range trades are mean-probability, small-edge trades: confidence should rarely exceed 0.80 inside the range. Break-and-retest trades may score higher. Funding rate extremes or major scheduled news invalidate the range thesis — output HOLD.
====

scalper
----
You are a quantitative crypto analyst specializing in scalping strategies on perpetual futures.
You trade on short timeframes (15m-1H). Your primary signals are VWAP positioning, order flow imbalance, and momentum bursts.
You use very tight stop losses (0.3-0.8%). You close positions quickly — target hold time under 2 hours.
You avoid entering during low-volume periods or major news events.
====

trend_following
----
You are a quantitative crypto analyst specializing in trend-following strategies on perpetual futures.
Your primary signals are EMA crossovers (50/200), MACD histogram direction, and volume confirmation.
You prefer high-confidence setups with clear directional bias. You avoid counter-trend trades.
In ranging markets, output HOLD. Only recommend a trade when multiple indicators align.
====
```

Seven live templates: `breakout`, `conservative`, `geometric_range`, `mean_reversion`,
`range_rotation`, `scalper`, `trend_following`. Two of them (`geometric_range`,
`range_rotation`) meet the migration-036 quality bar (data-quality gate → phased entry/exit
logic → calibration); the other five are 4-line persona stubs.

---

## 2. Delivered-field inventory (TODAY) — the `[DELIVERED]` whitelist

Authoritative source: `ai-signal-generator/app/prompt/builder.py` (`build_prompt()` section
ordering + each `_render_*`). Data reaches state via
`app/graph/nodes/node_ingest.py`, which calls the `app/data/*` fetchers under the same
`strategy_config` toggles listed below. Section order in the assembled prompt:

Header → Technical → Geometry → Open Orders → Sentiment → News → Macro → Portfolio →
Data Warnings → Strategy Instructions (the DB template + `custom_instructions`) → Task.

### 2.1 Header — `_render_header` (always included, no toggle)

| Field label in prompt | State source |
|---|---|
| `MATP AI ANALYSIS — {base}-{quote} — {interval}` | `strategy_config.base_asset/quote_asset`, `cycle_interval` |
| `Generated: … UTC` | wall clock |
| `Analysis Trigger:` | `trigger_reason` |
| `ACTIVE POSITION — EXIT EVALUATION MODE` block: `Direction:`, `Entry Price:`, `Current P&L:`, `Time Open:`, `Original Thesis:` | `position_open`, `position_side`, `position_entry_price`, `position_unrealized_pnl_pct`, `position_opened_at`, `original_reasoning` |

### 2.2 `TECHNICAL INDICATORS ({interval} timeframe):` — `_render_technical`

Gate: `use_technical` AND `ohlcv_data` present. Individual indicator lines additionally
depend on the `indicators` array in `ai_strategy_config` (default
`['RSI','MACD','EMA50','EMA200','BB','VWAP']`) — `compute_indicators` only emits keys for
enabled indicators. All computed from **closed candles only**.

| Field label in prompt | State key (`technical_indicators` / `ohlcv_data`) |
|---|---|
| `Current Price:` | `ohlcv_data.current_price` |
| `24h Change:` / `7d Change:` | `ohlcv_data.price_change_24h_pct` / `price_change_7d_pct` |
| `RSI(14): {value} — {interpretation}` | `rsi_14`, `rsi_interpretation` |
| `MACD: hist {v}, signal cross {n} bars ago` | `macd_hist`, `macd_signal_bars` |
| `EMA 50/200: {cross_status} (EMA50=… / EMA200=…)` | `ema_cross_status`, `ema_50`, `ema_200` |
| `BB: {interpretation}` | `bb_interpretation` |
| `VWAP: price {pct}% {above/below} VWAP` | `vwap_deviation_pct`, `vwap_direction` |
| `ATR(14): {value} ({pct}% of price)` | `atr_14`, `atr_pct_of_price` |
| `Volume (vs 20MA): {pct}% above/below average` | `volume_vs_avg_pct` |
| `Key Levels: Nearest Support / Nearest Resistance` | `support_1`, `resistance_1` |

### 2.3 `GEOMETRIC PATTERN:` — `_render_geometry`

Gate: `use_geometry` AND `geometry_data` truthy. Produced by
`app/data/geometry.py::detect_geometry` on closed candles.

| Field label in prompt | State key (`geometry_data`) |
|---|---|
| `Detected Shape:` (named pattern, or "Unclassified Structure…" / "No Reliable Pattern…" for `no_pattern`) | `shape`, `fit_quality` |
| `Fit Quality:` | `fit_quality` (`strong`/`weak`) |
| `Upper Boundary:` / `Lower Boundary:` | `upper_boundary`, `lower_boundary` |
| `Upper Touches:` / `Lower Touches:` | `upper_touches`, `lower_touches` |
| `Position in Range: {pct}%` (flagged UNRELIABLE when fit is not strong) | `position_in_range_pct` |
| `Pattern Age: {n} bars` | `pattern_age_bars` |
| `Convergence Rate:` / `Divergence Rate:` | `convergence_pct_per_bar` |

### 2.4 `OPEN ORDERS (this strategy's resting limit orders):` — `_render_open_orders`

Gate: `use_geometry` AND `open_orders is not None`. Fetched from the **listener**
(`GET {listener}/strategies/{id}/orders`) — never the executor, per the exchange-isolation
rule.

| Field label in prompt | Source |
|---|---|
| per order: `order_id=… side=… price=… size=… status=…` | listener `/orders` response |
| usage instruction line (`target_order_id` for cancel/amend, no duplicate sides) | static |

### 2.5 `SENTIMENT:` — `_render_sentiment`

Section renders if at least one of the three toggles is on; each line has its own toggle.

| Field label in prompt | Toggle | Fetcher |
|---|---|---|
| `Fear & Greed Index: {value} ({label})` | `use_fear_greed` | `fetch_fear_greed` (alternative.me) |
| `Funding Rate: {rate}% ({interpretation})` | `use_funding_rate` | `fetch_funding_rate` (ccxt, exchange public) |
| `Open Interest: ${B}B ({pct}% 24h)` + `Long/Short Ratio: {ratio} ({interpretation})` | `use_open_interest` | `fetch_open_interest` (ccxt, exchange public) |

### 2.6 `NEWS DIGEST (last {n} hours):` — `_render_news`

Gate: `use_news` AND `news_data` present.

| Field label in prompt | Source |
|---|---|
| up to 10 × `[{SEVERITY}] {headline}` | `fetch_news_digest(lookback_hours=24)` |

Note: this is **past** news only. No forward-looking scheduled-event calendar is delivered
(see §3 — several live templates already tell the model to react to "major scheduled news"
it cannot see).

### 2.7 Macro — `_render_macro`

Section renders if `use_btc_dominance` OR `use_macro`; lines individually gated. (No
section header line of its own.)

| Field label in prompt | Toggle |
|---|---|
| `BTC Dominance: {pct}% ({trend})` | `use_btc_dominance` |
| `DXY: {value} ({trend})` / `US10Y: {pct}% ({trend})` | `use_macro` |

### 2.8 `PORTFOLIO CONTEXT:` — `_render_portfolio` (always included)

| Field label in prompt | Note |
|---|---|
| `Account Balance: (resolved at execution time)` | static placeholder — no real balance/allocation is delivered |
| `Last Signal: N/A` (only when flat) | static placeholder |

### 2.9 `DATA WARNINGS:` (conditional) and `YOUR TASK:` + `CONFIDENCE SCALE:` (always)

`data_fetch_errors` from ingest; `_render_task` appends the action instruction set and the
canonical confidence scale (0.50–0.95). **Phase-1 drafts must not restate either.**

### 2.10 The emit contract — `LLMSignalOutput` (`node_analyze.py`)

Action Literal set (exact, exhaustive):
`open_long`, `open_short`, `close_long`, `close_short`, `hold`, `partial_close`,
`adjust_stops`, `place_limit_long`, `place_limit_short`, `cancel_order`, `amend_order`.

Required fields: `confidence`, `size_pct`, `stop_loss_pct`, `take_profit_pct`, `reasoning`;
optional: `new_sl_price`, `new_tp_price`, `limit_price`, `target_order_id`.

### 2.11 Toggle inventory (live `ai_strategy_config` columns)

Verbatim (data-source toggles relevant to prompt content, from
`information_schema.columns` on the live DB):

```
use_technical : boolean
use_fear_greed : boolean
use_funding_rate : boolean
use_open_interest : boolean
use_news : boolean
use_economic_calendar : boolean
use_btc_dominance : boolean
use_macro : boolean
use_geometry : boolean
```

**Finding — dead toggle:** `use_economic_calendar` exists in the schema but is referenced
nowhere in the service:

```
$ grep -rn "economic" ai-signal-generator/app/ --include='*.py' | grep -v __pycache__
$ echo "exit: $?"
exit: 1
```

No fetcher, no ingest call, no render section. The Phase-2 `economic_calendar` plumbing
spec can reuse this existing column instead of adding a new one (migration need drops to
zero for the toggle itself).

---

## 3. Desired-field inventory (IDEAL) — the `[REQUIRES PLUMBING]` set

Candidates drawn from `docs/ROADMAP.md` plus judgment. Honesty notes: (a) the ROADMAP does
not literally list "order-book/HVN" or "CVD" — those are judgment-derived from what the
live template texts already *assume* (see per-template gaps below); (b) ROADMAP Open
Question #4 ("Separate OHLCV Timeframe from Analysis Interval") is the roadmap anchor for
the multi-timeframe field; (c) several live templates already instruct the model to use
data that is never delivered — those are the highest-priority fields because today they
invite fabrication:

- `scalper` cites **"order flow imbalance"** — nothing order-flow-shaped is delivered.
- `scalper`, `range_rotation`, `geometric_range` cite **"major scheduled news" / "major
  news events"** — only a *past* news digest is delivered; no forward calendar.
- `breakout` cites **"BB squeeze, low ATR"** compression — a `bb_interpretation` string and
  raw ATR are delivered, but no percentile/regime context that makes "low" decidable.

### 3.1 Field-id catalog (each becomes a Phase-2 spec entry)

| Field id | What the LLM would see | Data source class | Roadmap anchor / origin |
|---|---|---|---|
| `mtf_structure` | Per-timeframe structure array (e.g. 1h/4h/1d): trend direction, EMA-50/200 posture, last swing structure (HH/HL vs LH/LL) | Extra `fetch_ohlcv` calls at fixed timeframes (ccxt public, same path as today) | ROADMAP Open Question #4 |
| `orderbook_depth` | Bid/ask depth within ±1% and ±2% of mid, imbalance ratio, largest resting walls with prices | ccxt public order-book snapshot (same public-data path as `sentiment.py`) | Judgment; backs scalper's "order flow imbalance" |
| `volume_profile_hvn_lvn` | POC, top HVNs and LVNs with prices, value-area high/low computed over the lookback window | Local computation on already-fetched OHLCV (no new external call) | Judgment; gives range/geometry targets real magnets |
| `cvd_delta` | Cumulative volume delta over recent windows + explicit price/CVD divergence flag | ccxt public trades (aggregated buy/sell volume) | Judgment; effort-heavy, flagged as such |
| `momentum_divergence` | Explicit price-vs-RSI and price-vs-MACD divergence flags (bullish/bearish/none, bars since) | Local computation on existing closed candles — cheap | Judgment; backs mean_reversion's "momentum is slowing" |
| `volatility_regime` | ATR percentile vs lookback, BB-width percentile, squeeze flag | Local computation on existing closed candles — cheap | Judgment; makes breakout's "low ATR / BB squeeze" decidable |
| `funding_history` | Funding-rate percentile vs trailing window + streak direction (vs today's single snapshot) | ccxt public funding history (same path as `fetch_funding_rate`) | Judgment; "funding rate extremes" already cited in 2 templates |
| `economic_calendar` | Next scheduled high-impact macro events (FOMC/CPI/NFP…) with time-until | External calendar API; **toggle column already exists, unwired** (§2.11) | Backs "major scheduled news" already cited in 3 templates |
| `liquidation_data` | Recent liquidation volume/clusters near price | External API or exchange feed; lower priority | Judgment; `trigger_liquidation` toggle exists as a trigger concept |

### 3.2 Per-template desired fields

| Template | `[REQUIRES PLUMBING]` fields that would materially improve it |
|---|---|
| `trend_following` | `mtf_structure` (trade with the higher-TF trend, the whole point of the strategy), `momentum_divergence` (early exhaustion warning), `cvd_delta` (participation confirmation), `volatility_regime` (distinguish trend from chop) |
| `mean_reversion` | `momentum_divergence` (currently told to "require confirmation that momentum is slowing" with no divergence data), `funding_history` (crowding fade), `volume_profile_hvn_lvn` (mean/POC as the actual reversion target), `volatility_regime` (band-width context) |
| `breakout` | `volatility_regime` (squeeze detection it already assumes), `volume_profile_hvn_lvn` (consolidation zone edges + post-break air pockets), `orderbook_depth` (wall absorption at the level), `cvd_delta` (real vs fake break), `mtf_structure` (break direction alignment) |
| `scalper` | `orderbook_depth` (the "order flow imbalance" it already cites), `cvd_delta` (momentum bursts), `economic_calendar` (the "major news events" it is told to avoid), `liquidation_data` (burst context), `funding_history` |
| `conservative` | `mtf_structure` (confluence across timeframes = independent signals), `economic_calendar` (macro weighting), `funding_history`, `momentum_divergence` |
| `range_rotation` | `volume_profile_hvn_lvn` (HVN-backed boundaries), `orderbook_depth` (approach pressure — "volume declining on the approach" made observable), `economic_calendar` (the "major scheduled news" it already cites), `funding_history` (the "funding rate extremes" it already cites) |
| `geometric_range` | `volume_profile_hvn_lvn` (boundary/HVN confluence for resting placements), `orderbook_depth` (don't rest a limit into a wall/void), `economic_calendar` (already cites "major scheduled news"), `cvd_delta` (Phase-5 breakout confirmation), `mtf_structure` (pattern vs higher-TF trend) |

### 3.3 Delivered-but-placeholder

`PORTFOLIO CONTEXT` renders a static `Account Balance: (resolved at execution time)` line.
A real `allocation_context` field (live `capital_allocation`, drawdown headroom vs
`allocation_peak`) is a candidate, but it interacts with ROADMAP Open Questions #1/#2
(capital allocation, risk-unit sizing) which are **decision pending** — Phase 1 will tag
any use of it `[REQUIRES PLUMBING: allocation_context]` and Phase 2 will note the open
design dependency rather than pre-empt those decisions.

---

## 4. Constraints carried into Phase 1/2

- Emit-action guidance in drafted prompts stays inside the `LLMSignalOutput` Literal set
  (§2.10) exactly.
- Drafts must not restate the confidence scale or the task instruction — `_render_task`
  appends both (§2.9). Only strategy-specific calibration nuance is allowed.
- Every data reference in a draft carries `[DELIVERED]` (must appear in §2) or
  `[REQUIRES PLUMBING: <field-id>]` (must appear in §3.1).
- New exchange-sourced data follows the existing boundary: public market data via the
  `app/data/*` ccxt/public-API pattern; anything involving the strategy's own
  orders/positions via the **listener**, never the executor directly.
- Structure per migration 036's bar: data-quality gate first, then entry/exit logic, then
  calibration. Provider-agnostic phrasing.
- Next free migration number at audit time: `ls db/migrations` shows `044_*.sql` as the
  latest → next is **045** (re-confirm at Phase-2 writing time).
