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
| 2026-06-20 | `capital_allocation` was static; drawdown used an anchor-PnL delta model (doubled Guard 5 bug) | Dynamic allocation: `capital_allocation` compounds on close, `initial_allocation` + `allocation_peak` added, Guard 5 replaced with high-water peak model |

---

## Deferred Backlog
- **Minimum order value guard**: before sending to exchange, check notional value (qty × price) against known exchange minimums. Reject with `size_too_small` before hitting the exchange API.
- **AI prompt template management page**: no runtime CRUD exists for `ai_prompt_templates` — templates are seed-only (migrations 006/010, `ON CONFLICT DO NOTHING`). `GET /api/ai/templates` is read-only; there is no POST/PUT/DELETE anywhere. Build a create/edit page.
  - **Safety model — clone-to-edit, not edit-in-place.** Templates are shared: every `ai_strategy_config.template_id` points at one. Editing a base template in place silently changes behavior for all strategies referencing it (incl. live ones) and breaks backtest/live parity. Seed templates must stay immutable; user clones one into a custom template and edits that.
  - Needs `is_system` (or `created_by`) flag to distinguish/protect seeded rows; CRUD endpoints; a "N strategies use this" warning before destructive actions.
  - `ai_prompt_templates` is a single canonical table in `public` — tester reads it, has no duplicate — so no schema-sync needed.
  - Display side is **already done** (committed): the config modal shows `system_prompt` + active data sources read-only via the `TemplatePreview` component in `Strategies.tsx`.
### Dynamic strategy allocation (realized-PnL-compounding base)

**Status:** COMPLETE — implemented 2026-06-20 across 5 phases.

**Summary of what was built:**

- `capital_allocation` is now a **live compounding balance**: `+= realized_pnl`
  on every position close (order-listener, all three close-path UPDATEs).
- `initial_allocation` (new column) = committed capital (seed + net manual
  deposits). Never updated by PnL. Used as the `total_return` denominator.
- `allocation_peak` (new column) = high-water mark of `capital_allocation`.
  Ratchets up on winning closes; shifts by delta on deposit/withdraw; re-anchors
  to `capital_allocation` when a strategy is re-enabled after auto-disable.
- **Guard 5** (order-listener) trips when
  `capital_allocation <= allocation_peak × (1 − max_drawdown_pct/100)`,
  auto-disables the strategy, and returns 429.
- **Deposit/withdraw** via PUT `allocation_delta` (signed). All three allocation
  columns shift by the delta — capital moves are not drawdown events.
- **UI** surfaces "Allocation" (live) and "Committed" (seed) on every card.
  Edit modal shows a deposit/withdraw delta input with live preview.
- `drawdown_anchor_pnl` is fully retired from all logic (column left in schema,
  drop deferred).

**Open tester-parity note:** strategy-tester backtest sizing does not yet apply
the compounding rule — backtests still use the static `capital_allocation` seed.
This divergence is accepted for now; tester parity is a separate backlog item.
