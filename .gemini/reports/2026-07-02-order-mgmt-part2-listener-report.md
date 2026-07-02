# Active Order Management — Part 2: order-listener pending-limit handling

Scope: `order-listener` only (per prompt). All three phases complete and verified live against
Blofin demo (`blofin-blofin-demo-v5vr`, strategy `hype-test-7db4`, HYPE-USDT). MMR/safety-SL logic
(`config.py`, `compute_guaranteed_sl`) was not touched — out of scope, already tracked separately in
`docs/ROADMAP.md`.

## Summary of changes

- `app/webhook_handler.py`:
  - `_update_order_status`: now also persists `account_id` on the `orders` row (previously the
    parameter was threaded through but never written to that column — needed so the reconciler can
    resolve an account per pending order without an extra join surprise).
  - Extracted the top-up/create decision (previously inlined in `_process_order`) into a new
    `_apply_position_fill()` so both the synchronous fill path and the reconciler's async
    fill-detection path materialize a position identically, with no duplicated logic.
  - `_process_order`: position create/top-up is now gated on `result.status == "filled"` instead of
    `result.success` (a resting limit has `success=True, status="pending"` — it must not create a
    phantom position). The signal-log outcome now distinguishes `"pending"` from `"filled"` instead
    of collapsing both into `"filled"`. Market-order behavior is unchanged: market orders always
    return `status="filled"`, so this path executes exactly as before.
  - New routes: `GET /strategies/{id}/orders`, `POST /strategies/{id}/orders/cancel`,
    `POST /strategies/{id}/orders/amend`.
- `app/executor_client.py`: added `get_account_open_orders` (mirrors `get_account_positions`'s
  UNKNOWN-safety contract: `None` means unreachable, never conflate with an empty list),
  `call_executor_cancel_order`, `call_executor_amend_order`.
