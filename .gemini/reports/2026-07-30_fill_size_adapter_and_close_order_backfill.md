# Record the size that filled, backfill missing close orders, and prove both live

**Date:** 2026-07-30
**Follows:** `.gemini/reports/2026-07-30_close_orders_missing_from_tree.md`
**Services changed:** `order-executor` (Blofin adapter), `order-listener`

Three pieces of work: fix the adapter so the *filled* size comes back, backfill every
close that had no order row, then test the whole path end to end on a live (demo)
position using `hype-test-7db4`, which was disabled again afterwards.

---

## 1. The adapter fix

`OrderResult` has always had an `actual_fill_size` field, and `_parse_fill_size()` already
existed and was already used by the entry path. Blofin's `_partial_close()` simply never
called it — it returned only fill price, pnl and fee. So `close_strategy_position()` had
nothing to record but its own request.

That is why the exchange rounds a request to its lot step and the DB never hears about it:
asking to close 0.002325 BTC (2.325 contracts, lot 0.1) really closes 0.0023. This morning
that left `strategy_positions.size` at 0.000775 against the exchange's 0.0008 and tripped a
`reconcile_divergent` flag the reconciler will never heal, because it refuses to grow a
position back.

* `order-executor/app/adapters/blofin.py` — `_partial_close()` now calls
  `_parse_fill_size(symbol, details, str(order_size))` and returns it as
  `actual_fill_size`.
* `order-listener/app/webhook_handler.py` — `close_strategy_position()` prefers
  `actual_fill_size` over the request, **for a partial reduce only**. On a full close the
  position is zeroed regardless, and a short fill there is something to surface rather than
  silently adopt as the new truth. An unparseable value falls back to the request with a
  warning.

---

## 2. Backfill

Scope was smaller than expected. Closed positions with no close order row:

```
 closed_positions | with_no_close_order
------------------+---------------------
              169 |                   1
```

The one was `3cb26cf7` (`sui-manual-59d9`, SUI-USDT long, 142 @ 0.6994 → 0.7542, closed
2026-07-03). Backfilled from the position's own recorded close:

```
                  id                  |          received_at          |  symbol  |   signal   |  size  | actual_fill_price | pnl | signal_source
--------------------------------------+-------------------------------+----------+------------+--------+-------------------+-----+---------------
 fa1205c6-1ea0-4eb7-b343-4853aebe9832 | 2026-07-03 15:36:54.545119+00 | SUI-USDT | close_long |    142 |            0.7542 |     | backfill

 closed_with_no_close_order
----------------------------
                          0
```

**`pnl` and `exchange_fee` were deliberately left NULL.** `strategy_positions.pnl_realized`
is NET of both fees while `orders.pnl` is the RAW exchange figure, and the close fee for
that trade is not recorded anywhere — so the gross is not recoverable. Writing the net into
a gross column would have quietly corrupted the reconciler's `prior_close_pnl` sum, which
only counts rows `WHERE pnl IS NOT NULL`; a NULL row is correctly excluded. The realized
figure remains visible on the position itself. `signal_source='backfill'` marks the row as
reconstructed rather than observed.

Open positions were also checked for unexplained size gaps — only `7b023a64` shows one
(0.00004564), and that is entry-side rounding on the opening order, not a missing close.

---

## 3. Live end-to-end test on `hype-test-7db4`

Strategy enabled for the test, HYPE-USDT on the BloFin demo account, and **disabled again
afterwards**. The partial close deliberately requested **0.77 HYPE** — off the instrument's
0.1 step — so the exchange had to round it.

```
STEP 1 — open long 2.0 HYPE
  HTTP 200: {'order_id': '0058e1fa-...', 'status': 'received', 'message': 'OK'}
  position d43e1ef1-327d-404f-8356-45a9e4e04675 entry=53.668
  [after open] exchange=2.00  db=2.0000000000000000000  match=True

STEP 2 — partial close 0.77 HYPE (off the 0.1 lot step)
  HTTP 200 success=True fill=53.656 pnl=-0.0096 is_full_close=False
  [after partial] exchange=1.20  db=1.2000000000000000000  match=True
  close order: size=0.8 fill=53.656 pnl=-0.0096 source=manual

STEP 3 — full close
  HTTP 200 success=True is_full_close=True
  exchange position after full close: None

STEP 4 — every order the tree can see for this position
  2026-07-30 14:14:44  open_long    buy   size=2.0  fill=53.668 pnl=0       source=tradingview
  2026-07-30 14:14:54  close_long   sell  size=0.8  fill=53.656 pnl=-0.0096 source=manual
  2026-07-30 14:15:06  close_long   sell  size=1.2  fill=53.658 pnl=-0.012  source=manual
  position: status=closed size=1.2 pnl_realized=-0.11503536 close_reason=manual_close

=== VERDICT ===
  [OK] partial_sizes_match
  [OK] close_order_created
  [OK] order_size_is_fill_not_request
  [OK] position_flat_on_exchange
  [OK] position_closed_in_db
```

**The key line is STEP 2.** 0.77 was requested, **0.8 filled**, and both the position size
and the order row recorded 0.8 — exchange 1.20 against DB 1.2000, `match=True`. Under the
old code the DB would have read 1.23 against the exchange's 1.20 and raised the same false
divergence flag as this morning.

The close order row in STEP 2 was created **by the new code**, not by hand — that is the
live proof the previous report could not give.

Tree API for the same position, all three orders with correct types and fees:

```json
[{"time":"...14:14:44.835Z","type":"entry",        "fill":53.668,"delta":2,
  "key":{"avg_fill":53.668,"realized":0,      "fee":0.0644016}},
 {"time":"...14:14:54.957Z","type":"partial-close","fill":53.656,"delta":0.8,
  "key":{"avg_fill":53.656,"realized":-0.0096,"fee":0.02575488}},
 {"time":"...14:15:06.074Z","type":"close",        "fill":53.658,"delta":1.2,
  "key":{"avg_fill":53.658,"realized":-0.012, "fee":0.03863376}}]
```

### Final state

```
hype-test-7db4 | HYPE Test | enabled = f
exchange positions: [ BTC-USDT long 0.0008 ]   <- only the pre-existing social-btc-astro position
```

---

## A false start worth recording

The first attempt used a fixed 4-second wait after the webhook. The webhook returns
`status: received` and fills asynchronously, so the position had not appeared yet, the
script treated it as a failure, and its `finally` block force-closed via the raw executor
endpoint — which bypasses the listener's bookkeeping and left position `87199540` open in
the DB against a flat exchange.

The reconciler healed it without help, which is itself a live check of that path:

```
 87199540-... | closed | 2.0 | 53.616 | -0.124676 | Closed on exchange
```

Test cost of the false start: −0.1247 on the demo account. The retry polls for the
position instead of guessing a sleep.

## Not covered

* **Only the Blofin adapter was fixed.** Hyperliquid's close path was not inspected; if it
  also leaves `actual_fill_size` unset, that venue keeps the old behaviour.
* **A full close still records the requested size**, by design (see above). If a full close
  ever fills short, the position is closed anyway and the divergence surfaces through the
  reconciler rather than being adopted.
* **The one backfilled row carries no pnl or fee**, for the reasons given.
* The opening order for `7b023a64` still records 0.00314564 against a 0.0031 position —
  entry-side rounding, the same class of bug on the open path, not touched here.
