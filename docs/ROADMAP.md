# MATP Roadmap & Design Decisions

## Architecture Rules
- All exchange calls (authenticated or public) go through adapters in `order-executor`. Dashboard-api, UI, and listener are exchange-agnostic.
- AI strategies run against a **demo account** (not dry-run). Demo is preferred — gives real order feedback, fill prices, SL/TP validation, and margin mechanics.

---

## Open Design Questions

### 1. Capital Allocation per Strategy
**Decision pending.** Each strategy should have a dedicated USDC allocation it sizes against exclusively, rather than drawing from shared available balance.

- Proposed field: `capital_allocation_usdc` on the `strategies` table (shared by AI and TradingView strategies).
- Sizing should use `allocated_capital` as the base, not `available_balance` (current) or `total_balance`.
- Hard cap: total open notional for a strategy must not exceed its allocation.

**Why not `available_balance`:** available balance fluctuates with other strategies' open positions, making `size_pct` inconsistent across cycles.

### 2. Position Sizing Method
**Decision pending.** Current method: LLM picks `size_pct`, capped by `max_position_size_pct`. No relationship to SL distance.

Preferred direction: **risk-unit sizing** — size the position so that a full SL hit costs a fixed dollar amount (e.g. $15/trade). Formula:
```
qty = risk_per_trade / (entry_price × stop_loss_pct / 100)
```
LLM still outputs `stop_loss_pct`; the guard derives qty from it. `max_position_size_pct` remains as a hard ceiling.

### 4. Separate OHLCV Timeframe from Analysis Interval
**Decision pending.** Currently `cycle_interval` (scheduler sleep time) doubles as the OHLCV candle timeframe passed to `fetch_ohlcv`. This means the LLM sees different candle resolutions depending on strategy state (no position → 4h candles, position open → 15m candles), which is an inconsistent market view.

Proposed: add `ohlcv_timeframe` column to `ai_strategy_config` (fixed per strategy, standard exchange intervals only: 1m/3m/5m/15m/30m/1h/2h/4h/6h/8h/12h/1d). `node_ingest.py` reads this instead of `cycle_interval`. The three analysis intervals become pure scheduler sleep timers with no candle-timeframe side effect.

Changes required: DB migration, one line in `node_ingest.py`, `ai.ts` GET/PUT, UI Add/Edit modals, optional prompt builder update.

### 5. Multi-Strategy / Same-Symbol Coordination
**Deferred — gather data first.** Up to 3 strategies may run concurrently on the same symbol (AI scalper, AI swing, TradingView).

- Risk: unintended stacked exposure or self-hedging.
- Approach when ready: per-symbol net exposure cap in the executor. First strategy in gets full size; subsequent ones are reduced or blocked if cap is reached.
- Coordination must live in the executor, not the AI layer, because TradingView strategies bypass the AI gate.

---

## Known Issues Fixed
| Date | Issue | Fix |
|------|-------|-----|
| 2026-06-10 | OHLCV returning stale price (~2400 instead of ~1600) because `since=90d_ago` + exchange candle cap left last candle 50 days in past | Removed `since` param — exchange now returns most recent N candles |
| 2026-06-10 | `gemini-3-pro-preview` deprecated, returning 404 | Updated eth-range strategy to `gemini-3.1-pro-preview` |
| 2026-06-10 | Config-reload was a no-op; scheduler slept full interval after interval change | Added interruptible sleep + immediate cycle on config reload |
| 2026-06-10 | `volume_vs_avg_pct` always showed -70 to -99% because Binance returns the current incomplete candle as the last OHLCV entry | Fixed by computing volume average and current value from `volume.iloc[:-1]` (completed candles only) |
| 2026-06-10 | On service restart, schedulers slept a full interval before the first cycle, leaving strategies idle for hours | Added immediate startup cycle before the sleep loop in `AdaptiveScheduler._loop()` |

---

## Deferred Backlog
- **Minimum order value guard**: before sending to exchange, check notional value (qty × price) against known exchange minimums. Reject with `size_too_small` before hitting the exchange API.
- **AI prompt template management page**: no runtime CRUD exists for `ai_prompt_templates` — templates are seed-only (migrations 006/010, `ON CONFLICT DO NOTHING`). `GET /api/ai/templates` is read-only; there is no POST/PUT/DELETE anywhere. Build a create/edit page.
  - **Safety model — clone-to-edit, not edit-in-place.** Templates are shared: every `ai_strategy_config.template_id` points at one. Editing a base template in place silently changes behavior for all strategies referencing it (incl. live ones) and breaks backtest/live parity. Seed templates must stay immutable; user clones one into a custom template and edits that.
  - Needs `is_system` (or `created_by`) flag to distinguish/protect seeded rows; CRUD endpoints; a "N strategies use this" warning before destructive actions.
  - `ai_prompt_templates` is a single canonical table in `public` — tester reads it, has no duplicate — so no schema-sync needed.
  - Display side is **already done** (committed): the config modal shows `system_prompt` + active data sources read-only via the `TemplatePreview` component in `Strategies.tsx`.
### Dynamic strategy allocation (realized-PnL-compounding base)

**Status:** Deferred — design intent captured, not yet implemented.

**Intent:** A strategy's allocation is not a static figure. The capital base
that position sizing is computed against compounds with **realized P&L only**:

    current_allocation = initial_allocation + cumulative_realized_pnl

It steps only when positions close (deterministic; it does NOT float with mark
price / unrealized P&L). This `current_allocation` — not the seed figure — is
what feeds `margin_per_trade` and the drawdown math.

**Open questions to resolve before implementing:**

- Two distinct quantities now exist: `initial_allocation` (the seed, static)
  and `current_allocation` (derived). Decide storage: persist only
  `initial_allocation` and compute current on read (single source of truth,
  but requires summing realized P&L over the strategy's position lifetime),
  vs. persist a running `current_allocation` updated on each position close.
- Confirm `cumulative_realized_pnl` is **net of fees** — i.e. the same realized
  figure already shown (with its fees bracket) in the UI feeds allocation.
- `max_drawdown_pct` / Guard 5 interaction: is cumulative drawdown measured
  against `initial_allocation`, against `current_allocation`, or against a
  running peak of `current_allocation`? Compounding the base changes what
  "drawdown" means and must be decided explicitly.
- Floor behaviour: if cumulative realized P&L drives `current_allocation` to or
  below a minimum viable margin, sizing must halt cleanly rather than emit
  zero/negative size.
- UI: the Strategies screen should surface both seed and current (compounded)
  allocation so the compounding is visible. Current mockup shows a single
  "Allocated" figure.
- Strategy-tester parity: the tester's sizing must apply the same compounding
  rule or backtests diverge from live. Ties into the existing open tester-parity
  item for `capital_allocation` / `margin_per_trade`.

**Layer note:** Upstream sizing logic — belongs wherever `capital_allocation` /
`margin_per_trade` are resolved, never in adapters. Adapters keep receiving
canonical units.
