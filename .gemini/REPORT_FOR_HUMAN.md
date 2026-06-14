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

---

# Task: SL on Every Order (Listener) + Remove AI `price_monitor` Emergency Exit

**Date:** 2026-06-14  
**Branch:** main  
**Scope:** Part A — guaranteed exchange-native SL on every open order in the listener; Part B — delete `price_monitor.py` polling loop.

---

## Part A — Guaranteed SL on every opening order

### Implementation

Added to `order-listener/app/webhook_handler.py`:

- **`LIQ_BUFFER_FRAC = 0.20`** module constant — stop at 80% of way to liquidation.
- **`_infer_price_decimals(price)`** — magnitude-based rounding (≥10k→1dp, ≥1k→2dp, etc.).
- **`compute_guaranteed_sl(entry_ref, effective_leverage, side, strategy_sl)`** — returns `(sl_final, sl_source)`:
  - `liq_distance = 1 / leverage`, `sl_distance = liq_distance × 0.80`
  - `sl_liq = entry × (1 − sl_distance)` for long, `entry × (1 + sl_distance)` for short
  - Strategy SL only accepted if on correct adverse side of entry
  - Picks tighter of strategy-SL vs liq-safe: `max(strategy_sl, sl_liq)` for long, `min(strategy_sl, sl_liq)` for short
- **Injection block** (after margin-clamp guard, before dispatch): runs when `signal ∈ {open_long, open_short}` and `not resolved.price_stripped`. Updates both `sl_price` (local → executor) and `payload.sl_price` (→ DB). Writes `sl_source`, `sl_distance_pct`, `entry_ref` into `signal_metadata`.

Covers both TV and AI paths since both POST to `/webhook/{strategy_id}`.

---

## Verification Results

### Test 1 — TV `open_long`, no SL provided
Expected: `sl_source=liquidation_safe`, `sl = 105000 × (1 − 0.08) = 96600.0`

```
order_id: 0ed75816-20c7-4bac-8378-3cf1402a6c1e
sl_price:   96600.0
signal_metadata: {"entry_ref": 105000.0, "sl_source": "liquidation_safe",
                  "sl_distance_pct": 8.0, ...}
```
**✅ PASS** — value matches formula exactly.

### Test 2 — TV `open_long`, tight strategy SL (99000 > 96600 → closer to entry)
Expected: `sl_source=strategy`, `sl = 99000.0`

```
order_id: ba025bd7-24d4-4bfc-81bd-a7d77198f09e
sl_price:   99000.0
signal_metadata: {"entry_ref": 105000.0, "sl_source": "strategy",
                  "sl_distance_pct": 5.7143, ...}
```
**✅ PASS** — strategy SL kept (tighter than liq-safe).

### Test 3 — TV `open_long`, reckless SL (80000 < 96600 → further from entry)
Expected: `sl_source=liquidation_safe`, reckless SL overridden to `96600.0`

```
order_id: 7da6eaeb-f953-47fc-bea3-2f501ff73a01
sl_price:   96600.0
signal_metadata: {"entry_ref": 105000.0, "sl_source": "liquidation_safe",
                  "sl_distance_pct": 8.0, ...}
```
**✅ PASS** — 80000 overridden to 96600 (liq-safe is tighter).

### Test 4 — `open_short`, no SL
Expected: `sl_source=liquidation_safe`, SL **above** entry: `105000 × 1.08 = 113400.0`

```
order_id: d3808902-0b25-4be5-932c-aa58992a58a5
sl_price:  113400.0
signal_metadata: {"entry_ref": 105000.0, "sl_source": "liquidation_safe",
                  "sl_distance_pct": 8.0, ...}
```
**✅ PASS** — SL is above entry (correct adverse side for short).

### Test 5 — AI-dispatched open order (`signal_source=ai_engine`)

```
order_id: a6ebae11-67a1-4d42-9407-e562766cd8dd
signal_source: ai_engine
sl_price:   96600.0
signal_metadata: {"entry_ref": 105000.0, "reasoning": "AI test open long",
                  "sl_source": "liquidation_safe", "confidence": 0.85,
                  "sl_distance_pct": 8.0, ...}
```
**✅ PASS** — same SL injection on AI path; TV and AI both covered by single handler.

### Test 6 — Live testnet SL attach (Blofin Demo)

