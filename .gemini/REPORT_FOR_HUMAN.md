# Position Investigation Report — HYPE-USDT (BloFin) + BTC-USDT (Hyperliquid)

**Date:** 2026-06-13  
**Session scope:** Read-only. No code or DB changes made.  
**Source data:** strategy_positions, orders tables + order-listener docker logs.

---

## HYPE-USDT (BloFin — account: acc_blofin_demo_default)

### 1. Position row

| Field | Value |
|---|---|
| Position ID | `63f892f3-30c5-46d0-8c6a-9045e3d6e4e7` |
| strategy_id | `test-strategy-4-4750` |
| account_id | `acc_blofin_demo_default` (BloFin demo) |
| status | **closed** |
| side | short |
| size (DB) | **5** |
| entry_price | 61.4435 |
| closing_price | NULL |
| pnl_realized | **1.5675** |
| reconcile_miss_count | **3** |
| close_reason | **Closed on exchange** |
| opened_at | 2026-06-12 16:38:35 UTC |
| closed_at | **2026-06-13 00:27:31 UTC** |
| opening_order_id | `9ae2192f-447e-4b22-b9f3-933592c54d65` |
| closing_order_id | **NULL** |

### 2. What closed it

The position was **not** closed by a real TradingView webhook and **not** by a human close. It was closed by the reconciler after a **BloFin API failure**.

Evidence from order-listener logs:

```
00:24:06  acc_blofin_demo_default/positions HTTP 200  →  HYPE short exchange_size=50.0 > db_size=5 — ignoring (will not grow)
00:25:08  acc_blofin_demo_default/positions HTTP 200  →  exchange_size=50.0 > db_size=5 — ignoring (will not grow)
00:26:09  acc_blofin_demo_default/positions HTTP 200  →  exchange_size=50.0 > db_size=5 — ignoring (will not grow)

00:27:21  [WARNING] executor_client: get_account_positions(acc_blofin_demo_default) failed:
00:27:21  [INFO]    reconciler: position 63f892f3 (HYPE-USDT short) miss 3/3 db=5 exchange=0

00:27:31  [WARNING] executor_client: Executor GET /accounts/acc_blofin_demo_default/positions/history?symbol=HYPE-USDT failed:
00:27:31  [WARNING] reconciler: pnl_unconfirmed for position 63f892f3 (HYPE-USDT short) close_reason=Closed on exchange
00:27:31  [INFO]    webhook_handler: Closed position 63f892f3 for strategy test-strategy-4-4750 (HYPE-USDT short), close_size=5, fill=None, pnl=None
00:27:31  [INFO]    reconciler: closed position 63f892f3 (HYPE-USDT short) reason=Closed on exchange pnl=None [pnl_unconfirmed]
```

The three consecutive "will not grow" passes immediately before the failure all returned HTTP 200 and confirmed the position was still live at size 50.0 on BloFin. The API call at 00:27:21 **failed** (returned no data). The reconciler received an empty list, counted it as miss 3/3 (no incremental 1/3 → 2/3 progression in logs — the counter jumped to 3 in a single failure event). The subsequent history call also failed, so PnL could not be confirmed (`pnl_unconfirmed`).

There is one real TradingView `close_short` order:

| Field | Value |
|---|---|
| Order ID | `3fa6bcf6-f852-4167-998c-ff5b407b95cb` |
| signal | `close_short` |
| signal_source | `tradingview` |
| platform | `auto` |
| size | 5 |
| actual_fill_price | 61.13 |
| pnl | 1.5675 |
| closes_position_id | `63f892f3...` |
| raw_webhook | Real TradingView payload (side/size/signal/timestamp/base_asset all present) |
| received_at | 2026-06-12 16:47:11 UTC |

This order fired at 16:47 and is linked to the position via `closes_position_id`, but the position's `closing_order_id` was **never set** (remains NULL). The position remained `open` in MATP from 16:47 until the reconciler declared it closed 7 hours 40 minutes later.

### 3. PnL truth