- `app/reconciler.py`: new `_reconcile_pending_orders()`, called unconditionally at the top of
  `reconcile_once` (so it still runs even when there are zero open `strategy_positions` rows, e.g. a
  strategy's very first order). Detects fills (materializes the position + reapplies TP/SL) and
  cancels/expirations (marks the order row `cancelled`), with the same UNKNOWN-safety contract as
  the existing position-reconcile loop.
- `db/init.sql` / migrations: **no schema change was needed** — `orders.status` already accepted
  arbitrary varchar values (`pending`/`filled`/`cancelled` all fit), and every column Phase 1
  needed (`account_id`, `exchange_order_id`, `price`, `size`, `tp_price`, `sl_price`) already existed.

Full listener test suite: `47 passed`, 3 pre-existing failures unrelated to this change (see
"Pre-existing test failures" below).

---

## Phase 1 — No phantom position on a pending limit

### Resting limit order: no phantom position created

Sent a limit order for strategy `hype-test-7db4` (HYPE-USDT, Blofin demo) far from market:

```
$ docker compose exec -T order-listener curl -s -X POST "http://localhost:8001/webhook/hype-test-7db4" \
  -d '{"base_asset":"HYPE","quote_asset":"USDT","side":"sell","order_type":"limit","size":"3",
       "price":"90","signal":"open_short","leverage":10,"margin_mode":"isolated",
       "timestamp":"2026-07-02T18:00:00Z","token":"a358dde02769b0482d121843e1a2cd94",
       "signal_source":"manual-test"}'
{"order_id":"3182d979-30df-4cb6-b8e0-92e7068ea00d","status":"received","message":"OK"}
```

`orders` row — `status='pending'`, `exchange_order_id` populated, `account_id` persisted:

```
 id                                   | status  | exchange_order_id | price |    size    |       account_id        |   signal
 3182d979-30df-4cb6-b8e0-92e7068ea00d | pending | 1000131534253     |    90 | 2.22222222 | blofin-blofin-demo-v5vr | open_short
```

Executor confirms the order resting:

```
$ docker compose exec -T order-executor curl -s "http://localhost:8004/accounts/blofin-blofin-demo-v5vr/orders"
[{"order_id":"1000131534253","symbol":"HYPE-USDT","side":"sell","price":90.0,"size":2.2,
  "filled_size":0.0,"status":"resting","created_at_ms":1783015113580}]
```

No `strategy_position` row references this order, and the strategy's existing open position
(3.1 HYPE short) is untouched:

```
$ psql ... "SELECT id, strategy_id, symbol, side, size, entry_price FROM strategy_positions
            WHERE strategy_id='hype-test-7db4' AND status='open';"
                  id                  |  strategy_id   |  symbol   | side  |         size          | entry_price
 9be7efbf-2af2-4356-82f2-90ad4fa3c674 | hype-test-7db4 | HYPE-USDT | short | 3.1000000000000000000 |      65.165
(1 row)

$ psql ... "SELECT * FROM strategy_positions WHERE opening_order_id='3182d979-...';"
(0 rows)
```

### Market order: still creates/tops-up a position exactly as before

```
$ docker compose exec -T order-listener curl -s -X POST "http://localhost:8001/webhook/hype-test-7db4" \
  -d '{"base_asset":"HYPE","quote_asset":"USDT","side":"sell","order_type":"market","size":"1",
       "signal":"open_short","leverage":10,"margin_mode":"isolated",
       "timestamp":"2026-07-02T18:00:00Z","token":"a358dde02769b0482d121843e1a2cd94",
       "signal_source":"manual-test"}'
{"order_id":"21f10d0f-6f2e-41f7-a878-b48aa814ce17","status":"received","message":"OK"}

$ psql ... "SELECT status, exchange_order_id, actual_fill_price, account_id FROM orders WHERE id='21f10d0f-...';"
 status | exchange_order_id | actual_fill_price |       account_id
 filled | 1000131534272     |            65.974 | blofin-blofin-demo-v5vr

$ psql ... "SELECT size, entry_price FROM strategy_positions WHERE strategy_id='hype-test-7db4' AND status='open';"
         size           |          entry_price
 4.1000000000000000000  | 65.36231707317073170731707317
```

Position topped up 3.1 → 4.1 with correctly blended entry price, exactly as before this change.
(Reverted the 1.0 test increment afterward via a `close_short` market signal, restoring the
position to 3.1 — pure test hygiene, unrelated to the code path itself.)

**Phase 1 gate: confirmed.** Resting order `3182d979-...` (exchange id `1000131534253`) left in
place for Phase 2.

---

## Phase 2 — Detect fills and reap cancels (reconciler)

### Investigation: does each exchange attach TP/SL before the parent order fills?

Both adapters send tp/sl fields as part of the same placement request (a Part 1 behavior, unchanged
here) — but do those triggers actually go *live* before the parent fills?

**Blofin:** placed the Phase-1 resting order with `sl_price=98.1` (auto-injected guaranteed SL).
While still resting, `list_trigger_orders("HYPE-USDT")` showed **no trigger at 98.1** — only two
older ones (`71.9`, `71.06`) belonging to *already-filled* orders on the same position. Confirms:
Blofin's inline `slTriggerPrice`/`tpTriggerPrice` only becomes an active trigger once that specific
order fills, and — importantly — each filled order gets its **own separate** trigger sized to just
that order's contracts, not the whole position (Blofin's trigger placement is not position-aware).

**Hyperliquid:** placed a probe limit (ETH-USDT, tp=2500, sl=800) directly via order-executor. The
placement ack showed the child legs as `"waitingForFill"` (not yet resting), but
`frontendOpenOrders`/`list_trigger_orders` immediately showed **both children already live** with
real oids, linked under the parent's `children` array, sized to the *original requested* 0.02 —
i.e. HL pre-stages the TP/SL at placement time, but sized to the full order, not any eventual
partial-fill amount.

