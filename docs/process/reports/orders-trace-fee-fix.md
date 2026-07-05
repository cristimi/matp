# Fix: surface AI reasoning/confidence + fees on ALL orders, instrument listener→executor hop

Date: 2026-07-05

## Root cause (recap)

`ai_reasoning`, `ai_confidence`, and `exchange_fee` reached the dashboard only through
`orders → order_execution_log (oel) → signal_log`. `order_execution_log` rows are written
only by the executor's `/execute` (open) path — the close path (`/close-position`) never
writes an OEL row — so every close/partial-close order showed blank reasoning, confidence,
and fee. Separately, `OrderResult` had no `fee` field at all, so opens never populated
`exchange_fee` either (stayed 0/NULL).

Fix (approach B): give `orders` its own `signal_log_id` and `exchange_fee` columns,
populated at order-record time for every order (open, close, rejected, reconciler
synthetic), and have the dashboard read those directly instead of depending on the
open-only OEL join.

## Phase 1 — Migration 043

Added nullable `signal_log_id bigint` and `exchange_fee numeric` to `public.orders`, plus
an index on `signal_log_id`. Forward-only, no backfill.

```
docker compose exec -T postgres psql -U matp -d matp < db/migrations/043_orders_signal_fee.sql
BEGIN
ALTER TABLE
CREATE INDEX
COMMIT
NOTICE:  Migration 043 verified OK: orders.signal_log_id and orders.exchange_fee present, index in place
DO
```

```
docker compose exec postgres psql -U matp -d matp -c "\d public.orders"
 ...
 signal_log_id      | bigint                   |           |          |
 exchange_fee       | numeric                  |           |          |
Indexes:
    ...
    "idx_orders_signal_log_id" btree (signal_log_id)
```

`db/init.sql` was **not** regenerated — it has been frozen at migration 037 since commit
`1c5cbcb` (migrations 038–042 aren't reflected there either), so 043 follows the same
established pattern rather than a one-off regen.

## Phase 2 — Populate `signal_log_id` + `exchange_fee` in order-listener

`_log_order` is the single `INSERT INTO orders` in `webhook_handler.py`, called once per
webhook for **both** opens and closes (`_process_order` handles both signal families from
the same order row) — so one change covers every webhook-driven order. It now writes
`signal_log_id` from the webhook's own `signal_log` row.

`_update_order_status` (opens and closes) and `close_strategy_position`'s webhook-driven
branch now thread `exchange_fee` from the executor's result back onto the order row.
`executor.py`'s own `_update_order_record`/`_update_execution_log` (a second, redundant
writer to the same `orders`/`order_execution_log` rows on the open path) were updated too,
for consistency between the two writers.

Reconciler's two synthetic-close `INSERT`s (`_handle_full_external_close`,
`_recover_manual_close_pnl`) now set `exchange_fee` from `get_closed_position_details`
when the exchange exposes it; `signal_log_id` is left NULL there — no AI decision to link
for an exchange-driven close.

**Finding:** checked whether `orders.signal_metadata` already carries reasoning/confidence
for close orders before building the FK — it does, for AI-sourced closes:

```
docker compose exec postgres psql -U matp -d matp -c "
SELECT id, signal, signal_source, signal_metadata FROM orders
WHERE signal IN ('close_long','close_short') AND signal_source='ai_engine'
ORDER BY received_at DESC LIMIT 1;"
 id | signal | signal_source | signal_metadata
17819b6d-... | close_short | ai_engine | {"dry_run": false, "reasoning": "The original thesis for a
  confirmed downside breakout is invalidated...", "confidence": 0.7, "template_id":
  "geometric_range", "trigger_reason": "scheduled"}
```

The webhook body itself already carries `reasoning`/`confidence` for AI-driven closes.
Per the agreed approach B, `signal_log_id` is still implemented as the canonical FK —
one consistent join for dashboard-api regardless of order type, rather than reading
`signal_metadata` for closes and `signal_log` for opens.

## Phase 3 — Fee capture in order-executor (both exchanges)

`OrderResult` gained `fee: Optional[Decimal] = None`.

### Blofin — proof + a pre-existing bug found along the way

Fetched a real order's `orders-history` entry via the running adapter:

```
docker compose exec order-executor python3 -c "... adapter._get_order_details('BTC-USDT', '1000131653258') ..."
{
  "orderId": "1000131653258", "fee": "0.150624", "pnl": "-0.3298",
  "averagePrice": "62760", "state": "filled", ...
}
```