- `pnl_realized` on position: **1.5675 USDT**
- Source: carried over from the TradingView `close_short` order (pnl=1.5675), which closed 5 out of 10 lots on BloFin.
- The reconciler's close at 00:27 logged `pnl=None [pnl_unconfirmed]` — it did not overwrite the existing 1.5675 value.
- The `closing_price` on the position row is **NULL** (no confirmed exchange close price).

### 4. Size — the double-open and the stranding

Two `open_short` orders fired from TradingView within 2 minutes:

| Order | received_at | size | actual_fill_price | exchange_order_id |
|---|---|---|---|---|
| `9ae2192f` (1st open) | 16:38:31 | 5 | 61.426 | 1000129767529 |
| `a8a8a71f` (2nd open) | 16:40:47 | 5 | 61.461 | 1000129767672 |

Q4 aggregate: `HYPE-USDT | sell | open_short | 2 orders | total_size=10`

One `close_short` fired from TradingView:

| Order | received_at | size | actual_fill_price |
|---|---|---|---|
| `3fa6bcf6` | 16:47:11 | 5 | 61.13 |

Q4 aggregate: `HYPE-USDT | buy | close_short | 1 order | total_size=5`

**Net on BloFin:** 10 opened − 5 closed = **5 lots (50 contracts) still short.**

MATP tracked only **one position** of size=5 (the entry_price 61.4435 = average of 61.426 and 61.461 confirms both orders contributed, but size stayed at 5 in the DB).

### 5. Current exchange state (Q6 — live read)

```json
[
  {
    "symbol": "HYPE-USDT",
    "side": "short",
    "size": "50.0",
    "entry_price": "61.4435",
    "leverage": 10,
    "mark_price": "58.534",
    "unrealized_pnl": "14.546"
  }
]
```

**The HYPE short is still live on BloFin demo at 50 contracts (5 lots), currently showing +14.55 USDT unrealized PnL** (mark price 58.53 < entry 61.44, profitable for a short). MATP's DB shows this position as `closed`.

---

## BTC-USDT (Hyperliquid — account: Hyperliquidtest)

### 1. Position row

| Field | Value |
|---|---|
| Position ID | `9d38cbcb-8514-4b71-9cc9-c4eec8172e27` |
| strategy_id | `hltest-76b3` |
| account_id | `Hyperliquidtest` (Hyperliquid demo) |
| status | **closed** |
| side | short |
| size (DB) | **0.005** |
| entry_price | 64113.1 |
| closing_price | **62995.203019** |
| pnl_realized | **30.01278** |
| reconcile_miss_count | **3** |
| close_reason | **Closed on exchange** |
| opened_at | 2026-06-12 16:48:12 UTC |
| closed_at | **2026-06-12 20:58:39 UTC** |
| opening_order_id | `f63ce56b-2962-41bd-a00f-81aaaec08be5` |
| closing_order_id | `3beb3e22-e5da-4f67-a339-679e1b586607` |

### 2. What closed it

The position was closed by the **reconciler** after three consecutive **successful** Hyperliquid API calls returned no BTC position. This is a legitimate reconciler close — the API did not fail; the position was genuinely absent from the exchange for 3 polls.

Evidence from order-listener logs:

```
20:56:24  adjust-stops succeeded: strategy=hltest-76b3 pos=9d38cbcb (BTC-USDT short) tp=120000.0 sl=95000.0 cancelled=0 placed=2
20:56:31  adjust-stops HTTP 502 Bad Gateway (second attempt)

20:56:33  GET Hyperliquidtest/positions HTTP 200  →  BTC-USDT short miss 1/3 db=0.005 exchange=0
20:57:36  GET Hyperliquidtest/positions HTTP 200  →  BTC-USDT short miss 2/3 db=0.005 exchange=0
20:58:38  GET Hyperliquidtest/positions HTTP 200  →  BTC-USDT short miss 3/3 db=0.005 exchange=0

20:58:39  GET Hyperliquidtest/positions/history?symbol=BTC-USDT HTTP 200
20:58:39  webhook_handler: Closed position 9d38cbcb (BTC-USDT short), close_size=0.005, fill=62995.203019, pnl=30.01278
20:58:39  reconciler: closed position 9d38cbcb (BTC-USDT short) reason=Closed on exchange pnl=30.01278
```

