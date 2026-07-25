# Reconciler fee accounting: sign fix, double-deduction fix, and 16-row backfill

Follow-up to `4e4f4cc`. Fixes the two defects found in that investigation, backfills the
affected rows, and proposes an amendment to the scalper strategy.

**Correction to `4e4f4cc`:** that report concluded `pnl_realized` was still correct because
the two defects cancelled. That was wrong. It rested on the assumption that Blofin's `fee`
covered only the closing leg. A live payload (below) proves it is the **whole round trip**,
so the cancellation left the **opening fee counted twice** and every affected position
overstated its loss.

## The ground truth

Captured from the live demo account (`positions-history`, position `9fe0f6bc`, BNB-USDT):

```json
{"openAveragePrice":"565.52","closeAveragePrice":"565.861111111111111111",
 "closePositions":"0.18","realizedPnl":"-0.06078916","fee":"-0.12218916"}
```

Two facts follow, and both contradict what the code assumed:

1. **`realizedPnl` is already net of fees.** Price-derived gross is
   `(565.861111 − 565.52) × 0.18 = +0.0614`, and `0.0614 + (−0.12218916) = −0.06078916`
   — exactly `realizedPnl`.
2. **`fee` is the round-trip total, not the closing leg.** The per-order fees we booked for
   that position sum to precisely `|fee|`:

```
 0.06107616   (open)
 0.00339120   (partial close)
 0.05772180   (final close)
 ----------
 0.12218916   == |fee|
```

## Defect A — `pnl + fee` double-deducted the fee

`blofin.py` returned `pnl_realized = pnl + fee`. Since `realizedPnl` is already net, this
subtracted the round trip a second time. `orders.pnl` on reconciler closes was therefore
`gross − 2×|fee|`, exact to 5 decimals on all five positions with no partial closes.

## Defect B — the exchange's fee sign reached the DB

`_handle_full_external_close` wrote `history["fee"]` verbatim. Blofin signs fees negative,
so 16 of 24 reconciler closes carried a negative `exchange_fee` against the positive
convention used everywhere else.

## Why the net figure was wrong (not merely cosmetic)

`sync_position_pnl` computes `Σ(close.pnl) − Σ(close.fee) − open.fee`. With
`pnl_old = R + F` (R = Blofin net, F = −|fee|):

```
(gross − 2·TF) − (−TF) − open_fee  =  gross − TF − open_fee
```

But `TF` **already contains** `open_fee`, so it came off twice. Every affected position
overstated its loss by roughly the opening fee. The correct value is simply Blofin's own
`realizedPnl`, i.e. `pnl_old + |fee_old|`.

## The fix

Contract made explicit in `adapters/base.py`: `pnl_realized` is **GROSS**, `fee` is a
**positive magnitude**, and a new `fee_scope` says which legs it covers
(`round_trip` | `close_only`) so callers cannot double-count the opening leg.

- `blofin.py` — `pnl_realized = pnl - fee` (recovers gross), `fee = abs(fee)`,
  `fee_scope = 'round_trip'`.
- `hyperliquid.py` — tagged `close_only` (it sums closing fills only) and `abs()` applied
  as a guard. **Its numbers are unchanged**: it never had the `+ fee` bug and all 8 of its
  reconciler closes were already non-negative. Both defects were Blofin-only.
- `reconciler.py` — `_handle_full_external_close` now reduces the exchange's
  position-level figures to this close's own leg, subtracting the opening fee (when scope
  is `round_trip`) and any earlier partial closes' pnl/fee. Same normalisation applied in
  `_recover_manual_close_pnl`. It also now passes the derived per-leg pnl to
  `close_strategy_position` rather than the raw position-level figure.

### Tests

`order-executor`: **16 passed** (6 new, driving the real captured payload).
`order-listener`: **60 passed** (6 new), from a 54-passed baseline. The same 5 failures
(`test_fill_size_open_path`, `test_webhook_handler` auth/cap) pre-exist on the unmodified
image and are unrelated to fees — verified by running the suite before the change.

### Live verification

```
$ wget -qO- ".../positions/history?symbol=BNB-USDT&since=1784898995000"
{'pnl_realized': 0.0614, 'fee': 0.12218916, 'fee_scope': 'round_trip', ...}
```

Gross positive, fee positive, scope tagged.

## Backfill — 16 rows

Values were derived by algebraically inverting what the buggy path wrote
(`gross = pnl_old + 2·|fee_old|`, `R = pnl_old + |fee_old|`), then splitting off this
close's own leg. The inversion was cross-checked against price-derived gross and matched
exactly on all 11 positions without partial closes. The script aborts rather than write a
negative fee.

```
BEGIN
SELECT 16      -- rows selected
UPDATE 16      -- orders (pnl + exchange_fee)
UPDATE 16      -- strategy_positions (pnl_realized)
UPDATE 4       -- strategies (booked totals)
COMMIT
```

The strategy rollups were corrected too, since the wrong figures had already been booked
into the compounding allocation by `_book_realized_pnl`:

```
             id             | pnl_total (before → after) | capital_allocation (before → after)
----------------------------+---------------------------+------------------------------------
 bnb-ai-scalper-edbb        |  -4.012404  →  -3.457811   |  95.987596  →  96.542189
 hype-breakout-da2e         |  -9.887847  →  -9.520277   |  92.512153  →  92.879723
 sol-ai-6486                |  -1.223778  →  -1.105632   |  98.776222  →  98.894368
 tao-ai-range-rotation-d257 |   3.186329  →   3.367161   | 103.186329  → 103.367161
```

Total correction **+1.221 USDT** — every affected position had overstated its loss.
`allocation_peak` was moved only where the new allocation exceeded it. `pnl_today` was
included because no reset job exists (`_book_realized_pnl` is its only writer), so it is
cumulative in practice — worth noting as a separate pre-existing issue.

