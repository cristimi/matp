# Pending-order live mark price + "modified" timestamp on Tree page

## Problem

On the Tree page, `PendingOrderCard` showed a mark price that never updated —
it was a one-shot value fetched by `GET /strategies/tree` at page load
(`dashboard-api/src/routes/strategies.ts`), unlike open positions, which get
live updates via the `/ws/pnl` websocket (`useLivePnl`). The card also had no
"last modified" timestamp, even though `orders.updated_at` already existed in
the DB with an auto-update trigger (`update_orders_updated_at`).

## Changes

**Backend — `dashboard-api/src/livePnl.ts`**
- `PnlSnapshot` gained a `pending_orders: Record<orderId, { mark_price }>` field.
- `tick()` now also queries pending orders (`status = 'pending'`) and fans out a
  mark-price fetch per unique `(account_id, symbol)` via the executor's public
  ticker endpoint (`/accounts/{id}/mark-price/{symbol}`), same call
  `routes/strategies.ts` used to make once per page load.
- Throttled to every 5th tick (`PENDING_MARK_EVERY_N_TICKS = 5`, ~5s at the
  default 1s `PNL_TICK_MS`) rather than every tick. Polling every 1s during
  testing measurably added to executor request volume and coincided with
  request timeouts — throttling keeps it "live" while cutting added load 5x.
  The last computed pending-order map is cached across throttled ticks so
  the value doesn't blank out between refreshes.

**Backend — `dashboard-api/src/routes/strategies.ts`**
- `GET /strategies/tree`'s pending-orders query now selects `o.updated_at` and
  returns it as `updated_at` per order.
- Removed the route's own one-shot mark-price fetch; it now reads
  `mark_price` from the shared live-PnL Redis snapshot (`snapshot?.pending_orders`),
  the same pattern already used for open positions — single source of truth,
  no duplicate executor calls on every page load.

**Frontend**
- `dashboard-ui/src/api.ts`: `PendingOrder.updated_at: string` added.
- `dashboard-ui/src/hooks/useLivePnl.ts`: `PnlSnapshot.pending_orders` added
  to the type and parsed from incoming `/ws/pnl` messages.
- `dashboard-ui/src/pages/StrategyTree.tsx`: `PendingOrderCard` now takes a
  `livePnl` prop (passed down from `StrategyCard`, same as `PositionCard`),
  reads `livePnl?.pending_orders?.[o.id]?.mark_price ?? o.mark_price` for a
  live-updating mark price, and renders `Modified {formatRelative(o.updated_at)}`
  in the card footer.

## Verification

TypeScript compiles clean on both services:
```
$ cd dashboard-api && npx tsc --noEmit   # exit 0
$ cd dashboard-ui  && npx tsc --noEmit   # exit 0
```

Deployed both via `./scripts/redeploy.sh dashboard-api` and
`./scripts/redeploy.sh dashboard-ui`; both containers are `Up ... (healthy)`.

`GET /strategies/tree` now returns both fields for the live pending order:
```
{'id': 'c992a3a0-ab00-475d-8b4d-ed90f0e55a16', 'symbol': 'BTC-USDT', 'side': 'sell',
 'price': 64093.5, 'sl_price': 64574.2, 'tp_price': 63606.3894,
 'mark_price': 63995,
 'received_at': '2026-07-12T12:01:44.560Z',
 'updated_at': '2026-07-12T12:01:47.466Z'}
```

Connected directly to `/ws/pnl` and watched `pending_orders` across ~20
snapshots over 20s — mark price refreshes live (throttled cadence, ~5s):
```
16:17:30.822Z {"c992a...":{"mark_price":63993}}
16:17:32.733Z {"c992a...":{"mark_price":63992}}
16:17:38.358Z {"c992a...":{"mark_price":63991}}
...
```

Confirmed the new UI string shipped in the live bundle:
```
$ docker compose exec -T dashboard-ui grep -c 'Modified' /usr/share/nginx/html/assets/index-*.js
1
```

## Pre-existing issue found (not fixed, flagged for follow-up)

While testing, `order-executor` is currently reporting `unhealthy` in
`docker compose ps` (been so for the container's full 32h uptime) and is
issuing GET requests to Blofin's `/api/v1/account/positions` multiple times
per second in what looks like a tight retry loop without backoff — this
predates this session's changes (confirmed via logs and by testing the
`/accounts/{id}/positions` and `/accounts/{id}/mark-price/{symbol}` endpoints
directly, both already timing out before any of tonight's edits). It's
likely why open-position PnL (`/ws/pnl` `positions` map) and now pending-order
mark price both intermittently show stale/null values — the executor itself,
not this feature, is the bottleneck. Worth a dedicated investigation into
`order-executor`'s blofin polling loop and health-check failure.