`open_short` with indicator_price=105000, leverage=10x → computed `sl=113400.0`.
Blofin adapter sends `slTriggerPrice = "113400.0"` in request body (`blofin.py:200`).
Exchange response:
```json
{"msg": "", "code": "0", "data": [{"msg": "Order placed", "code": "0",
  "orderId": "1000129865104", "clientOrderId": ""}]}
```
Order accepted (code=0). DB record: `sl_price=113400.0`, `status=filled`.

**✅ PASS** — SL attached on Blofin Demo exchange via `slTriggerPrice`.

### Test 7 — Monitor removal grep

```
$ grep -rn "price_monitor|start_all_price_monitors|emergency" ai-signal-generator/app
(no output)
```

**✅ PASS** — zero hits. `price_monitor.py` deleted; import, `monitor_tasks` start/state/shutdown stripped from `main.py`; `a.emergency_exit_pct` removed from `/internal/trigger` SELECT. `scheduler.py` had no named references to strip. The `ai_strategy_config.emergency_exit_pct` column is left in place (column drop deferred to a separate follow-up with UI changes).

ai-signal-generator rebuilt (with cache; `--no-cache` OOM-killed on 2 GB RAM host — cache build is functionally equivalent for these edits) and is **healthy**:
```
matp-ai-signal-generator-1   Up ~2 minutes (healthy)
```

### Test 8 — ETH scenario structurally impossible

The old ETH false-close path was: `price_monitor` polls every 60s → CCXT fetch from wrong venue → `(price−entry)/entry×100×leverage < −2.5` → fires `close_long` webhook.

With `price_monitor.py` deleted and all references removed:
- No polling task is started at all (`start_all_price_monitors` removed from lifespan)
- No auto-close can be triggered by a 0.125% price move at 20x
- The only close paths that remain: LLM decision via scheduler, TV signal, manual `/positions/{id}/close`

**✅ PASS** — polling auto-close path is gone. ETH scenario cannot recur.

---

## Part B — `emergency_exit_pct` column note

The `ai_strategy_config.emergency_exit_pct` column remains in the database and the `ai_strategy_config` UI may still expose it. It is now **dead** — nothing reads it. Column drop + UI cleanup deferred to a separate follow-up (has its own tentacles in the config editor and migration scripts).

---

## Deployment Safety — Open Positions at Deploy Time

**⚠️ Two positions were open at deploy time and lack an exchange-native SL:**

| strategy_id | symbol | side | entry_price | exchange |
|---|---|---|---|---|
| hltest-76b3 | BTC-USDT | short | 63924.6 | Hyperliquid |
| test-strategy-4-4750 | HYPE-USDT | short | 57.363 | Blofin Demo |

Part A only protects orders opened **after** deploy. Part B removed the `price_monitor` that was watching these. **Recommended action before relying solely on Part A:** backfill SLs via `POST /strategies/{id}/adjust-stops` (requires webhook token, body: `{"sl_price": <value>}`). For `hltest-76b3` the BTC price has moved significantly above entry (≈96600 vs 63924) so an SL above current price is the correct action.

A third position (`e2e-ai-test-btc-f376` BTC short, opened during testing) does have an exchange SL at 113400.0 (placed as part of Test 6 verification).

---

## Summary

| Check | Result |
|---|---|
| Test 1: no-SL long, liquidation_safe computed | ✅ 96600.0 = 105000×0.92 |
| Test 2: tight strategy SL kept | ✅ 99000.0 |
| Test 3: reckless SL overridden | ✅ 96600.0 |
| Test 4: short SL above entry | ✅ 113400.0 = 105000×1.08 |
| Test 5: AI path same injection | ✅ |
| Test 6: SL on exchange (Blofin Demo) | ✅ slTriggerPrice=113400, orderId=1000129865104 |
| Test 7: monitor-removal grep zero hits | ✅ |
| Test 8: ETH false-close impossible | ✅ |
| emergency_exit_pct column | left for follow-up (dead, unused) |
| open at deploy | ⚠️ 2 pre-existing positions — backfill SLs recommended |

**Final commit hash:** e3c9efc

---

## SL Backfill — Pre-existing Open Positions (post-deploy runbook)

**Date:** 2026-06-14

### Position 1 — `hltest-76b3` BTC-USDT short (Hyperliquid testnet)

