# Amended orders now draw as a staircase, in TradingView position-tool style

**Date:** 2026-07-27
**Fixes:** `.gemini/reports/eth-pending-order-chart-backdated-price.md`
**Scope:** record every price a resting order has held, draw the levels over the bars they
were actually live for, and restyle the risk/reward overlay after TradingView's
Long/Short Position drawing tool.

---

## The problem being fixed

A resting limit order is amended in place. `orders.price` / `sl_price` / `tp_price` are
overwritten and `received_at` is left alone, so the row carries the **latest** price with the
**original** placement time. The chart joined the two and drew one flat line across the whole
span. ETH order `86ee9b20` showed a buy at 1901.84 starting 2026-07-26 09:01, with candles
sitting under it all day — reading as an order that should have filled immediately, when in
truth it was at 1869.56 that morning and only reached 1901.84 at 07:01 the next day.

Nothing stored the intermediate prices; they survived ~2 days in container logs and nowhere
else.

## What changed

**1. `order_price_history` — migration `065`.** One row per price an order has held, oldest
first. `seq 0` is the original placement, each successful amend appends. Carries price, stop,
target, size and the exchange order id (Hyperliquid's modify returns a fresh oid each time).

**2. order-listener writes it.**
- `_log_order` writes `seq 0` (`source='placement'`) for any order with a price, before the
  exchange sees it — so the original intent is on record even for an order the venue rejects.
  Market orders have no resting price and are skipped.
- `amend_order_for_strategy` appends (`source='amend'`) using the **post-UPDATE** values, so a
  partial amend (price only, no tp/sl) records what the order actually holds rather than what
  the request mentioned. The UPDATE now uses `RETURNING` to get the row id.

**3. dashboard-api returns it** as `overlay.steps` on all three chart types (order, position,
AI-signal). Steps with no price are dropped.

**4. dashboard-ui draws it.** `computeRiskReward` turns the steps into `segments` — one rung
per recorded price, each running until the next replaces it. Orders with no history produce
exactly one rung and look as they always did.

**5. The overlay is restyled** after TradingView's position tool: a green reward zone from
entry to target, a red risk zone from entry to stop, a solid entry line between them, dashed
risers connecting the rungs, and price/percentage/R:R chips on the right edge. The old
grey stop→target box is gone.

**6. Under the chart**, a "Price moved N×" stat, marked `(partly recorded)` when the history
was reconstructed rather than recorded live.

### Backfill and its honesty

Migration 065 backfills `seq 0` from `raw_webhook` — the one field an amend never overwrites,
so it is the true original — and `seq 1` from the row's current values wherever they differ,
timestamped `updated_at`. Both are marked `source='backfill'`.

That gives a two-step staircase for historical orders: **both ends are real, the walk between
them is not**. It is strictly better than the flat line it replaces, and the UI says so
rather than pretending the history is complete. Orders placed from now on record every step.

## Verification

### Migration applied

```
$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/065_order_price_history.sql
CREATE TABLE
COMMENT
COMMENT
CREATE INDEX
CREATE INDEX
INSERT 0 50
INSERT 0 21
```

50 orders got an original-placement row; 21 of those had been amended and got a second.

### The order from the original report

```
$ psql -c "SELECT seq, at, price, sl_price, tp_price, source FROM order_price_history
           WHERE order_id='86ee9b20-b1de-4ea8-ae41-48f51b514ee2' ORDER BY seq;"

 seq |              at               |  price  | sl_price | tp_price  |  source
-----+-------------------------------+---------+----------+-----------+----------
   0 | 2026-07-26 09:01:21.618402+00 | 1869.56 |  1863.95 | 1880.7774 | backfill
   1 | 2026-07-27 07:01:12.891713+00 | 1901.84 |  1890.46 |   1979.94 | backfill
```

The chart now draws 1869.56 from 09:01 on the 26th — correctly *below* the candles, which is
what a resting buy limit looks like — and steps up to 1901.84 only where it belongs.

### Live API, from inside the network

```
$ docker compose exec -T nginx wget -qO- \
    "http://dashboard-api:8003/orders/86ee9b20-b1de-4ea8-ae41-48f51b514ee2/candles?tf=1h"

symbol ETH-USDT tf 1h candles 301
entry_price (latest): 1901.84  placed_at: 2026-07-26 09:01:21.618000+00:00
steps:
   2026-07-26 09:01:21.618000+00:00 entry 1869.56 sl 1863.95 tp 1880.7774 backfill
   2026-07-27 07:01:12.891000+00:00 entry 1901.84 sl 1890.46 tp 1979.94 backfill
```

The other two chart types still build, with and without steps:

```
position 2a7fcb46-d549-48d8-8110-72577436c842
  candles 301 entry 573.46 steps 0          <- market entry, no resting price: one box, as before

$ .../ai/signals/4891/candles
candles 300 entry 1980.8 steps 1 geometry True
   {'at': 1785139300891, 'entry': 1980.8, 'stop': 1992.09, 'target': 1903.5488, 'source': 'backfill'}
```

### The amend INSERT, validated against the live schema

Run exactly as the listener runs it, inside a rolled-back transaction so nothing was written:

```
BEGIN
INSERT 0 1
 seq |  price  | sl_price | tp_price  | exchange_order_id |  source
-----+---------+----------+-----------+-------------------+----------
   0 | 1869.56 |  1863.95 | 1880.7774 |                   | backfill
   1 | 1901.84 |  1890.46 |   1979.94 | 57045212046       | backfill
   2 | 1911.11 |  1899.99 |   1988.88 | 99999999999       | amend
ROLLBACK
```

`COALESCE(MAX(seq), -1) + 1` appends at the right place.

### Tests

```
$ npx vitest run src/charts
 Test Files  2 passed (2)
      Tests  42 passed (42)
```

28 pre-existing tests still pass unchanged. 7 new Layer-A cases cover the staircase: one
segment without history, one per recorded price with the right spans, stop and target
stepping alongside the entry, the `reconstructed` flag, out-of-order and price-less steps,
collapsing steps that fall outside the charted window, and the risk/reward numbers staying on
the newest levels.

7 new Layer-B cases drive the renderer with a stubbed chart and canvas and read back the
rectangles: green zone above the entry for a long and below it for a short, one pair of zones
per rung, the dashed risers, a solid entry line per rung, the label text, and nothing drawn at
all when the zones are scrolled off screen.

### Deploy

```
$ ./scripts/redeploy.sh order-listener   -> Up (health: starting)
$ ./scripts/redeploy.sh dashboard-api    -> Up (health: starting)
$ ./scripts/redeploy.sh dashboard-ui     -> live dashboard-ui asset: index-Babe_xx0.js

$ docker compose exec -T dashboard-ui grep -rl 'Price moved' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-Babe_xx0.js
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-Babe_xx0.js
```

Both builds run `tsc` before bundling, so the deploy is also a typecheck. Clean.

### Live write path

<!-- LIVE_AMEND_PROOF -->

## Not verified

The rendered picture. Everything above proves the data reaching the canvas and the
rectangles the renderer asks for, but no browser was driven and no screenshot was taken —
the visual result is unconfirmed by me.

## Files touched

- `db/migrations/065_order_price_history.sql` (new)
- `order-listener/app/webhook_handler.py`
- `dashboard-api/src/chartData.ts`
- `dashboard-ui/src/charts/core/types.ts`
- `dashboard-ui/src/charts/core/riskReward.ts`
- `dashboard-ui/src/charts/adapters/lightweightCharts/riskRewardPrimitive.ts`
- `dashboard-ui/src/charts/adapters/lightweightCharts/index.ts`
- `dashboard-ui/src/components/ExpandableChart.tsx`
- `dashboard-ui/src/charts/core/__tests__/riskReward.test.ts`
- `dashboard-ui/src/charts/adapters/lightweightCharts/__tests__/riskRewardPrimitive.test.ts` (new)

## Still open

The entry band and take-profit band from the earlier proposal are **not** built. They were
left out deliberately pending the decision on values, and the counterfactual in
`.gemini/reports/ai-limit-orders-no-amend-counterfactual.md` argues the band is a narrow
tool — one near-miss in thirteen amend intervals.
