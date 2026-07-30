# BTC position PnL audit, entry-side fill size, and the Hyperliquid question

**Date:** 2026-07-30
**Follows:** `.gemini/reports/2026-07-30_fill_size_adapter_and_close_order_backfill.md`

Asked for: audit how PnL is reported on the last BTC position, then fix the two loose ends
from the previous report (Hyperliquid's close path, and entry-side rounding).

---

## 1. PnL on the last BTC position — it reconciles exactly

Position `7b023a64` (`social-btc-astro`, BTC-USDT long), closed manually 14:56:57 at
64,651.9.

```
          received_at          |   signal   |   size     | actual_fill_price |  pnl     | exchange_fee | source
-------------------------------+------------+------------+-------------------+----------+--------------+---------
 2026-07-29 20:15:42.018183+00 | open_long  | 0.00314564 |           63579.5 | 0        | 0.11825787   | social_listener
 2026-07-30 12:14:03.822+00    | close_long | 0.0023     |           64932.3 | 3.111440 | 0.089606574  | manual
 2026-07-30 14:56:57.854492+00 | close_long | 0.0008     |           64651.9 | 0.857920 | 0.031032912  | manual
```

```
 gross_from_orders |  all_fees   | net_computed | position_pnl_realized
-------------------+-------------+--------------+-----------------------
       3.969360000 | 0.238897356 |  3.730462644 |           3.730462644
```

The model is consistent and correct:

* **Order rows carry the RAW per-leg gross pnl.** Each leg checks out by hand:
  `0.0023 × (64932.3 − 63579.5) = 3.11144` and `0.0008 × (64651.9 − 63579.5) = 0.85792`.
* **The position carries the NET**, gross minus every fee on the position including the
  earlier partial's — 3.969360 − 0.238897356 = **3.730462644**, matching to the last digit.
* The two close legs sum to 0.0031, the position's real size.

### One thing that does NOT reconcile — flagged, not fixed

The strategy's booked total does not equal the sum of its positions:

```
 strategies.pnl_total (social-btc-astro) : 5.099169
 SUM(pnl_realized) over its 3 closed pos : 6.971835   (-0.240561 + 3.481933 + 3.730463)
 gap                                      : 1.872666
```

The gap is exactly the shortfall on one position, `75c43386` (the short closed 2026-07-29):
its `pnl_realized` reads 3.481933, but arithmetic on the strategy total implies only
1.609267 was ever booked. A partial close never books to the strategy — only the final
close does — so a position whose `pnl_realized` is later corrected by a different mechanism
leaves the strategy total behind. **This is inference from the arithmetic, not a traced
code path**, and it is a third defect outside the scope of this task. Not touched.

---

## 2. Backfill was incomplete — corrected

The previous report claimed zero gaps. That check was wrong: it looked for positions with
**no close order at all**, so a position missing only its **final** close leg — while
retaining its partials — passed silently. Three more were found:

```
                  id                  |   strategy_id    |  symbol  |           closed_at           | final_leg_size | close_orders | final_close_orders
--------------------------------------+------------------+----------+-------------------------------+----------------+--------------+--------------------
 75c43386-f13e-4cca-bafc-9ae50aeb8769 | social-btc-astro | BTC-USDT | 2026-07-29 20:10:58.15892+00  |     0.00029063 |            4 |                  0
 3f37a298-4810-41a4-a60f-18a53d1bde45 | sui-manual-59d9  | SUI-USDT | 2026-07-14 21:19:43.76953+00  |            113 |            1 |                  0
 42045ebc-fba5-44bb-a0d4-102c6bf7fb27 | tv_test_harness  | BTC-USDT | 2026-07-06 10:39:52.436291+00 |          0.004 |            2 |                  0
```

Backfilled from each position's own `size` / `closing_price` / `closed_at`, `pnl` and
`exchange_fee` left NULL for the same reason as before (the gross is unrecoverable and
`orders.pnl` is gross while `pnl_realized` is net). Both checks now clean:

```
 no_close_order_at_all | no_final_close_order
-----------------------+----------------------
                     0 |                    0
```

---

## 3. Hyperliquid — no fix needed, my earlier caveat was wrong

The previous report speculated that Hyperliquid might have the same gap. It does not.
`close_position()` delegates to `_place_order()`, which already sets the field from the
exchange's own `totalSz` (`adapters/hyperliquid.py:595-606`):

```python
ts = filled.get("totalSz")
if ts not in (None, "", "0"):
    actual_fill_size = Decimal(str(ts))
else:
    actual_fill_size = Decimal(str(size_rounded)) if order_status == "filled" else None
```

No change made. Blofin was the only venue with the hole.

---

## 4. Entry-side rounding — fixed

`strategy_positions` has used the exchange-confirmed fill size since the fill-size work
(`_materialize_fill`: *"Use exchange-confirmed fill size if the caller supplied one"*), but
the **order row** was never updated after the fill: `_update_order_status()` wrote
`actual_fill_price`, `pnl`, `exchange_fee` — and left `size` at the request.

That is why the BTC entry above reads `0.00314564` against a real 0.0031 position, and why
its close legs (0.0023 + 0.0008 = 0.0031) sum to less than its own entry order.

`_update_order_status()` now also sets `size = COALESCE($10, size)`, supplied only on a
confirmed fill (`status == "filled"` and a non-empty `actual_fill_size`), so rejected and
still-pending orders are untouched.

### Live verification — `hype-test-7db4`, enabled for the test and disabled again

Opened with **2.37 HYPE**, deliberately off the instrument's 0.1 step:

```
STEP 1 — open long 2.37 HYPE (off the 0.1 lot step)
  requested              : 2.37
  exchange position      : 2.40
  strategy_positions.size: 2.4000000000000000000
  orders.size (entry)    : 2.4000000000000000000   status=filled

STEP 3 — entry vs closes for this position
  open_long    size=2.4  fill=53.929 pnl=0
  close_long   size=2.4  fill=53.906 pnl=-0.0552
  entry total=2.4  close total=2.4

=== VERDICT ===
  [OK] order_size_is_fill_not_request
  [OK] order_size_matches_exchange
  [OK] order_size_matches_position
  [OK] flat_on_exchange
  [OK] entry_equals_sum_of_closes
```

Requested 2.37, filled 2.40, and the order row, the position and the exchange now all read
2.4. The entry total equals the close total — the invariant that was broken before.

### Final state

```
hype-test-7db4 | enabled = f
exchange positions: []
open positions in DB: 0
```

---

## Not covered

* **The strategy-level PnL gap (1.872666)** described in §1. Real, reproducible from the
  data, cause inferred rather than traced. Needs its own investigation.
* **Historical entry orders keep their requested sizes.** The fix applies to new fills
  only. Correcting old rows would mean inferring each fill from the position size, which is
  unsafe for positions built from several entries — not attempted.
* Only Blofin and Hyperliquid were examined; no other venue adapters exist in this repo.