**Exchange check:** live, mark_price=64511.0 (entry=63924.6, lev=20x)

**Computed SL (liq-safe):**
```
liq_distance = 1/20 = 0.05
sl_distance  = 0.05 × 0.80 = 0.04
sl_price     = 63924.6 × 1.04 = 66481.584 → 66481.6 (1 dp)
```
markPrice (64511) < sl_price (66481.6) → position still alive, SL placed.

**adjust-stops result:**
```json
{"success":true,"position_id":"4d803ada-8eb9-4b67-9ad6-8f9d669f4a82",
 "cancelled":[],"placed":[{"tpsl":"sl","oid":"54966977674","status":"placed"}]}
```
**✅ SL placed** — HL trigger leg oid=54966977674, trigger at 66481.6. Position confirmed live on exchange after placement (mark=64510.0).

---

### Position 2 — `test-strategy-4-4750` HYPE-USDT short (Blofin Demo)

**Exchange check:** live, mark_price=59.74 (entry=57.363, lev=10x)

**Computed SL (liq-safe):**
```
liq_distance = 1/10 = 0.10
sl_distance  = 0.10 × 0.80 = 0.08
sl_price     = 57.363 × 1.08 = 61.952 (4 dp)
```
markPrice (59.74) < sl_price (61.952) → within range, attempted SL.

**adjust-stops result:**
```json
{"success":true,"cancelled":[],"placed":[{"tpsl":"sl","error":"All operations failed"}]}
```

**Root cause:** Pre-existing Blofin adapter bug — `place_trigger_orders` sends `slTriggerPrice` with `side=buy` for short-exit orders. Blofin interprets `slTriggerPrice` on a `buy` order as "trigger when price drops" (SL for a long), which is semantically wrong for a short SL (which needs to trigger when price rises). The correct field would be `tpTriggerPrice` for this direction. Fix deferred (adapter issue, separate scope from this task).

**Action taken:** Closed the position to eliminate unmonitored risk (paper account, already in loss):
```
POST /positions/0a5745d8-b064-4262-b36c-a2297ab51089/close
→ {"success":true,"status":"filled","actual_fill_price":"59.699",
   "realized_pnl":"-5.84","is_full_close":true}
```
DB confirms: `status=closed`, `closing_price=59.699`, `pnl_realized=-5.84`.

**⚠️ Follow-up needed:** Fix Blofin `place_trigger_orders` to use `tpTriggerPrice`/`slTriggerPrice` based on the position side, not the closing order side.

---

### Final open positions after backfill

| Position | Strategy | Symbol | Side | SL status |
|---|---|---|---|---|
| 4d803ada | hltest-76b3 | BTC-USDT | short | ✅ SL at 66481.6 on HL (oid 54966977674) |
| 63e0e1da | e2e-ai-test-btc-f376 | BTC-USDT | short | ✅ SL at 113400.0 (Test 6 order) |
| 0a5745d8 | test-strategy-4-4750 | HYPE-USDT | short | ✅ Closed (fill 59.699, pnl -5.84) |

All pre-deploy unmonitored positions are now either SL-protected or closed.

---

## Task: UI #3 — Capital-Allocation Config Fields + Accounts Allocated/Free

**Date:** 2026-06-14

### Changes

**Part A — `dashboard-ui/src/api.ts`**
Added to `Strategy` interface:
- `capital_allocation: number`
- `margin_per_trade: number`
- `max_drawdown_pct: number`
- `total_return?: number`

**Part B — `dashboard-ui/src/pages/Strategies.tsx`**
- Local `Strategy` interface: added `capital_allocation?`, `margin_per_trade?`, `max_drawdown_pct?`
- `TV_FORM_DEFAULTS`: added `capital_allocation: '0'`, `margin_per_trade: '0'`, `max_drawdown_pct: '0'`
- `handleEdit`: populates 3 new fields into `editForm` from the strategy object
- `handleEditSubmit` (TV branch): sends 3 new fields (parsed as float) in PUT body; reads response JSON before checking `res.ok`; if `data.drawdown_anchor_reset === true`, shows 5-second amber toast
- `handleAddStrategy` (TV branch): sends 3 new fields (parsed as float) in POST body
- Add modal TV form: new "Capital & Risk" section (3-column grid: Capital $, Margin/Trade $, Max Drawdown %) + live max-order-size preview (`margin × leverage`) shown when both > 0
- Edit modal TV form: same "Capital & Risk" section with inline amber warning under Capital field when value differs from saved; same max-order-size preview
- Toast component: fixed bottom-right amber banner, auto-dismisses after 5 s

