# Every executed close now gets an order row

**Date:** 2026-07-30
**Reported as:** "under each position in the strategy tree it should have all its executed
orders — in this case there is only the enter order."

---

## The gap

The tree lists a position's orders as:

```sql
SELECT COUNT(*) FROM orders o
 WHERE o.id = sp.opening_order_id
    OR o.closes_position_id = sp.id
```
(`dashboard-api/src/routes/strategies.ts:1005`)

So a close with **no order row** is invisible. Position `7b023a64` had 74% taken off at
64,932.3 earlier today and showed only its entry:

```
          received_at          |  signal   | side |    size    | status | actual_fill_price | pnl
-------------------------------+-----------+------+------------+--------+-------------------+-----
 2026-07-29 20:15:42.018183+00 | open_long | buy  | 0.00314564 | filled |           63579.5 |   0
(1 row)
```

`close_strategy_position()` only ever linked an order row when the **caller** supplied one.
Four callers do not:

| caller | reason | had the hole |
|---|---|---|
| `webhook_handler.py:477` manual UI/API close | `manual_close` | yes |
| `webhook_handler.py:193` strategy disabled | `flatten_on_disable` | yes |
| `webhook_handler.py:1911` flip's opposite leg | `flip_close` | yes |
| `reconciler.py:241` partial reduction | `Closed on exchange` | yes |

The webhook path was never affected — it creates its order first and passes the id in. And
`_handle_full_external_close` already wrote a synthetic row for *full* external closes, so
the fix is that same row, moved into the shared routine so the partial and manual paths get
it too.

### A second effect, not just cosmetic

The reconciler computes `prior_close_pnl` as
`SUM(orders.pnl) WHERE closes_position_id = sp.id`. Closes that never got a row were
missing from that sum, so the final synthetic close order was over-attributed. Giving every
close a row corrects that as a side effect.

---

## The change

`order-listener/app/webhook_handler.py`, inside `close_strategy_position()`, before the
position UPDATE: when `closing_order_id is None`, insert a `filled` market order carrying
the close's own fill price, pnl and fee, and use its id for the existing linking step.

* `signal` — `exchange_close` when the reason is `Closed on exchange` (matching the
  reconciler's existing rows), otherwise `close_long` / `close_short`.
* `signal_source` — `manual` / `reconciler` / `system`, derived from the reason.
* A failed insert is logged and swallowed: the close has already executed on the exchange,
  and a missing audit row must not roll it back or report failure.

---

## Verification

The close that had already happened was backfilled from the exchange's own figures (fill
64,932.3, gross pnl 3.11144, fee 0.089606574, size 0.0023):

```
          received_at          |   signal   | side |    size    | status | actual_fill_price |   pnl   |  signal_source
-------------------------------+------------+------+------------+--------+-------------------+---------+-----------------
 2026-07-29 20:15:42.018183+00 | open_long  | buy  | 0.00314564 | filled |           63579.5 |       0 | social_listener
 2026-07-30 12:14:03.822+00    | close_long | sell |     0.0023 | filled |           64932.3 | 3.11144 | manual
(2 rows)
```

Tree position payload — `order_count` is now 2:

```json
{"id":"7b023a64-...","side":"long","size":0.0008,"entry_price":63579.5,
 "sl_price":63580,"tp_price":null,"status":"open","order_count":2, ...}
```

Expanded order list under the position (`GET /positions/{id}/orders`):

```json
[{"id":"0c52fd6a-...","time":"2026-07-29T20:15:42.018Z","type":"entry",
  "fill":63579.5,"delta":0.00314564,"status":"filled",
  "key":{"avg_fill":63579.5,"realized":0,"fee":0.11825787}},
 {"id":"2183b383-...","time":"2026-07-30T12:14:03.822Z","type":"partial-close",
  "fill":64932.3,"delta":0.0023,"status":"filled",
  "key":{"avg_fill":64932.3,"realized":3.11144,"fee":0.089606574}}]
```

The UI classifies it as `partial-close` with the right fill, size, realized pnl and fee.

---

## What is NOT verified, and what is still broken

* **The new code path has not run on a live close.** The row above was backfilled by hand
  to match what the code produces; the code itself fires on the *next* close. Proving it
  needs a real close, which would mean trading the account further than asked. Offered, not
  taken.
* **The recorded size is the REQUESTED size, not the filled size.** Root cause now
  identified: `OrderResult` has an `actual_fill_size` field, but Blofin's `_partial_close`
  (`order-executor/app/adapters/blofin.py:815`) never populates it — it returns only fill
  price, pnl and fee. So `close_strategy_position` falls back to `eff_close_size`. This is
  the same defect that made `strategy_positions.size` read 0.000775 against the exchange's
  0.0008 this morning and raised a false `reconcile_divergent` flag. Fixing it means
  converting the filled contracts back to base with `_to_base()` in the adapter and using
  it in the listener — a separate change to the exchange adapter, not made here.
* **Historical closes are still missing rows.** Only position `7b023a64` was backfilled.
  Other positions closed through these four paths before today remain incomplete in the
  tree; no sweep was run.