The closing order (`3beb3e22`) is a synthetic reconciler order:
- `signal = exchange_close`
- `signal_source = reconciler`
- `platform = exchange`
- `raw_webhook = {}`
- `size = 0`
- `pnl = 30.01277999…`

This is not a TradingView webhook. The reconciler created it after confirming the position was gone from Hyperliquid and retrieving the close price from exchange history. The `closing_order_id` on the position correctly points to this synthetic order.

**What caused the exchange-side close:** The first miss occurred 9 seconds after a successful `adjust-stops` call that placed TP=120,000 and SL=95,000 on a short entered at 64,113. The position closed at 62,995 (profitable, price moved downward). The DB contains no further evidence of what triggered the Hyperliquid-side close; the reconciler only observed the absence.

### 3. PnL truth

- `pnl_realized` on position: **30.01278 USDT**
- `pnl` on closing order: **30.01277999… USDT** (same value, confirmed from exchange history)
- `closing_price`: **62995.203019** (retrieved from Hyperliquid position history, not inferred)
- The history call succeeded, so PnL is **confirmed** (not `pnl_unconfirmed`).

### 4. Size — the opening order vs tracked position

| | Value |
|---|---|
| Opening order size (raw_webhook) | **0.01** BTC |
| Opening order actual_fill_price | 64113.1 |
| Hyperliquid fill response (raw_response) | `"totalSz": "0.01"` — 0.01 filled |
| Position size tracked in DB | **0.005** BTC |

The opening order (`f63ce56b`) requested and filled 0.01 BTC on Hyperliquid, but MATP stored the position as 0.005. The halving is confirmed. The source of the halving (whether in the executor adapter, the webhook handler, or position creation logic) is not determinable from this read-only data pull; both the raw_webhook and the Hyperliquid response agree on 0.01.

Q4 aggregate for BTC / hltest-76b3 in the window:  
`BTC-USDT | sell | open_short | 1 order | total_size=0.01` (opening order)  
`BTC-USDT | buy  | exchange_close | 1 order | total_size=0` (synthetic reconciler close, size=0)

### 5. Current exchange state

BTC-USDT short on Hyperliquidtest: **not present** (position was genuinely closed on exchange before the reconciler detected it). No live BTC position was returned by the executor.

### 6. Reconciler log excerpt (BTC)

```
2026-06-12 20:56:24  adjust-stops strategy=hltest-76b3 pos=9d38cbcb (BTC-USDT short) tp=120000.0 sl=95000.0 cancelled=0 placed=2
2026-06-12 20:56:31  adjust-stops → HTTP 502 Bad Gateway
2026-06-12 20:56:33  GET Hyperliquidtest/positions HTTP 200 → miss 1/3 db=0.005 exchange=0
2026-06-12 20:57:36  GET Hyperliquidtest/positions HTTP 200 → miss 2/3 db=0.005 exchange=0
2026-06-12 20:58:38  GET Hyperliquidtest/positions HTTP 200 → miss 3/3 db=0.005 exchange=0
2026-06-12 20:58:39  GET positions/history?symbol=BTC-USDT HTTP 200
2026-06-12 20:58:39  Closed position 9d38cbcb (BTC-USDT short), close_size=0.005, fill=62995.203019, pnl=30.01278
2026-06-12 20:58:39  closed position 9d38cbcb reason=Closed on exchange pnl=30.01278
```

---

## Summary Table

