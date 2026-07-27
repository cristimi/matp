# ETH pending buy order looks like it should have filled — chart draws the *current* price back to placement time

**Date:** 2026-07-27
**Order:** `86ee9b20-b1de-4ea8-ae41-48f51b514ee2` — ETH-USDT buy limit, strategy `eth-ai-34d2`
**Verdict:** representation issue in the chart. The order was never fillable. Nothing was missed
by the exchange or the executor.

---

## The complaint

On the order chart the pending buy limit is drawn at **1901.84** as a line starting at its
placement time (2026-07-26 09:01 UTC). The candles sit *below* that line from 09:01 until the
14:00–15:00 UTC breakout. A resting buy limit fills when price trades down to it, so the drawing
says the order should have filled instantly, and certainly by the 14:00 bar.

## What actually happened

The order row is **amended in place** roughly every hour by the AI engine. `orders.price` and
`orders.exchange_order_id` are overwritten; `orders.received_at` is not. So the row carries the
*latest* price with the *original* placement time. The chart joins those two into one line.

The price only became 1901.84 at **2026-07-27 07:01 UTC** — ~22 hours after the timestamp the
line starts from.

### Amend history (from order-listener logs)

```
2026-07-26 10:01:18  order_id=57005191952  new_price=1876.436
2026-07-26 11:00:55  order_id=57006891811  new_price=1877.156
2026-07-26 12:01:16  order_id=57008535290  new_price=1877.876667
2026-07-26 13:01:28  order_id=57010217225  new_price=1878.596984
2026-07-26 17:01:56  order_id=57011995946  new_price=1886.526
2026-07-26 19:01:13  order_id=57019547023  new_price=1888.71
2026-07-26 22:03:41  order_id=57022886084  new_price=1891.995
2026-07-26 22:50:41  order_id=57027889371  new_price=1891.995
2026-07-27 01:20:27  order_id=57029428616  new_price=1895.276915
2026-07-27 02:01:14  order_id=57034336813  new_price=1896.370745
2026-07-27 03:00:55  order_id=57035679792  new_price=1897.46
2026-07-27 07:01:12  order_id=57037565966  new_price=1901.84   <-- current
```

(The 21:01 / 05:01 / 06:01 amends at 1927.3 / 1973.68 / 1976.81 belong to the *short* orders
`abec4581…` and `1323d6c3…`, not to this one.)

Original placement, from `raw_webhook`:

```
"price": "1869.56", "side": "buy", "signal": "open_long", "order_type": "limit"
"entry_ref": 1869.56  — "placing a limit long at the lower boundary (1869.56)"
```

### The order price was always below the market

ETH-USDT 1h candles (blofin, from redis `stream:candles:blofin:ETH-USDT:1h`), UTC:

```
2026-07-26 09:00  O=1880.56 H=1884.91 L=1880.49 C=1882.94     order @ 1869.56
2026-07-26 10:00  O=1883.13 H=1886.71 L=1882.01 C=1885.02     order @ 1876.436
2026-07-26 11:00  O=1885.03 H=1887.00 L=1883.76 C=1885.00     order @ 1877.156
2026-07-26 12:00  O=1885.30 H=1893.00 L=1884.71 C=1885.49     order @ 1877.877
2026-07-26 13:00  O=1885.48 H=1887.77 L=1880.66 C=1885.70     order @ 1878.597
2026-07-26 14:00  O=1885.70 H=1899.80 L=1885.70 C=1897.73     order @ 1878.597
2026-07-26 15:00  O=1898.19 H=1916.42 L=1897.03 C=1913.76     order @ 1878.597
2026-07-26 16:00  O=1914.83 H=1927.78 L=1908.00 C=1915.96     order @ 1878.597
2026-07-26 17:00  O=1915.49 H=1917.16 L=1910.25 C=1910.25     order @ 1886.526
...
2026-07-27 06:00  O=1964.68 H=1981.05 L=1962.66 C=1967.25     order @ 1901.84
```

At every moment the resting buy price sat below that hour's low. The gap never closed, so the
order never touched. The 14:00 bar the eye lands on had a low of 1885.70 against an order price
of 1878.60 — 7 points short.

### The exchange agrees

```
$ docker compose exec -T nginx wget -qO- \
    "http://order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/orders"
[{"order_id":"57045212046","symbol":"ETH-USDT","side":"buy","price":1901.8,"size":0.5349,
  "filled_size":0.0,"status":"resting","created_at_ms":1785135672216}]
```

`created_at_ms=1785135672216` = **2026-07-27 07:01:12 UTC** — the exchange-side order at 1901.8
is 6 hours old, not 22. Hyperliquid's modify issues a fresh oid each time, so the venue itself
has no memory of the price stretching back to yesterday. Only our `orders` row does, and only
because `received_at` is left untouched.

## Where the code does it

`order-listener/app/webhook_handler.py:697-715` — amend updates price/size/oid in place:

```sql
UPDATE orders
SET exchange_order_id = $1,
    price = COALESCE($2, price),
    ...
WHERE exchange_order_id = $6 AND strategy_id = $7 AND status = 'pending'
```

No history row is written and `received_at` is preserved by design.

`dashboard-api/src/chartData.ts:426-429` (`buildOrderChart`) then hands the UI:

```ts
placed_at:   toMs(r.oel_placed_at) ?? toMs(r.received_at),
entry_price: toNum(r.actual_fill_price) ?? toNum(r.price),
```

`entry_price` is today's amended price; `placed_at` is yesterday's original submit.

`dashboard-ui/src/charts/core/riskReward.ts:121-133` draws the outer box from `placed` to the
last candle at that single price — a flat line across the whole span:

```ts
const placedRaw = overlay.placed_at ?? overlay.filled_at ?? firstCandleTime;
const placed = onGrid(placedRaw);
const end    = Math.max(onGrid(endRaw), placed);
const outer: PriceTimeBox = { from: placed, to: end, low: …, high: … };
```

The same flaw applies to `sl_price` / `tp_price`, which the amend also rewrites (`tp_price=1979.94
sl_price=1890.46` as of 07:01) — the stop/target edges of the box are likewise today's values
drawn back to yesterday.

Note: the `oel_placed_at` fallback is dead for any amended order. `order_execution_log` is keyed
on `exchange_order_id`, and the amend replaced it, so the join misses. Confirmed — the only OEL
row is for the original oid:

```
id                | 205
exchange_order_id | 57005191952        <-- original, superseded
requested_price   | 1869.56
placed_at         | 2026-07-26 09:01:22.302163+00
filled_at         | 2026-07-26 09:01:24.579801+00
status            | pending
```

Two side observations from that row, neither affecting the chart today:

1. `filled_at` is set 2 seconds after placement on a `status=pending` row that never filled. If
   the oid join ever matched again, `buildOrderChart` would draw a bogus inner "filled" box.
2. Because the join misses, an amended order also loses its real exchange-side placement stamp.

## Options (not implemented — no changes made)

1. **Cheapest, honest:** draw the entry line only from the last amend, not from `received_at`.
   Needs an `amended_at` (or reuse `updated_at`) on the orders row and a
   `placed_at: amended_at ?? received_at` in `buildOrderChart`.
2. **Fullest:** record each amend as a row in `order_events` (the table exists and is empty for
   this order) and draw the entry as a step line that follows the price through time. This is the
   only version that shows what the strategy actually did — walking the bid up behind the market
   all day.
3. **Stopgap:** label the line "current price, amended N times" in the chart details so the eye
   stops reading it as a level that has been there since placement.

## Files touched

None. Investigation only, as requested.
