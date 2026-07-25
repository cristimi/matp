# The 8 "Closed on exchange" AI positions since 2026-07-19

Follow-up to the fee-reporting fix (`6c2b316`). Of 32 AI trades closed since 2026-07-19,
24 were closed by the AI itself (`signal_close`, netting **+0.005**) and 8 were closed by
the exchange (`Closed on exchange`, netting **−4.35** — the entire period's loss).

**Verdict: all 8 are legitimate exchange-side protective exits (stop-loss or take-profit).
No liquidations, and no reconciler false positives.** Two real accounting defects were
found along the way, but neither corrupts the realized-PnL numbers.

## 1. Not liquidations

Every close was far from its liquidation price:

```
     strategy_id     | side  | entry_price | closing_price | liquidation_price
---------------------+-------+-------------+---------------+-------------------
 eth-ai-34d2         | long  |   1863.3383 |     1863.8686 |    1806.94
 bnb-ai-scalper-edbb | short |     577.48  |      577.52   |     631.69
 bnb-ai-scalper-edbb | long  |     574.39  |      571.82   |     519.86
 bnb-ai-scalper-edbb | long  |     573.10  |      573.69   |     518.69
 sol-ai-6486         | long  |      77.70  |       77.3191 |      74.23
 bnb-ai-scalper-edbb | long  |     570.81  |      569.355  |     516.62
 bnb-ai-scalper-edbb | long  |     566.56  |      563.29   |     512.78
 bnb-ai-scalper-edbb | long  |     561.26  |      557.84   |     507.98
```

## 2. Not reconciler false positives

All 8 reached `reconcile_miss_count = 3`. The logs show the exchange reported size **0**
from the very first miss — the position was genuinely gone, not a transient poll failure:

```
12:57:10 reconciler: position 9964c546 (BNB-USDT long) miss 1/3 db=0.18 exchange=0
12:58:12 reconciler: position 9964c546 (BNB-USDT long) miss 2/3 db=0.18 exchange=0
12:59:13 reconciler: position 9964c546 (BNB-USDT long) miss 3/3 db=0.18 exchange=0
12:59:14 Closed position 9964c546 ... fill=563.29 pnl_gross=-0.8326476 pnl_net=-0.77181228
```

The 3-pass threshold and the size-0 reading both did their job.

## 3. What actually closed them

My first pass compared each close price against the SL/TP on the **opening** order and 5 of
8 appeared to hit neither. That comparison was wrong — the AI amends stops mid-life.
Splitting on whether `adjust_stops` fired during the position's life explains all 8:

| Position | adjust_stops fired | Exit |
|---|---|---|
| `172d419f` eth | 4 | amended stop |
| `89389863` bnb | 1 | amended stop (breakeven) |
| `ebe17087` bnb | 1 | amended stop |
| `5f579c7d` sol | 4 | amended stop |
| `728b0af3` bnb | 0 | **TP hit** — closed 573.69 ≥ TP 573.6731 |
| `9964c546` bnb | 0 | **SL hit** — closed 563.29 < SL 563.866 |
| `0d2940ff` bnb | 0 | **SL hit** — closed 557.84 < SL 558.106 |
| `4f2685fe` bnb | 0 | **SL hit** — stop reset to 569.74 at partial close, closed 569.355 |

The AI's own reasoning confirms the mechanism. For `89389863` (BNB short, entry 577.48),
the 11:30 cycle proposed `adjust_stops` at confidence 0.700, gate passed, webhook fired:

> "Current price 577.13 is 0.35% below entry (577.48)… 70% of the target has been hit,
> exceeding the half-target threshold… **Therefore we adjust the stop to breakeven at
> 577.48.**"

Six minutes later the position was gone, filled at **577.52** — the breakeven stop, with
4 ticks of slippage. Working exactly as designed.

For `4f2685fe`, the 12:00 `partial_close` cycle states "**Stop remains at 0.5% (569.74)**
below entry"; it filled at 569.355, below that level.

## 4. The real problem: breakeven stops + round-trip fees

The exits are correct. The economics are not. `89389863` is the clearest case:

- Gross PnL: **−0.0068** (a flat trade — stopped out at breakeven, by design)
- Round-trip fees: **0.177** (0.0589 open + 0.1178 close)
- Realized: **−0.1835**

The strategy repeatedly moves its stop to breakeven, gets stopped at ~flat, and pays a full
taker round-trip. Across the whole period: **gross −1.59, fees 2.75, net −4.34** — fees are
1.7× the trading loss itself. On `bnb-ai-scalper-edbb` (22 of 32 trades, −3.84) the
per-trade edge is smaller than the round-trip cost.

## 5. Defect A — reconciler double-deducts the close fee into `orders.pnl`

`BlofinAdapter.get_closed_position_details` (`order-executor/app/adapters/blofin.py:944`):

```python
pnl = Decimal(str(entry.get("realizedPnl") or "0"))
fee = Decimal(str(entry.get("fee") or "0"))
return { "pnl_realized": pnl + fee, "fee": fee, ... }
```

Blofin's `realizedPnl` is **already net of the close fee**, so `pnl + fee` deducts it twice.
`orders.pnl` for reconciler closes equals `gross − 2×|fee|`, exact to 5 decimals on all five
positions with no partial closes:

```
     symbol   |  gross   |   fee    | gross_minus_2x_fee | actual_order_pnl
--------------+----------+----------+--------------------+------------------
 BNB-USDT     | -0.00680 | -0.11781 |           -0.24242 |         -0.24242
 BNB-USDT     | -0.43690 | -0.11691 |           -0.67073 |         -0.67073
 BNB-USDT     |  0.10030 | -0.11697 |           -0.13365 |         -0.13365
 BNB-USDT     | -0.58860 | -0.12202 |           -0.83265 |         -0.83265
 BNB-USDT     | -0.61560 | -0.12086 |           -0.85733 |         -0.85733
```

## 6. Defect B — reconciler stores the exchange's fee sign verbatim

`_handle_full_external_close` writes `history["fee"]` straight into `orders.exchange_fee`
(`order-listener/app/reconciler.py:803`). Blofin returns fee as a negative number, so
**16 of 24** reconciler close orders carry a negative fee, against the positive convention
every other path uses:

```
       signal_source       | close_orders | negative_fee | most_negative
---------------------------+--------------+--------------+---------------
 ai_engine                 |           65 |            0 |        0.0005
 reconciler                |           24 |           16 |       -0.4837
 tv_test                   |           26 |            0 |        0.0752
```

The sign is not even consistent within the reconciler path — `172d419f` came back positive
(+0.08991).

### Why realized PnL is still correct

The two defects cancel. `pnl_realized = Σ(order pnl) − Σ(signed fee)`, so double-deducting
the close fee and then subtracting a negative fee adds one back:

```
gross − 2·close_fee − (open_fee − close_fee) = gross − close_fee − open_fee   ✓
```

Verified against the price-derived value — 5 of 8 match exactly; the 3 that differ all have
partial closes my simple formula ignores:

```
                  id  | true_net (gross−fees) | recorded pnl_realized
----------------------+-----------------------+-----------------------
 89389863             |              -0.18351 |              -0.18351
 ebe17087             |              -0.61240 |              -0.61240
 728b0af3             |              -0.07513 |              -0.07513
 9964c546             |              -0.77181 |              -0.77181
 0d2940ff             |              -0.79708 |              -0.79708
```

**So the −4.34 period figure is trustworthy.** The corruption is confined to the per-order
`pnl` and `exchange_fee` columns.

### Display impact — newly visible

Before `6c2b316` these fields were invisible: the timeline read fees from
`order_execution_log`, which has no rows for closes, so everything rendered `—`. Now that it
reads `orders.exchange_fee`, the L3 timeline on these 16 orders will show a **negative fee**
and a **`Realized` value that double-counts the close fee**. The fee fix was correct; it
surfaced pre-existing bad data underneath.

## Recommendation (not applied — investigation only)

1. Drop the `+ fee` in `blofin.py:944` (`pnl_realized = pnl`), since `realizedPnl` is
   already net. Requires confirming against a raw `positions-history` payload first.
2. Normalise to `abs(fee)` when writing `orders.exchange_fee` in the reconciler.
3. These two must land **together** — each alone breaks the cancellation and would corrupt
   `pnl_realized`, which is currently correct. A backfill of the 16 affected rows should
   accompany them.
4. Separately, the fee drag on `bnb-ai-scalper-edbb` deserves a look: breakeven stops on a
   scalper paying taker fees both ways is structurally negative-expectancy at the current
   edge.