```
HL frontendOpenOrders excerpt — parent (oid 55874260856) with two Trigger children:
  child sl: {"oid":55874260858,"triggerPx":"800.0","sz":"0.02","orderType":"Stop Market", ...}
  child tp: {"oid":55874260857,"triggerPx":"2500.0","sz":"0.02","orderType":"Take Profit Market", ...}
```//(cancelling the parent auto-cancelled both children — confirmed empty after cancel.)

**Conclusion:** neither exchange guarantees the eventual position's stops are correctly sized
without help — Blofin needs the trigger created post-fill at all (it doesn't exist pre-fill and
would only cover the individual fill, not the aggregate position), and HL's pre-staged legs are
sized to the *requested* size which could be wrong under a partial fill. So `_reconcile_pending_orders`
unconditionally calls the existing `modify-stops` executor route after materializing any fill —
cheap, idempotent, and it consolidates/corrects sizing regardless of which exchange placed it.

### Fill detection: materialize position, correct entry price, TP/SL active

Simulated "the market moved and filled a resting order" by amending the Phase-1 order's price
through order-executor directly (not through the listener — representing genuine external price
movement, since the reconciler must detect an exchange-side fill it didn't cause synchronously):

```
$ docker compose exec -T order-executor curl -s -X POST \
  ".../accounts/blofin-blofin-demo-v5vr/orders/amend" \
  -d '{"symbol":"HYPE-USDT","order_id":"1000131534253","new_price":60}'
{"success":true,"order_id":"1000131534426","cancelled_order_id":"1000131534253", ...}

$ docker compose exec -T order-executor curl -s ".../accounts/blofin-blofin-demo-v5vr/orders"
[]   # the amended replacement crossed the book and filled immediately

$ docker compose exec -T order-executor curl -s ".../accounts/blofin-blofin-demo-v5vr/positions"
[{"symbol":"HYPE-USDT","side":"short","size":"5.30","entry_price":"65.721657156005522319", ...}]
```

Before reconcile: `orders` row still `pending`, `strategy_positions` still 3.1 (listener hasn't
seen the fill yet). Ran a reconcile pass:

```
$ docker compose exec -T order-listener curl -s -X POST "http://localhost:8001/reconcile"
{"success":true,"message":"Reconcile pass complete"}
```

Result — order materialized correctly:

```
$ psql ... "SELECT status, exchange_order_id, actual_fill_price, size FROM orders WHERE id='3182d979-...';"
 status | exchange_order_id |       actual_fill_price        |    size
 filled | 1000131534253     | 66.22799999999999999909866964  | 2.22222222

$ psql ... "SELECT size, entry_price FROM strategy_positions WHERE strategy_id='hype-test-7db4' AND status='open';"
         size           |      entry_price
 5.3000000000000000000  | 65.721657156005522319    -- exact match to the live exchange position
```

Reconciler log:

```
reconciler: pending order 3182d979-... (HYPE-USDT short) FILLED fill_size=2.2 fill_price=66.228
reconciler: post-fill modify-stops for order 3182d979-... (HYPE-USDT short): success=True
```

TP/SL confirmed active and consolidated — before this fill there were two fragmented triggers
(`sz=10`, `sz=31` contracts, from earlier separate fills); after the reconciler's modify-stops call:

```
$ list_trigger_orders("HYPE-USDT")
[{"oid":"10002460299","tpsl":"sl","triggerPx":"98.100000000000000000","sz":"53"}]
```

One trigger, correctly sized to the full confirmed position (53 contracts = 5.3 HYPE), at the
order's own `sl_price` (98.1).

### Cancel/expire detection: order marked cancelled, no position

Placed a second resting limit, then cancelled it directly on the exchange (external cancel):

```
$ docker compose exec -T order-listener curl -s -X POST ".../webhook/hype-test-7db4" \
  -d '{"base_asset":"HYPE","quote_asset":"USDT","side":"sell","order_type":"limit","size":"2",
       "price":"95","signal":"open_short", ...}'
{"order_id":"18e5ed64-5220-4cfa-a9f1-8530565dfebf", ...}   # -> orders.status='pending'

$ docker compose exec -T order-executor curl -s -X POST ".../orders/cancel" \
  -d '{"symbol":"HYPE-USDT","order_id":"1000131534496"}'
{"success":true, ...}

$ docker compose exec -T order-listener curl -s -X POST "http://localhost:8001/reconcile"
{"success":true,"message":"Reconcile pass complete"}

$ psql ... "SELECT status FROM orders WHERE id='18e5ed64-...';"
 status
 cancelled

$ psql ... "SELECT size, entry_price FROM strategy_positions WHERE strategy_id='hype-test-7db4' AND status='open';"
 size: 5.3   -- unchanged, no growth
```

Reconciler log: `reconciler: pending order 18e5ed64-... (HYPE-USDT short) CANCELLED (no matching
fill found on exchange)`.

### UNKNOWN safety

Placed a third resting order, then stopped `order-executor` and ran a reconcile pass:

```
$ docker compose stop order-executor
$ docker compose exec -T order-listener curl -s -X POST "http://localhost:8001/reconcile"
{"success":true,"message":"Reconcile pass complete"}

reconciler: open-orders UNKNOWN for account blofin-blofin-demo-v5vr — leaving 1 pending order(s) untouched this pass
reconciler: positions UNKNOWN for account blofin-blofin-demo-v5vr (exchange/executor unreachable) — leaving its 2 open position(s) untouched this pass
reconciler: positions UNKNOWN for account hyperliquid-hyperliquid-hqdy (exchange/executor unreachable) — leaving its 1 open position(s) untouched this pass

$ psql ... "SELECT status FROM orders WHERE id='dc4bff95-...';"
 status
 pending    -- untouched, as required
```

Restarted `order-executor` (confirmed healthy), then cancelled and reconciled that order normally
to clean up.

**Phase 2 gate: confirmed.** `./scripts/redeploy.sh order-listener` succeeded and the service is
healthy after each change.

---

## Phase 3 — Management routes (listener → executor proxy)

Full lifecycle against `hype-test-7db4` / Blofin demo:

```
$ docker compose exec -T order-listener curl -s -X POST ".../webhook/hype-test-7db4" \
  -d '{"...","order_type":"limit","size":"2","price":"95","signal":"open_short", ...}'
{"order_id":"c9869f35-d1df-405b-998c-9fbb5610b366", ...}
# orders row: status=pending, exchange_order_id=1000131534568, price=95, size=2

$ docker compose exec -T order-listener curl -s "http://localhost:8001/strategies/hype-test-7db4/orders"
[{"order_id":"1000131534568","symbol":"HYPE-USDT","side":"sell","price":95.0,"size":2.0,
  "filled_size":0.0,"status":"resting", ...}]

$ docker compose exec -T order-listener curl -s -X POST \
  "http://localhost:8001/strategies/hype-test-7db4/orders/amend" \
  -d '{"order_id":"1000131534568","new_price":92,"token":"a358dde02769b0482d121843e1a2cd94"}'
{"success":true,"order_id":"1000131534569","cancelled_order_id":"1000131534568", ...}

$ psql ... "SELECT status, exchange_order_id, price, size FROM orders WHERE id='c9869f35-...';"
 status  | exchange_order_id | price | size
 pending | 1000131534569     |    92 |    2     -- new id + new price picked up locally

$ docker compose exec -T order-listener curl -s "http://localhost:8001/strategies/hype-test-7db4/orders"
[{"order_id":"1000131534569", ..., "price":92.0, ...}]   -- matches executor truth exactly

$ docker compose exec -T order-listener curl -s -X POST \
  "http://localhost:8001/strategies/hype-test-7db4/orders/cancel" \
  -d '{"order_id":"1000131534569","token":"a358dde02769b0482d121843e1a2cd94"}'
{"success":true,"oid":"1000131534569"}

$ psql ... "SELECT status, exchange_order_id FROM orders WHERE id='c9869f35-...';"
 status    | exchange_order_id
 cancelled | 1000131534569

$ docker compose exec -T order-listener curl -s "http://localhost:8001/strategies/hype-test-7db4/orders"
[]
```

Auth posture verified (matches `adjust-stops`'s token gating — a control/mutating route, unlike the
read-only `GET .../orders` list which needs no token, same as other query endpoints in this codebase):

```
$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST ".../orders/cancel" -d '{"order_id":"123","token":"wrong-token"}'
{"detail":"Invalid token"}
HTTP_STATUS:403
```

**Phase 3 gate: confirmed.** No leftover resting test orders on either exchange:

```
$ docker compose exec -T order-executor curl -s ".../accounts/blofin-blofin-demo-v5vr/orders"
[]
$ docker compose exec -T order-executor curl -s ".../accounts/hyperliquid-hyperliquid-hqdy/orders"
[]
$ psql ... "SELECT count(*) FROM orders WHERE status='pending';"
 count
 0
```

---

## Deploy verification (all phases)

```
$ ./scripts/redeploy.sh order-listener
...
✓ order-listener redeployed.

$ docker compose ps order-listener
matp-order-listener-1   ...   Up (healthy)

$ docker compose exec -T order-listener curl -s "http://localhost:8001/health"
{"status":"ok","service":"order-listener"}

$ docker compose exec -T order-listener python3 -m pytest tests/ -q
47 passed, 3 pre-existing failures (see below)
```

Final DB state after all testing — positions consistent, no orphaned pending rows:

```
$ psql ... "SELECT strategy_id, symbol, side, size, entry_price FROM strategy_positions WHERE status='open';"
     strategy_id     |  symbol   | side  |          size          |      entry_price
 tv-btc-test-hl-94e1 | BTC-USDT  | short |                   0.02 |               61693.0
 sui-manual-59d9     | SUI-USDT  | long  | 142.000000000000000000 |                0.6951
 hype-test-7db4      | HYPE-USDT | short |  5.3000000000000000000 | 65.721657156005522319
```

(`hype-test-7db4`'s position legitimately grew from 3.1 → 5.3 across this session: the Phase 1
market-order test added 1.0 and was reverted, and the Phase 2 fill-detection test added a real
confirmed 2.2 fill that was intentionally left in place as the demonstrated end state — not
reverted, since it represents a correctly-reconciled real fill, not test debris.)

## Pre-existing test failures (unrelated to this change)

`test_valid_token_passes_auth`, `test_quote_variant_accepted_when_flag_on`, and
`test_daily_signal_cap_returns_429` fail both before and after this change, with an identical error:
`"Cannot size open for strategy test_strategy_1: no webhook price and exchange mark price
unavailable for BTC-USDT"`. This guard (lines ~617–638 in `webhook_handler.py`, part of the
existing margin-per-trade/guaranteed-SL feature) calls `get_mark_price`, which these three tests
don't mock — the failure happens before any code touched in this Part 2 change even executes.
Confirmed this is pre-existing and not introduced here: same 3 failures / same 47 passes both
immediately after Part 1's deploy and after every subsequent redeploy in this session. Left
unfixed per scope ("do not investigate, refactor, or change anything outside the stated task").

## Explicitly out of scope (per prompt)

`order-executor`, `ai-signal-generator`, and the dashboard were not touched. `config.py` and
`compute_guaranteed_sl` were not touched. The broader token-gating-of-control-endpoints question
(e.g. `close_position_by_id` currently has no token check at all, unlike `adjust-stops`) is a
separate ROADMAP item and was not addressed here — Phase 3's new routes use the stronger existing
posture (token-gated, matching `adjust-stops`) so as not to weaken it.
