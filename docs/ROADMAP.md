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

### 3. Multi-Strategy / Same-Symbol Coordination
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

---

## Deferred Backlog
- **Startup catch-up**: on service restart, check `ai_signal_log` for last cycle time; if interval has elapsed, run a cycle immediately rather than waiting a full new interval.
- **Minimum order value guard**: before sending to exchange, check notional value (qty × price) against known exchange minimums. Reject with `size_too_small` before hitting the exchange API.