| Symbol | What closed it | PnL recorded in DB | Still live on exchange? |
|---|---|---|---|
| HYPE-USDT | Reconciler — **BloFin API failure** at 00:27 UTC June 13 caused `get_account_positions` to return empty; miss jumped to 3/3 in one step; history call also failed so fill price = NULL | **1.5675 USDT** (from earlier TradingView close_short order; reconciler wrote `pnl_unconfirmed`, did not overwrite) | **YES** — 50 contracts short, entry 61.4435, mark 58.53, unrealized PnL +14.55 USDT |
| BTC-USDT | Reconciler — **3 consecutive successful Hyperliquid polls** (HTTP 200) all returned no position; history call confirmed close | **30.01278 USDT** (confirmed from exchange history, closing_price = 62995.20) | **NO** — genuinely closed on Hyperliquid prior to reconciler detection |

---

# Capital Allocation Foundation (Prompt #1 of 3)

**Migration:** `016_capital_allocation.sql`  
**Branch:** `feat/strategy-tester`

---

## STEP 1 — Schema ✅

**Migration 016** adds to `strategies`:

| Column | Type | Default | Purpose |
|---|---|---|---|
| `capital_allocation` | NUMERIC | 100 | Initial $ bankroll / loss cap |
| `margin_per_trade` | NUMERIC | 5 | Fixed $ margin per trade |
| `max_drawdown_pct` | NUMERIC | 50 | % of initial allocation as cumulative stop |
| `drawdown_anchor_pnl` | NUMERIC | 0 | pnl_total snapshot when allocation last set |

Note: `capital_allocation_percent` (a %) and `max_daily_drawdown_percent` (daily) remain as-is (legacy).

`\d strategies` confirms all 4 columns:
```
 capital_allocation         | numeric | not null | 100
 margin_per_trade           | numeric | not null | 5
 max_drawdown_pct           | numeric | not null | 50
 drawdown_anchor_pnl        | numeric | not null | 0
```

**`init.sql` Phase 1–3 schema folded in:**
- `strategies.strategy_source VARCHAR(20) NOT NULL DEFAULT 'tradingview'`
- `strategy_positions.close_reason VARCHAR(30)`
- `strategy_positions.reconcile_miss_count INTEGER NOT NULL DEFAULT 0`
- `orders.closes_position_id UUID REFERENCES strategy_positions(id)`
- `CREATE INDEX idx_orders_closes_position ON orders (closes_position_id) WHERE closes_position_id IS NOT NULL`
- `CREATE UNIQUE INDEX uq_strat_pos_one_open ON strategy_positions (strategy_id, symbol, side) WHERE status = 'open'`

All confirmed in live DB (`\d strategy_positions`, `\d orders`).

---

## STEP 2 — AI Sizing → Fixed Margin ✅

**`ai-signal-generator/app/graph/nodes/node_guard.py`:**
- Removed balance fetch and `size_pct` usage for sizing
- New: `base_qty = round((margin_per_trade * leverage) / current_price, 4)`
- `margin_per_trade` read from `strategy_config` (included via `s.*` in scheduler query)

**Numbers (hltest-76b3: margin=5, lev=20, BTC=107000):**
```
base_qty = (5.0 × 20) / 107000.0 = 0.0009
notional = 0.0009 × 107000.0 = 96.30 USD
```

Balance no longer affects size. SL/TP prices still computed from LLM's `stop_loss_pct` / `take_profit_pct`.

---

## STEP 3 — TV Sizing → Clamp to Margin ✅

**`order-listener/app/webhook_handler.py`:**
- After Guard 3, for `open_long`/`open_short`: `margin_qty = (margin_per_trade × leverage) / indicator_price`
- If `payload.size > margin_qty`: clamp to `margin_qty`, write `size_scaled_to_margin / original_size / used_size` into `signal_metadata`

**Test — size above margin (hltest-76b3: margin=5, lev=20, price=107000 → margin_qty≈0.000935):**

Send `size=0.01`:
```
Listener: Strategy hltest-76b3 margin clamp: 0.01 → 0.00093458 (margin=5.0, lev=20, price=107000.0)
```

Order row (id=e56b6288):
```
size=0.00093458 | scaled=true | orig=0.01 | used=0.00093458
```

