# Strategy-level PnL gap — corrections now book their delta

**Date:** 2026-07-30
**Follows:** `.gemini/reports/2026-07-30_pnl_audit_and_entry_fill_size.md` §1

The previous report flagged that `social-btc-astro` had booked 5.099169 against 6.971835
of closed-position PnL and inferred the cause. This traces it, fixes it, and reconciles
every affected strategy.

---

## Cause — stated in the code itself

`sync_position_pnl()` (`order-listener/app/webhook_handler.py`) recomputes each position's
`pnl_realized` from its orders in two branches:

```python
# (1) First attribution (NULL -> value): set AND book.
...
# (2) Corrections (already-booked, value changed): update only, do NOT re-book.
```

Branch (2) was deliberate — re-booking the whole figure would double-count it — but the
consequence is that a position whose PnL is revised **after** its first booking updates the
position row and leaves `strategies.pnl_total` on the old number, permanently.

`capital_allocation` is derived from that total (`base + pnl_total`), and capital
allocation feeds position sizing and the drawdown stop. So the error was not cosmetic.

`social-btc-astro` position `75c43386` is the worked example: booked ≈1.609267 at close, a
later correction moved its `pnl_realized` to 3.481933, and the strategy kept the old value
— adrift by exactly the 1.872666 difference.

### It was fleet-wide, and bidirectional

```
             id             | booked_total | sum_positions |    gap
----------------------------+--------------+---------------+-----------
 bnb-ai-scalper-edbb        |   -10.875611 |     -6.738227 | -4.137383
 social-btc-astro           |     5.099169 |      6.971835 | -1.872666
 sol-ai-6486                |   -10.669075 |     -9.508268 | -1.160807
 hype-test-7db4             |   -29.463950 |    -28.593348 | -0.870602
 sui-manual-59d9            |     8.429808 |      7.731495 |  0.698313
 tv_test_harness            |    -4.499686 |     -4.220586 | -0.279100
 tao-ai-range-rotation-d257 |     0.508494 |      0.750200 | -0.241706
 hype-breakout-da2e         |    -9.520277 |     -9.315946 | -0.204331
 eth-ai-34d2                |   -17.984921 |    -17.894887 | -0.090034
```