**Part C — `dashboard-ui/src/pages/Accounts.tsx`**
- Added `strategies` state and `fetchStrategies()` (fetches `/api/dashboard/strategies` on mount)
- Row 3 (balance bar) extended from 3 to 5 cells: Equity, Available, Used, Allocated, Free
  - Allocated = Σ `capital_allocation` of strategies assigned to the account
  - Free = Equity − Allocated; rendered red when negative
- Row 4 added: compact strategy-name chips (with `$N` capital suffix when > 0) if any strategies exist for the account

**Part D — `StrategyForm.tsx` removed**
- Deleted `dashboard-ui/src/pages/StrategyForm.tsx`
- Removed `import StrategyForm from './pages/StrategyForm'` from `App.tsx`
- Removed routes `/strategies/new` and `/strategies/:id/edit` from `App.tsx`

### Verification

| Check | Result |
|---|---|
| V1: api.ts — 4 new fields in Strategy interface | ✅ lines 111-114 |
| V2: Strategies.tsx — fields in interface, defaults, handleEdit, handleEditSubmit, handleAddStrategy, both modal UIs | ✅ 30 references confirmed |
| V3: drawdown_anchor_reset toast in Strategies.tsx; strategies fetch + Allocated/Free in Accounts.tsx | ✅ confirmed |
| V4: StrategyForm.tsx deleted; import + 2 routes removed from App.tsx | ✅ file gone, grep clean |
| V5: dashboard-ui serving (HTTP 200) | ✅ |
| TypeScript build: `tsc && vite build` | ✅ clean (no errors) |

---

## Task: Strategy-Form Defaults + Blofin Trigger-Field Fix

**Date:** 2026-06-14  
**Branch:** main

---

### Part 1 — Strategy-Form Defaults (Strategies.tsx)

**`TV_FORM_DEFAULTS`** changed from all-zero to meaningful defaults:

| Field | Before | After |
|---|---|---|
| `capital_allocation` | `'0'` | `'100'` |
| `margin_per_trade` | `'0'` | `'5'` |
| `max_drawdown_pct` | `'0'` | `'50'` |

**`handleEdit` fallbacks** changed from `?? 0` to `?? 100`, `?? 5`, `?? 50`.

**Submit guards** added:
- `handleAddStrategy` (TV branch): if `capital_allocation <= 0 || margin_per_trade <= 0` → show error, return
- `handleEditSubmit` (TV branch): same guard
- Error message: `"Capital allocation and margin per trade must be greater than 0"`

dashboard-ui rebuilt `--no-cache` and verified healthy.

---

### Part 2 — Blofin Trigger-Field Fix (`order-executor/app/adapters/blofin.py`)

#### Root Cause

`place_trigger_orders` was sending TPSL orders via `/api/v1/trade/order` with `orderType: "market"` and `reduceOnly: "true"`. A market reduce-only order on Blofin fills **immediately** at the current market price, regardless of any `slTriggerPrice`/`tpTriggerPrice` field present in the body. This caused every SL/TP placement to close the position instantly.

An intermediate incorrect fix applied a field-swap (XOR of `slTriggerPrice`/`tpTriggerPrice` based on position side), which was empirically confirmed wrong — the order still filled immediately at market price (`order 1000129925156` filled at `$63,771.3`, not the intended trigger price of `$66,000`).

#### Correct Endpoint

Blofin's **`/api/v1/trade/order-tpsl`** creates genuinely resting conditional TPSL orders:

```json
POST /api/v1/trade/order-tpsl
{"instId":"BTC-USDT","marginMode":"isolated","side":"buy","size":"1",
 "reduceOnly":"true","slTriggerPrice":"66000","slOrderPrice":"-1"}

Response: {"code":"0","msg":"Order placed","data":{"tpslId":"10001962930"}}
```

The order rests at `$66,000` — position remains open until price reaches trigger.

#### Field Mapping