### Post-backfill state

```
 negative_fee_remaining | nonneg | total
------------------------+--------+-------
                      0 |     24 |    24
```

Critically, `sync_position_pnl` recomputes `pnl_realized` from the orders every pass, so
the backfill had to agree with it or be silently reverted. It does, across **every**
position in the DB:

```
 positions_where_sync_would_disagree
-------------------------------------
                                   0
```

Confirmed empirically: 8 reconciler passes have run since, and the values are unchanged.
The corrected timeline for `0d2940ff`:

```json
{"type":"entry","key":{"fee":0.06061608}}
{"type":"close","key":{"realized":-0.6156,"fee":0.06024672}}
```

`realized` now matches the price-derived gross exactly and both fees are positive.
Position `pnl_realized = -0.736463` = Blofin's own `realizedPnl`.

### Revised period figures (since 2026-07-19)

The `-4.34` in `4e4f4cc` was overstated. Corrected: **32 trades, 8 wins, −3.78**, of which
`signal_close` (24 trades) nets **+0.005** and the 8 exchange-side exits account for
**−3.79**. The earlier conclusion is unchanged in substance — the AI's own exits are
break-even and fees dominate — only the magnitude moves.

---

# Proposed amendment: `bnb-ai-scalper-edbb`

**Not implemented — proposal only.**

## The problem, quantified

Over 25 closed trades, lifetime:

| | |
|---|---|
| Gross PnL | **+0.320** |
| Fees | **3.022** |
| Net | **−2.702** |
| Gross win rate | 44.0% |
| Net win rate | 28.0% |

**The directional edge is positive. Fees are 9× larger than it.** Fees alone flip 4 of 25
winners into losers. Bucketed by whether the move could pay for itself:

```
 bucket                              | trades | gross  | fees  |  net
-------------------------------------+--------+--------+-------+--------
 a. |move| < fees (cannot pay costs) |      9 | -0.005 | 1.084 | -1.090
 b. |move| < 2x fees (marginal)      |      4 | -0.315 | 0.492 | -0.807
 c. |move| >= 2x fees                |     12 |  0.640 | 1.446 | -0.805
```

Bucket (a) is 36% of trades producing a gross of *zero* and burning 1.084 in fees. But note
bucket (c) loses too — so a selectivity gate alone will not fix this.

## Root cause: the template never models cost

The Scalper system prompt reasons in terms of "0.3–0.8% edge" and "very tight stops
(0.3–0.8%)" and **does not mention fees anywhere**. Measured cost is **0.0806% of notional
per leg, taker — 0.161% round trip** (all 73 orders are `market`; there is not one maker
fill). Average notional is only $66.38 and average hold is 133 minutes.

At the tight end of the template's own range — a symmetric 0.3% target and stop — the
after-cost arithmetic is:

- win = `0.3 − 0.161` = **+0.139%**
- loss = `0.3 + 0.161` = **−0.461%**
- breakeven win rate = `0.461 / (0.139 + 0.461)` = **77%**

The strategy wins 44% of the time. With a *symmetric* stop, `EV = d(2p−1) − c` is negative
at p = 0.44 for **every** target distance — no value of `d` rescues it.

## The amendments

**1. Make cost a first-class input (highest value).**
Inject the account's round-trip cost into the prompt context and add a hard Phase-1 gate:
reject any entry whose intended TP distance is below `3 × round_trip_cost` (≈0.5%). On this
sample that removes buckets (a) and (b) — 13 trades, **+1.897**.

**2. Require asymmetric R:R.**
Solving `s(pR − (1−p)) > c` at p = 0.44, s = 0.3%, c = 0.161% gives **R ≥ 2.5**. Replace the
symmetric stop language with an explicit floor: take-profit at least 2.5× the stop
distance. This is the single change that makes the arithmetic solvable at the observed win
rate.

**3. Maker entries via post-only limit.**
Every order is taker today. Routing the *entry* leg through the existing resting-limit
workflow (already built, and its amend/cancel phases were just repaired in `6cd92d7`) cuts
round-trip cost materially — with Blofin's maker tier roughly a third of taker, from
~0.161% to ~0.10%, which lowers the required R from 2.5 to ~2.0.
*Verify the account's actual maker rate before relying on this figure — I measured the
taker leg at 0.0806% but have not observed a maker fill to confirm the maker side.*
Trade-off: post-only entries can miss the move; the strategy must tolerate unfilled entries.

**4. Consolidate exits.**
1.92 closing orders per trade. Fees are proportional to notional so splitting a close is
roughly cost-neutral in theory, but observed fees run ~13% above the clean two-leg
round trip, consistent with lot-size rounding on each partial. Minor, but free to fix.

**5. Fix "breakeven" in the template (affects all strategies, not just this one).**
The AI moves stops to "breakeven at entry" — e.g. `89389863`, where the reasoning reads
*"we adjust the stop to breakeven at 577.48"* against a 577.48 entry. A stop at the raw
entry price **guarantees a loss equal to the round trip**. Breakeven is `entry ± cost`, not
`entry`. This fired only 3 times on the scalper but is a template-level defect that hit
`eth-ai-34d2` and `sol-ai-6486` harder.

## Honest assessment

Amendments 1–4 together shift this from a structural loser to roughly break-even on the
observed sample; they do not manufacture an edge. The gross edge is **+0.013 per trade**
against a **0.121 per trade** cost. If, after the cost gate is in place, gross-per-trade
does not rise well clear of cost, the correct conclusion is that 15-minute BNB scalping at
this notional does not clear taker fees, and the capital belongs in a lower-frequency
strategy. `eth-ai-34d2` is the only AI strategy currently net-positive (+2.17 over the
period) and holds far longer.