Nine of twelve. Mostly under-booked (corrections that raised a position's PnL), one
over-booked.

---

## The fix

Branch (2) now books **`new - old`** rather than nothing. The original amount stays booked
exactly once and only the revision moves the total. It remains idempotent through the
`IS DISTINCT FROM` guard: once a value settles, no row is returned and nothing is booked.

Implemented as a CTE so the pre-update value is available in `RETURNING`, plus a log line
per correction naming the position, both values and the delta.

**Deploy safety check** — before deploying, the number of positions whose stored value
differs from the recomputed one:

```
 positions_where_stored_differs_from_computed
----------------------------------------------
                                            0
```

So the change was inert on existing data: it prevents future drift, and could not
retroactively repair the accumulated gap. That needed a separate data fix.

---

## Data reconciliation

One-time transaction setting `pnl_total` to the sum of each strategy's closed positions,
moving `pnl_today` and `capital_allocation` by the same delta, and raising
`allocation_peak` only where the new capital exceeds it.

```
             id             | was_total  | now_total  |   delta   | was_capital | now_capital
----------------------------+------------+------------+-----------+-------------+-------------
 sui-manual-59d9            |   8.429808 |   7.731495 | -0.698313 |  208.429808 |  207.731495
 hype-test-7db4             | -29.463950 | -28.593348 |  0.870602 |  170.536050 |  171.406652
 eth-ai-34d2                | -17.984921 | -17.894887 |  0.090034 |   82.015079 |   82.105113
 tv_test_harness            |  -4.499686 |  -4.220586 |  0.279100 |   95.500314 |   95.779414
 hype-breakout-da2e         |  -9.520277 |  -9.315946 |  0.204331 |   92.879723 |   93.084054
 tao-ai-range-rotation-d257 |   0.508494 |   0.750200 |  0.241706 |  100.508494 |  100.750200
 bnb-ai-scalper-edbb        | -10.875611 |  -6.738227 |  4.137383 |   89.124389 |   93.261773
 sol-ai-6486                | -10.669075 |  -9.508268 |  1.160807 |   89.330925 |   90.491732
 social-btc-astro           |   5.099169 |   6.971835 |  1.872666 |  105.099169 |  106.971835
UPDATE 11
```

After, with drawdown re-checked against each strategy's own stop:

```
             id             |   booked   | positions  |    gap     | capital  |   peak   | drawdown_pct | max_dd | enabled
----------------------------+------------+------------+------------+----------+----------+--------------+--------+---------
 ai-btc-6f8c                |  -1.183249 |  -1.183249 | 0.00000000 |  98.8168 | 100.3993 |         1.58 |     50 | t
 bnb-ai-scalper-edbb        |  -6.738227 |  -6.738227 | 0.00000000 |  93.2618 | 101.0781 |         7.73 |     80 | t
 eth-ai-34d2                | -17.894887 | -17.894887 | 0.00000000 |  82.1051 |  99.3966 |        17.40 |     75 | t
 hype-breakout-da2e         |  -9.315946 |  -9.315946 | 0.00000000 |  93.0841 | 106.7256 |        12.78 |     75 | f
 hype-test-7db4             | -28.593348 | -28.593348 | 0.00000000 | 171.4067 | 202.7949 |        15.48 |     75 | f
 social-btc-astro           |   6.971835 |   6.971835 | 0.00000000 | 106.9718 | 106.9718 |         0.00 |     50 | t
 sol-ai-6486                |  -9.508268 |  -9.508268 | 0.00000000 |  90.4917 | 100.0000 |         9.51 |     85 | t
 sui-manual-59d9            |   7.731495 |   7.731495 | 0.00000000 | 207.7315 | 208.4298 |         0.34 |     50 | f
 tao-ai-range-rotation-d257 |   0.750200 |   0.750200 | 0.00000000 | 100.7502 | 104.8157 |         3.88 |     80 | t
 tv-btc-test-hl-94e1        | -82.647163 | -82.647163 | 0.00000000 | 117.3528 | 155.0823 |        24.33 |     85 | f
 tv_test_harness            |  -4.220586 |  -4.220586 | 0.00000000 |  95.7794 |  96.9754 |         1.23 |     90 | f
 xrp-ai-3844                |   0.000000 |   0.000000 | 0.00000000 |  99.9900 |  99.9900 |         0.00 |     84.9 | t
```

Every gap exactly zero. No strategy moved closer to its drawdown stop — the largest
remaining drawdown is 24.33% against an 85% limit, on a disabled strategy. Only
`sui-manual-59d9` lost capital (−0.698), taking it to 0.34% drawdown against a 50% stop.

---

## Live verification of the code fix

On `hype-test-7db4` (disabled). A close **order's** pnl — the real input — was moved by
+1.0, `sync_position_pnl()` was run, then the change was reverted and it was run again:

```
position 1e0aae3e-45c0-4d63-aaa3-79d7f26dacd8
  BEFORE   strategy pnl_total=-28.593348...  capital=171.406651...  position pnl=-0.210482...
  +1.0     strategy pnl_total=-27.593348...  capital=172.406651...  position pnl= 0.789517...
  REVERTED strategy pnl_total=-28.593348...  capital=171.406651...  position pnl=-0.210482...

=== VERDICT ===
  [OK] position pnl rose by the bump
  [OK] strategy total rose by the SAME bump
  [OK] capital rose by the same bump
  [OK] position pnl restored on revert
  [OK] strategy total restored on revert
  [OK] capital restored on revert
  [OK] strategy/positions gap is zero
```

The strategy total tracks the correction exactly, in both directions, and returns to its
starting value — so the mechanism is neither lossy nor double-counting.

Fleet-wide, after everything:

```
 strategies | with_gap | total_abs_gap
------------+----------+---------------
         12 |        0 |    0.00000000
```

---

## Found on the way, NOT fixed

* **`pnl_today` never resets.** `db/migrations/_archive/002_strategy_centric.sql` documents
  it as *"realised P&L today (resets at midnight UTC)"*, but no reset exists anywhere in
  the codebase — and `pnl_today == pnl_total` on all twelve strategies confirms it has
  never run. The column is currently a duplicate of `pnl_total` under a misleading name.
  The reconciliation above moved both by the same delta, which keeps them consistent with
  each other but does not make `pnl_today` mean what it says.

## Caveats on the reconciliation

* It assumes **`pnl_total` should equal the sum of closed positions' `pnl_realized`** —
  which is the model the code implements (each close books its position once). If a
  booking legitimately happens with no position row, that would be absorbed. One such path
  exists: the flip handler books `flip_pnl` directly when no opposite leg is found
  (`webhook_handler.py`, *"booking directly"*). No occurrence of it appears in the retained
  logs, but log retention does not cover the full history, so this cannot be ruled out for
  older trades.
* Positions whose close orders carry NULL `pnl` — the four rows backfilled earlier today —
  contribute nothing to the recomputation (`WHERE o.pnl IS NOT NULL`), so the backfill did
  not shift any figure here.