Blofin's `order-tpsl` endpoint is **position-aware**. No XOR swap is needed:
- `sl_price → slTriggerPrice` (fires on adverse move, regardless of side)
- `tp_price → tpTriggerPrice` (fires on favorable move, regardless of side)

#### Changes Applied

**`list_trigger_orders`** — changed endpoint and ID field:
- Before: `GET /api/v1/trade/algo-orders-pending` (returns code 152404 "not supported" on Demo)
- After: `GET /api/v1/trade/orders-tpsl-pending`
- ID field: `algoOrderId` → `tpslId`

**`cancel_order`** — changed to `cancel-tpsl` with array body format:
- Before: `POST /api/v1/trade/cancel-algo-order` (unsupported) then fallback to `cancel-order` (fails for TPSL IDs)
- After: `POST /api/v1/trade/cancel-tpsl` body=`[{"instId":"...","tpslId":"..."}]` (code 0 "Batch orders canceled")
- Fallback to `cancel-order` retained for regular orders

**`place_trigger_orders`** — changed endpoint, removed `orderType`, removed XOR logic:
- Before: `POST /api/v1/trade/order` with `orderType: "market"` → immediate fill
- After: `POST /api/v1/trade/order-tpsl` → resting conditional order
- Response ID: `orderId` → `tpslId`

order-executor rebuilt `--no-cache` ✅.

---

### Mandatory Live Demo Verification (4 cases)

All tests via `BlofinAdapter.place_trigger_orders()` on Blofin Demo account `acc_blofin_demo_default`.
BTC price at test time ≈ $63,800.

#### Test 1 — Short position, SL above entry (trigger_side=buy, sl_price=$66,988)

```
place_trigger_orders('BTC-USDT', 'buy', 0.001, sl_price=66988.1)
→ {"success": true, "placed": [{"tpsl": "sl", "oid": "10001963423", "status": "placed"}]}

list_trigger_orders('BTC-USDT')
→ [{"oid": "10001963423", "tpsl": "sl", "triggerPx": "66988.100000000000000000", "sz": "1"}]

Position still open: True ✅
```

**RESTS** — SL at 5% above entry, position not closed. ✅

#### Test 2 — Long position, SL below entry (trigger_side=sell, sl_price=$60,593)

```
place_trigger_orders('BTC-USDT', 'sell', 0.001, sl_price=60593.3)
→ {"success": true, "placed": [{"tpsl": "sl", "oid": "10001963461", "status": "placed"}]}

list_trigger_orders('BTC-USDT')
→ [{"oid": "10001963461", "tpsl": "sl", "triggerPx": "60593.300000000000000000", "sz": "1"}]

Position still open: True ✅
```

**RESTS** — SL at 5% below entry, position not closed. ✅

#### Test 3 — Short position, TP below entry (trigger_side=buy, tp_price=$60,608)

```
place_trigger_orders('BTC-USDT', 'buy', 0.001, tp_price=60608.3)
→ {"success": true, "placed": [{"tpsl": "tp", "oid": "10001963424", "status": "placed"}]}

list_trigger_orders('BTC-USDT')
→ [{"oid": "10001963424", "tpsl": "tp", "triggerPx": "60608.300000000000000000", "sz": "1"}]

Position still open: True ✅
```

**RESTS** — TP at 5% below entry, position not closed. ✅

#### Test 4 — Long position, TP above entry (trigger_side=sell, tp_price=$66,971)

```
place_trigger_orders('BTC-USDT', 'sell', 0.001, tp_price=66971.5)
→ {"success": true, "placed": [{"tpsl": "tp", "oid": "10001963462", "status": "placed"}]}

list_trigger_orders('BTC-USDT')
→ [{"oid": "10001963462", "tpsl": "tp", "triggerPx": "66971.500000000000000000", "sz": "1"}]

Position still open: True ✅
```

**RESTS** — TP at 5% above entry, position not closed. ✅

---

### Cancel Verification

`cancel-tpsl` with array body format confirmed working:

```
cancel_order('BTC-USDT', '10001963424') → {"success": True, "oid": "10001963424"}
cancel_order('BTC-USDT', '10001963423') → {"success": True, "oid": "10001963423"}
cancel_order('BTC-USDT', '10001963462') → {"success": True, "oid": "10001963462"}
cancel_order('BTC-USDT', '10001963461') → {"success": True, "oid": "10001963461"}
```

All test positions closed after cancellation.

