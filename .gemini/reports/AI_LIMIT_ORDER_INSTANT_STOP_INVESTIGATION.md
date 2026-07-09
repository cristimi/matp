# Investigation: AI limit orders filled at wrong price and stopped out instantly (2026-07-09)

## Summary

Both AI limit entries placed on 2026-07-09 (ETH short 05:05 UTC, BTC long 17:05 UTC) were
**marketable on arrival**: by the time the order reached Hyperliquid (~3 minutes after the
AI analyzed the market), price had already traded through the intended limit price. A GTC
limit order that is already through the market fills **immediately at the current market
price**, not at the limit price — while the SL/TP trigger legs are computed from the
*intended* limit price (`node_guard.py`). Result: entries at or beyond their own stop loss,
closed by the exchange trigger within minutes.

This is not the AI picking a bad price and not an executor pricing bug — it is a
**latency + no-marketability-check** design gap.

## The two orders

### ETH — eth-ai-34d2, order `ffcd8801`

AI decision (ai_signal_log id 655, triggered 05:02:30):

> "Price is at the upper boundary of a strong descending channel (position in range 100.0%)
> ... Placing a limit short at the upper boundary (1733.94) with stop just above
> (0.58% / 0.75x ATR)"

- Intended: sell limit **1733.94**, SL 1744.0, TP 1687.64
- Order received by listener 05:05:12 (2m42s after analysis) — ETH had rallied ~0.57%
- Filled at **1743.91** — 0.09 below the SL of 1744.0
- Position closed by exchange SL trigger at 05:12:18 (`close_reason = Closed on exchange`,
  pnl −0.0104)

### BTC — ai-btc-6f8c, order `c23b8282`

AI decision (ai_signal_log id 708, triggered 17:02:31):

> "The current price of 62731.0 is near the lower boundary of the ascending channel
> (62516.93) ... A limit long order is placed at the lower boundary to fade the range.
> The stop loss is set 0.75% below the entry."

- Intended: buy limit **62516.93**, SL 62048.1, TP 63467.19
- Order received 17:05:39 (3m08s after analysis) — BTC had dropped ~1.6% (62731 → 61718.9)
- Filled at **61718.90** — already **329 below the SL** of 62048.1; the stop condition was
  true at fill time
- Position closed by exchange trigger at 17:07:57 (`close_reason = Closed on exchange`)

## Evidence

Orders (webhook price vs actual fill vs SL):

```
                  id                  |         received_at           | side |  wh_price | actual_fill | sl_price
 ffcd8801-b784-4f7c-9007-c86cd7594f5e | 2026-07-09 05:05:12.107573+00 | sell |  1733.94  |   1743.91   |  1744.0
 c23b8282-ae54-4eaf-a436-ab208e967f62 | 2026-07-09 17:05:39.81816+00  | buy  | 62516.93  |   61718.9   | 62048.1
```

Cycle timing from ai-signal-generator logs (ETH case; BTC identical shape):

```
05:02:30,774 app.scheduler: Triggering cycle strategy=eth-ai-34d2 reason=scheduled
05:04:43,757 GET http://order-listener:8001/strategies/eth-ai-34d2/orders   <- data collection ~2m13s
05:05:11,228 node_analyze: LLM [google/gemini-2.5-flash] -> action=place_limit_short confidence=0.780
05:05:12,191 node_dispatch: Webhook fired ... order_id=ffcd8801-...
```

```
17:02:31,724 Triggering cycle strategy=ai-btc-6f8c reason=scheduled
17:05:39,443 node_analyze: LLM -> action=place_limit_long confidence=0.750
17:05:40,504 node_dispatch: Webhook fired ... order_id=c23b8282-...
```

Positions:

```
 strategy_id |  symbol  | side  | entry_price |           opened_at           |           closed_at           | close_reason
 ai-btc-6f8c | BTC-USDT | long  |     61718.9 | 2026-07-09 17:05:45.395435+00 | 2026-07-09 17:07:57.041826+00 | Closed on exchange
 eth-ai-34d2 | ETH-USDT | short |     1743.91 | 2026-07-09 05:05:16.941211+00 | 2026-07-09 05:12:18.746873+00 | Closed on exchange
```

## Root causes

1. **~3-minute decision-to-order latency.** The cycle fires at candle-close+buffer
   (~:02:30); data collection takes ~2–2.5 min (sentiment/OI venue fetches dominate,
   e.g. a binance dapi error retry visible at 05:04:22), the LLM adds ~30–40 s. The
   geometry/prices the LLM reasons over are from the candle close, so the limit price is
   up to ~5 min stale when it reaches the exchange.

2. **No marketability check anywhere in the chain.**
   - `ai-signal-generator/app/graph/nodes/node_guard.py` (place_limit branch, ~line 102):
     computes SL/TP as percentages off `limit_price` and passes the order through without
     comparing `limit_price` to any current price.
   - `order-executor/app/adapters/hyperliquid.py` (`_submit_order`, ~line 397): limit
     orders are sent as `{"limit": {"tif": "Gtc"}}` at the given price with `normalTpsl`
     trigger legs — no check that the limit is still on the passive side of the mark price
     (the adapter already fetches mark price for market orders, so the data is one request
     away).

   So when price has traded through the limit, the entry fills at market and the SL
   (anchored to the intended limit price) is instantly at/inside the fill.

## Possible fixes (not implemented — for discussion)

- **Executor-side guard (most robust):** before submitting a GTC entry limit, fetch mark
  price; if the order is already marketable (buy limit ≥ mark, sell limit ≤ mark), reject
  it back to the listener as `rejected` with a clear error. The AI re-evaluates next cycle.
  Hyperliquid also supports ALO (add-liquidity-only / post-only) tif, which makes the
  exchange itself cancel a would-be-taker limit — a one-line change (`"tif": "Alo"`) that
  guarantees passive fills for these resting entries.
- **Guard-node sanity check (cheap, partial):** in node_guard, reject `place_limit_*` when
  the limit price is on the wrong side of the collection-time snapshot
  (`ohlcv_data.current_price`). Would have caught the ETH case (limit == snapshot price)
  but *not* the BTC case (the move happened after collection).
- **Latency reduction:** parallelize/shorten sentiment collection; it dominates the window.

No code was changed in this investigation.