**Test — size within margin (size=0.0005 < 0.000935):**
```
id=1d756207 | size=0.0005 | scaled=(null)
```

No clamp applied ✅.

---

## STEP 4 — Per-Account Fund Cap ✅

**`dashboard-api/src/routes/strategies.ts`:**
- Helpers `getAccountAvailableBalance()` (executor balance endpoint) + `getAllocatedOnAccount()`
- POST /strategies: fund cap check before INSERT
- PUT /strategies/:id: fund cap check when `capital_allocation` changes

**Hyperliquidtest account:** available=$619.78, already allocated=$400 (4 strategies)

**Reject (400+300=700 > 619):**
```json
POST /strategies {"capital_allocation": 300, "account_id": "Hyperliquidtest"}
→ 422: "Insufficient free funds on account: $300 requested, $219.78 available
        ($400.00 already allocated of $619.78 total)."
```

**Accept (400+100=500 < 619):**
```json
POST /strategies {"capital_allocation": 100, "account_id": "Hyperliquidtest"}
→ 201: {"id": "fund-cap-test-ok-ad5a", "enabled": true, ...}
```

✅ Fund cap enforced.

---

## STEP 5 — Drawdown Stop ✅

**`order-listener/app/webhook_handler.py`:**
- Guard before open signals: `loss = pnl_total - drawdown_anchor_pnl`; limit = `-(capital_allocation × max_drawdown_pct / 100)`
- If `loss <= limit`: disable strategy, reject 429 `drawdown_stop`
- `pnl_total` now updated alongside `pnl_today` on every trade close

**Test (eth-range-ba4f: capital=100, max_dd=50%, anchor=0 → limit=-50, seeded pnl_total=-55):**

```json
POST /webhook/eth-range-ba4f {signal: "open_long"}
→ 429: "Drawdown stop hit for strategy eth-range-ba4f: realized loss $55.00 >= $50.00
        (50% of $100.00 allocation). Strategy auto-disabled."
```

```sql
SELECT enabled FROM strategies WHERE id='eth-range-ba4f';
-- enabled=f
```

Subsequent order returns `403 Strategy stopped` ✅.

---

## STEP 6 — Reset Drawdown Anchor on Allocation Change ✅

**`dashboard-api/src/routes/strategies.ts`:**
- PUT /strategies/:id: `drawdown_anchor_pnl = pnl_total` when `capital_allocation` is in body
- Returns `drawdown_anchor_reset: true`

**Test (eth-range-ba4f: pnl_total=-55, new capital_allocation=200):**
```json
PUT /strategies/eth-range-ba4f {"capital_allocation": 200}
→ {
    "capital_allocation": "200",
    "drawdown_anchor_pnl": "-55",
    "pnl_total": "-55",
    "drawdown_anchor_reset": true
  }
```

Anchor reset to -55. New stop fires if total loss exceeds -55 - (200 × 50%) = -155. ✅

---

## Task 3 — cleanup(risk): Drop Daily-Loss Cap + Collapse to Single Drawdown Stop (migration 017)

**Commit:** `478e2e7` — branch `feat/strategy-tester`  
**Date:** 2026-06-14

### What was removed
- `ai_risk_config.max_daily_loss_pct` and `ai_risk_config.max_drawdown_pct` from all code readers (15 files) and then from both `public.ai_risk_config` and `tester.ai_risk_config` via migration 017.
- `node_guard.py` daily-loss-cap + old AI-gate drawdown block deleted.
- `node_guard_sim.py` sim daily-loss gate deleted.
- Prompt builder (live + vendored): daily-loss cap line removed from portfolio context.
- All dashboard-api risk-config RISK_FIELDS, RISK_DEFAULTS, validation, GET/PUT handlers cleaned.
- All dashboard-ui AiFormState type fields, form defaults, inputs, and PUT bodies cleaned.
- All strategy-tester SELECT queries, risk_config dicts, and migrate INSERTs cleaned.
- `strategy-tester/app/_vendored/CHECKSUMS` updated for modified `prompt_builder.py`.