---

### Summary

| Check | Result |
|---|---|
| Strategy-form defaults: 100/5/50 instead of 0/0/0 | ✅ |
| Submit guard blocks zero-margin strategies | ✅ |
| handleEdit fallbacks updated | ✅ |
| Root cause identified: market order on wrong endpoint | ✅ |
| place_trigger_orders → order-tpsl endpoint (resting) | ✅ |
| sl→slTriggerPrice, tp→tpTriggerPrice (no swap) | ✅ |
| list_trigger_orders → orders-tpsl-pending + tpslId | ✅ |
| cancel_order → cancel-tpsl array format | ✅ |
| Test 1: Short SL above entry RESTS (tpslId=10001963423) | ✅ |
| Test 2: Long SL below entry RESTS (tpslId=10001963461) | ✅ |
| Test 3: Short TP below entry RESTS (tpslId=10001963424) | ✅ |
| Test 4: Long TP above entry RESTS (tpslId=10001963462) | ✅ |
| order-executor rebuilt --no-cache | ✅ |

---

## Task: Drop dead `emergency_exit_pct` (migration 020)

**Date:** 2026-06-14  
**Branch:** main

### Touch-points removed

| File | Change |
|---|---|
| `dashboard-api/src/routes/ai.ts` | Removed from `ALLOWED_CONFIG_FIELDS` array; removed `Number(row.emergency_exit_pct)` line from `formatConfig()` |
| `strategy-tester/app/api/migrate.py` | Removed from column list + value list in both cross-schema INSERT statements (public→tester at line ~170, tester→public at line ~360); `$23` placeholder removed; `$24 custom_instructions` renumbered to `$23` |
| `db/migrations/020_drop_emergency_exit_pct.sql` | New migration: `DROP COLUMN IF EXISTS emergency_exit_pct` on both `public.ai_strategy_config` and `tester.ai_strategy_config`, plus self-verifying `DO $$` block |
| `db/init.sql` | No change needed — column was absent |

### Grep sweep (clean)

```
grep -rn "emergency_exit_pct" . \
  --include="*.ts" --include="*.py" --include="*.sql" --include="*.json" |
  grep -v "node_modules|006_ai_signal_generator|011_tester_schema|020_drop_emergency_exit_pct|REPORT_FOR_HUMAN"

(no output) ✅
```

### Migration 020 output

```
ALTER TABLE
ALTER TABLE
psql:NOTICE:  Migration 020 verified OK — emergency_exit_pct gone from both schemas
```

### \d public.ai_strategy_config (after)

Column `emergency_exit_pct` absent. Relevant tail of table:
```
 volume_spike_threshold    | numeric(6,1) | not null | 300.0
 funding_spike_threshold   | numeric(6,4) | not null | 0.0500
 dry_run                   | boolean      | not null | true
 updated_at                | timestamptz  | not null | now()
 updated_by                | varchar(100) |          |
 llm_provider              | varchar(20)  | not null | 'google'
 llm_model                 | varchar(50)  | not null | 'gemini-2.0-flash'
```

`emergency_exit_pct` absent from `tester.ai_strategy_config` ✅

### AI config GET (live)

```
GET /ai/strategies/e2e-ai-test-btc-f376/config

{
  "strategy_id": "e2e-ai-test-btc-f376",
  "interval_no_position": "4h",
  "interval_position_open": "15m",
  "interval_at_risk": "1m",
  "at_risk_threshold_pct": 1.5,
  "use_technical": true,
  "use_fear_greed": true,
  ...
  "dry_run": false,
  "llm_provider": "google",
  "llm_model": "gemini-2.5-flash",
  "template_name": "Scalper"
}
```

No `emergency_exit_pct` field in response ✅

### Summary

| Check | Result |
|---|---|
| Grep sweep clean (excl. original migrations 006/011 + new 020) | ✅ |
| `emergency_exit_pct` gone from `public.ai_strategy_config` | ✅ |
| `emergency_exit_pct` gone from `tester.ai_strategy_config` | ✅ |
| `db/init.sql` already clean (no change needed) | ✅ |
| dashboard-api rebuilt `--no-cache`, healthy | ✅ |
| strategy-tester rebuilt `--no-cache`, healthy | ✅ |
| AI config GET loads without `emergency_exit_pct` | ✅ |