`fee` is a plain positive magnitude (cost), same units as price/pnl fields already parsed
from this same response. While proving this, found that **`_get_order_details` was
ignoring the `orderId` it queried for** — Blofin's demo `orders-history`/`fills-history`
API doesn't reliably filter server-side by `orderId`; it just returns the account's recent
order list for the instrument, and the adapter unconditionally took `items[0]`:

```
docker compose exec order-executor python3 -c "..."
--- queried oid=1000131651876 -> returned orderId=1000131653258 fee=0.150624 ...
--- queried oid=1000131651512 -> returned orderId=1000131653258 fee=0.150624 ...
--- queried oid=1000131650390 -> returned orderId=1000131653258 fee=0.150624 ...
```

Three different requested order IDs all returned the same (most recent) entry. This is a
pre-existing bug, independent of the fee work — it means `actual_fill_price` and
`realized_pnl` were already at risk of misattribution whenever a newer order landed on the
same instrument within the ~1–2s post-placement lookup window. Flagged to the operator,
who approved fixing it as part of this change (see below) rather than just reusing the
broken helper for fee too.

**Fix applied:** `_get_order_details` now matches the requested `orderId` against the
returned `items` list itself and returns `{}` if no match is found, instead of trusting
`items[0]`. Fee (and, incidentally, price/pnl) extraction was added at every call site that
already fetches this `details` dict: `submit_order` (open, market and limit/resting-filled
branches), `close_position` (full close), `_partial_close`. `get_closed_position_details`
(used by the reconciler) now also returns a raw `fee` key alongside its existing
`pnl_realized = pnl + fee` fold — that fold is unchanged, fee is exposed additionally, not
double-counted.

**Side effect of the fix, quantified:** because the fix now returns nothing rather than a
wrong order's data when no ID match exists, and because `exchange_order_id` itself often
comes back NULL from Blofin's close/partial-close response (a separate, pre-existing gap —
not something this change touches), fewer Blofin close orders will show a fill price/pnl/fee
than before. This is strictly more correct (no fabricated numbers), not a regression:

```
docker compose exec postgres psql -U matp -d matp -c "
SELECT count(*) FILTER (WHERE exchange_order_id IS NULL) AS null_oid,
       count(*) FILTER (WHERE exchange_order_id IS NOT NULL) AS has_oid, count(*) AS total
FROM orders WHERE signal IN ('close_long','close_short')
  AND account_id='blofin-blofin-demo-v5vr' AND received_at > NOW() - INTERVAL '3 days';"
 null_oid | has_oid | total
       10 |       8 |    18
```

10/18 (55%) of Blofin closes over the prior 3 days already had a NULL `exchange_order_id`
before any of today's changes — confirming this is pre-existing and out of scope for this
fix. **Blocker/follow-up:** why Blofin's close-position/partial-close response frequently
omits a usable `orderId` is a separate investigation (not started here).

### Hyperliquid

Fetched real `userFills` entries via the running adapter:

```
docker compose exec order-executor python3 -c "..."
{"coin": "BTC", "px": "62688.0", "sz": "0.00733", "side": "A", "dir": "Open Short",
 "closedPnl": "0.0", "oid": 55959766813, "fee": "0.206776", "feeToken": "USDC", ...}
matching oid=55959766813: [ {..., "fee": "0.206776", ...}, {..., "fee": "0.357415", ...} ]
```

`fee` is a plain positive magnitude in `feeToken` (USDC, matching account currency);
partial fills for one order id must be summed (confirmed: a single `oid` had two fill
entries here). Consolidated `_get_fill_pnl` into a new `_get_fill_data(oid)` that fetches
`userFills` once and returns both `pnl` and `fee` summed across matching fills — avoids
doubling an expensive full-history fetch (452 fills on this account) per close. Fee is now
captured for **both** opens and closes (pnl remains close-only, as before).
`get_closed_position_details` also now returns `fee` alongside `pnl_realized`.

## Phase 4 — Dashboard reads `orders` directly

`dashboard-api/src/routes/orders.ts` `/:id/detail`: `signal_log` is now joined via
`sl.id = o.signal_log_id` instead of `oel.signal_log_id`, and `exchange_fee` is read from
`o.exchange_fee` instead of `oel.exchange_fee`. The `order_execution_log` join is kept only
for the open-only execution-panel fields (`requested_price`, `exchange_order_id` from OEL,
`placed_at`, `filled_at`) — those staying blank on closes is correct.

## Phase 5 — Instrument the listener→executor hop