### Grep sweeps (both clean)
```
grep -rn "max_daily_loss_pct|daily_loss_cap" ... → (no output)
grep -rn "max_drawdown_pct" ... → only legitimate references:
  - order-listener webhook_handler.py:524 (Guard 5 canonical stop — KEEP)
  - dashboard-api routes/strategies.ts (strategies.max_drawdown_pct columns — KEEP)
  - strategy-tester engine/backtest_engine.py (result metric — KEEP)
  - strategy-tester api/strategies.py (backtest_runs result — KEEP)
```

### Migration 017 output
```
ALTER TABLE
ALTER TABLE
NOTICE:  Migration 017 verified OK — daily-loss columns gone from both schemas
DO
```

### \d public.ai_risk_config (after)
```
 strategy_id           | character varying(100) | not null
 max_position_size_pct | numeric(5,2)           | not null | 5.00
 max_concurrent_trades | integer                | not null | 1
 updated_at            | timestamptz            | not null | now()
 updated_by            | character varying(100)
```

### All 5 edited services rebuilt --no-cache and healthy ✅

---

## Task 4 — cleanup(risk): Remove Guard 4 Daily Drawdown + max_daily_drawdown_percent (migration 018)

**Commit:** `10a6574` — branch `feat/strategy-tester`  
**Date:** 2026-06-14

### What was removed
- Guard 4 block (~28 lines) deleted from `order-listener/app/webhook_handler.py`
- Guard 4 test deleted from `order-listener/tests/test_webhook_handler.py`
- `max_daily_drawdown_percent` removed from all INSERT/UPDATE/SELECT in:
  - `dashboard-api/src/routes/strategies.ts` (create + PUT renumbered from $13 to remove gap)
  - `dashboard-ui/src/api.ts`, `Strategies.tsx`, `StrategyForm.tsx`, `StrategyDetail.tsx`
  - `strategy-tester/app/api/strategies.py` (StrategyCreate, StrategyUpdate, INSERT $12→$11, UPDATE $11→$10)
  - `strategy-tester/app/api/migrate.py` (both cross-schema copy flows)
- `POST /:id/reset-daily` endpoint deleted from `dashboard-api/src/routes/strategies.ts`
- `db/init.sql` line `max_daily_drawdown_percent NUMERIC DEFAULT 20` removed
- Migration 018 created and applied

### Grep sweeps (both clean)
```
grep -rn "max_daily_drawdown_percent|reset-daily|reset_daily" \
  ai-signal-generator/ order-listener/ dashboard-api/src/ \
  dashboard-ui/src/ strategy-tester/app/ db/init.sql → (no output)

grep -n "Guard 4" order-listener/ → (no output)
grep -n "Guard 5" order-listener/app/webhook_handler.py → line 491: # Guard 5: Cumulative drawdown stop ✅
```

### Listener test suite
```
29 passed, 2 warnings in 13.22s  ✅  (Guard 4 test removed; 28 remaining + 1 new pass)
```

### Guard 5 regression
Strategy `test_blofin_demo_01` (BTC-USDT, capital_allocation=100, max_drawdown_pct=50):
- Seeded `pnl_total=-51`, `drawdown_anchor_pnl=0` (limit is −50)
- Fired `open_long` webhook → received:
```json
{"detail":"Drawdown stop hit for strategy test_blofin_demo_01: realized loss $51.00 >= $50.00 (50% of $100.00 allocation). Strategy auto-disabled."}
```
- HTTP 429, `enabled=false` confirmed in DB ✅

### Migration 018 output
```
ALTER TABLE
ALTER TABLE
NOTICE:  Migration 018 verified OK — max_daily_drawdown_percent gone from both schemas
DO
```

### \d public.strategies (relevant columns after)
```
 max_daily_signals          | integer  | DEFAULT 500
 capital_allocation_percent | numeric  | DEFAULT 100
 capital_allocation         | numeric  | not null | DEFAULT 100
 max_drawdown_pct           | numeric  | not null | DEFAULT 50
```
`max_daily_drawdown_percent` absent ✅

