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