`order-listener/app/executor_client.py`: `call_executor`, `call_executor_close_position`,
`get_account_open_orders`, `get_account_positions` now log elapsed ms and
`type(exc).__name__` + `repr(exc)` on failure, instead of `str(exc)` (which is empty for
e.g. `asyncio.TimeoutError` — the exact gap that made a prior 502 burst un-root-causeable).
Logging only, no control-flow change.

```
docker compose exec order-listener grep -n "elapsed_ms\|type(e).__name__" /app/app/executor_client.py
41:        elapsed_ms = int((time.monotonic() - start) * 1000)
44:            f"{type(e).__name__}: {e!r}"
55: ... 69: ... 72: ...
173: ... get_account_positions ... 176: ...
202: ... get_account_open_orders ... 205: ...
322: ... close-position timeout ... 325: ...
334: ... close-position failed ... 337: ...
```
(all four target call sites present in the running container; a live timeout wasn't
reproduced on demand, but the logging code is confirmed deployed and live)

## Deploy

Migration 043 applied directly (see Phase 1). Redeployed `order-executor`,
`order-listener`, `dashboard-api` via `./scripts/redeploy.sh <service>` — all three came up
`healthy` with no errors in logs post-redeploy.

## Live verification — real orders, post-deploy

**Open (Blofin, `hype-test-7db4`)** — full pipeline confirmed:

```
docker compose exec postgres psql -U matp -d matp -c "
SELECT id, signal, strategy_id, signal_source, signal_log_id, exchange_fee,
       exchange_order_id, status FROM orders WHERE id='2ba5d22d-29a6-448e-98c6-02ff3fbb1530';"
 2ba5d22d-... | open_short | hype-test-7db4 | tradingview | 215 | 0.12047412 | 1000131655662 | filled

docker compose exec nginx wget -qO- http://dashboard-api:8003/orders/2ba5d22d-29a6-448e-98c6-02ff3fbb1530/detail
{"execution":{"requested_price":null,"exchange_fee":0.12047412,"exchange_order_id":"1000131655662",
 "placed_at":"2026-07-05T13:18:47.827Z","filled_at":"2026-07-05T13:18:51.175Z",
 "actual_fill_price":69.238, ...}}
```

**Close (Hyperliquid, `tv-btc-test-hl-94e1`)** — full pipeline confirmed:

```
docker compose exec postgres psql -U matp -d matp -c "
SELECT id, signal, strategy_id, signal_source, signal_log_id, exchange_fee,
       exchange_order_id, status, actual_fill_price, pnl
FROM orders WHERE id='c479674e-059d-45ae-93b5-6e7ee7bf527f';"
 c479674e-... | close_short | tv-btc-test-hl-94e1 | tradingview | 218 | 0.141399 | 56007722386 | filled | 62844.0 | -0.031

docker compose exec nginx wget -qO- http://dashboard-api:8003/orders/c479674e-059d-45ae-93b5-6e7ee7bf527f/detail
{"execution":{"requested_price":null,"exchange_fee":0.141399,"exchange_order_id":null,
 "placed_at":null,"filled_at":null,"actual_fill_price":62844, ...}}
```
(`exchange_order_id`/`placed_at`/`filled_at` are null here because those three specifically
come from the open-only OEL join, per Phase 4's design — `exchange_fee` and
`signal_log_id`-backed reasoning/confidence are the fields this project changes, and both
resolved correctly for a close.)

**Close (Blofin, `hype-test-7db4`)** — landed but demonstrates the pre-existing gap
documented in Phase 3, not a failure of this change:

```
 b8b866c1-... | close_short | hype-test-7db4 | tradingview | 216 | (null) | (null) | filled
```
`signal_log_id` still resolved (216); `exchange_fee`/`exchange_order_id` are NULL because
Blofin's close-position response didn't return a usable order id for this specific close
(see the 10/18 finding in Phase 3) — there was nothing to fetch fee from, by design of the
new stricter matching. Neither `ai_reasoning` nor `ai_confidence` apply to this run
(`ai_reasoning`/`confidence` were checked on both new orders above and correctly render
`null`, since both are `signal_source='tradingview'`, non-AI signals — consistent with
`signal_log.ai_reasoning` being NULL for non-AI sources).

## Known follow-up (not fixed here, flagged for separate triage)

Why Blofin's close-position/partial-close response frequently omits a usable `orderId`
(≈55% of recent closes) — separate from the `orders-history` matching bug fixed in Phase 3,
and out of scope for this fee/reasoning fix.