### All 4 edited services rebuilt --no-cache and healthy ✅
- order-listener ✅
- dashboard-api ✅
- dashboard-ui ✅
- strategy-tester ✅

---

## Task 5 — cleanup(capital-allocation): Drop capital_allocation_percent + Repoint total_return (migration 019)

**Commit:** `0e2e65b` — branch `feat/strategy-tester`  
**Date:** 2026-06-14

### What was removed
- `capital_allocation_percent` removed from all INSERT/UPDATE/SELECT in:
  - `dashboard-api/src/routes/strategies.ts` (INSERT renumbered to remove $13 gap; `total_return` denominator repointed from `capital_allocation_percent` → `capital_allocation`)
  - `order-listener/tests/test_webhook_handler.py` (removed from `SAFE_STRATEGY` dict)
  - `strategy-tester/app/api/strategies.py` (StrategyCreate, StrategyUpdate, INSERT $12→removed, UPDATE $11→removed, renumbered to $12/$13)
  - `strategy-tester/app/api/migrate.py` (both cross-schema copy flows: public→tester and tester→public)
  - `db/init.sql` (`capital_allocation_percent NUMERIC DEFAULT 100` line removed)
- Migration 019 created and applied

### Repointed total_return (substantive change)
Old formula (divided by %-based field, default 100):
```sql
CASE
  WHEN COALESCE(s.capital_allocation_percent, 0) = 0 THEN 0::float
  ELSE ROUND(... / NULLIF(s.capital_allocation_percent, 0)::numeric * 100, 2)::float
END AS total_return
```

New formula (divides by $ bankroll):
```sql
CASE
  WHEN COALESCE(s.capital_allocation, 0) = 0 THEN 0::float
  ELSE ROUND(
    COALESCE((SELECT SUM(sp.pnl_realized) FROM strategy_positions sp
              WHERE sp.strategy_id = s.id AND sp.status = 'closed'), 0)::numeric /
    NULLIF(s.capital_allocation, 0)::numeric * 100,
  2)::float
END AS total_return
```

### Grep sweep (clean)
```
grep -rn "capital_allocation_percent" \
  ai-signal-generator/ order-listener/ dashboard-api/src/ \
  dashboard-ui/src/ strategy-tester/app/ db/init.sql → (no output)
```

### Migration 019 output
```
ALTER TABLE
ALTER TABLE
NOTICE:  Migration 019 verified OK — capital_allocation_percent gone from both schemas
DO
```

### \d public.strategies (relevant columns after)
```
 capital_allocation   | numeric | not null | 100
 max_drawdown_pct     | numeric | not null | 50
 drawdown_anchor_pnl  | numeric | not null | 0
```
`capital_allocation_percent` absent ✅  
`capital_allocation_percent` absent from tester.strategies ✅

### Sanity check — total_return with capital_allocation=200 and realized PnL=+50
```sql
SELECT
  CASE
    WHEN COALESCE(200, 0) = 0 THEN 0::float
    ELSE ROUND(50::numeric / NULLIF(200, 0)::numeric * 100, 2)::float
  END AS total_return;
-- total_return = 25  ✅  (not 50)
```

### Live API spot-check (GET /strategies from dashboard-api)
```
hltest-76b3:          capital_allocation=100, realized_pnl=72.77, total_return=72.77 ✅
e2e-ai-test-btc-f376: capital_allocation=100, realized_pnl=1.39,  total_return=1.39  ✅
test_blofin_demo_01:  capital_allocation=100, realized_pnl=9.83,  total_return=9.83  ✅
```
Formula confirmed: SUM(pnl_realized) / capital_allocation × 100.

### Listener test suite
```
29 passed, 2 warnings in 6.61s  ✅
```

### All 3 edited services rebuilt --no-cache and healthy ✅
- dashboard-api ✅ (built --no-cache, restarted healthy)
- order-listener ✅ (pytest 29/29)
- strategy-tester ✅ (built --no-cache, healthy)
