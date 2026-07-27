# What the AI strategies would have done with post-entry management switched off — 7-day counterfactual

**Date:** 2026-07-27
**Window:** positions opened 2026-07-20 08:53 → 2026-07-27 08:53 (7 days)
**Question asked:** the AI engine re-fits TP/SL and decides to close positions at every
scheduled cycle. What if that were turned off and every position simply rode the TP/SL it
was given at entry?
**Answer:** clearly worse. −13.16 USD becomes −26.25 USD, roughly double the loss.
**Nothing was changed.** Analysis only.

---

## 1. What "management" means here

Four actions the AI takes on a position that is already open:

| action | what it does |
|---|---|
| `adjust_stops` | moves the live TP and/or SL on the exchange |
| `partial_close` | closes part of the position |
| `close_long` / `close_short` | closes the whole position early |

All four are the thing being disabled. Entry decisions are left untouched — the
counterfactual takes exactly the same trades, at the same times, at the same sizes.

In the window these fired **70 times** across the four AI strategies:

```
$ psql -c "SELECT strategy_id, proposed_action, count(*) FROM ai_signal_log
           WHERE triggered_at >= now() - interval '7 days' AND gate_passed AND webhook_fired
             AND proposed_action IN ('adjust_stops','partial_close','close_long','close_short')
             AND strategy_id IN ('bnb-ai-scalper-edbb','eth-ai-34d2','sol-ai-6486',
                                 'tao-ai-range-rotation-d257')
           GROUP BY 1,2 ORDER BY 1,2;"

        strategy_id         | proposed_action | count
----------------------------+-----------------+-------
 bnb-ai-scalper-edbb        | adjust_stops    |     4
 bnb-ai-scalper-edbb        | close_long      |     8
 bnb-ai-scalper-edbb        | close_short     |     8
 bnb-ai-scalper-edbb        | partial_close   |    25
 eth-ai-34d2                | adjust_stops    |     1
 eth-ai-34d2                | close_long      |     1
 eth-ai-34d2                | close_short     |     4
 sol-ai-6486                | adjust_stops    |     6
 sol-ai-6486                | close_long      |     1
 sol-ai-6486                | partial_close   |     6
 tao-ai-range-rotation-d257 | close_long      |     1
 tao-ai-range-rotation-d257 | close_short     |     3
 tao-ai-range-rotation-d257 | partial_close   |     2
```

## 2. The population

35 positions, all closed, four strategies:

```
$ psql -c "SELECT strategy_id, status, close_reason, count(*)
           FROM strategy_positions WHERE opened_at >= now() - interval '7 days'
           GROUP BY 1,2,3 ORDER BY 1,3;"

        strategy_id         | status |    close_reason    | count
----------------------------+--------+--------------------+-------
 bnb-ai-scalper-edbb        | closed | Closed on exchange |     6
 bnb-ai-scalper-edbb        | closed | signal_close       |    16
 eth-ai-34d2                | closed | Closed on exchange |     1
 eth-ai-34d2                | closed | signal_close       |     5
 social-btc-astro           | closed | signal_flat        |     1
 social-btc-astro           | open   |                    |     1
 sol-ai-6486                | closed | Closed on exchange |     2
 sol-ai-6486                | closed | signal_close       |     1
 tao-ai-range-rotation-d257 | closed | signal_close       |     4
```

`social-btc-astro` is excluded — it is not an AI-engine strategy.

**26 of 35 positions (74%) were ended by the AI rather than by the market.** That is the
size of the thing being switched off.

## 3. Method

- **Baseline TP/SL** is the pair recorded on the position's opening order,
  `orders.tp_price` / `orders.sl_price`. `adjust_stops` writes only to the exchange and never
  back to the DB (`webhook_handler.adjust_stops_for_strategy`, no `UPDATE orders`), so those
  two columns still hold the values the position was given at entry. Verified: no post-entry
  code path writes them.
- **Size** is the real filled size, taken as the sum of the position's closing-order sizes
  (the opening order stores the *requested* size, which the exchange rounds).
- **Replay** is against blofin 1-minute candles, 12 959 bars per symbol, fetched through
  ccxt inside `market-ingestion`, zero gaps. Bars are walked from the first minute strictly
  after entry; the first bar to touch TP or SL ends the trade. **No bar touched both** — there
  are 0 ambiguous cases, so no result rests on a coin flip.
- **Fees** are the real exchange fees on the entry, plus a modelled taker fee on the exit at
  the rate measured from the real orders (blofin 7.4 bp, hyperliquid 3.5 bp).
- **eth-ai-34d2 runs on hyperliquid**; blofin ETH-USDT candles are used as the price proxy.

Script: `sim.py` in the session scratchpad.

### The replay is validated against reality

Five positions really did end on their entry stop with no `adjust_stops` in between. The
replay has to reproduce them, and does — same side, same level, within 2–3 minutes:

```
VALIDATION — positions that really ended on their untouched entry stop
pos       sim exit  sim price      sim time  real price     real time
728b0af3        tp   573.6731   07-22 14:04    573.6900   07-22 14:07
4f2685fe        sl   568.4140   07-23 12:45    569.3550   07-23 12:47
9964c546        sl   563.8660   07-24 12:57    563.2900   07-24 12:59
0d2940ff        sl   558.1060   07-24 13:53    557.8400   07-24 13:56
ff4de176        sl  1934.4000   07-26 22:00   1936.0165   07-26 22:02
```

The price gaps are ordinary stop slippage, and are small in both directions.

## 4. Result

```
======================================================================================================================
PER-POSITION — AI post-entry management OFF (position rides its entry TP/SL)
======================================================================================================================
pos       strategy    sd    opened           entry sim exit           at     real end  mgmt    $ sim   $ real     diff blk
----------------------------------------------------------------------------------------------------------------------
97e2da53  tao-ai-rang short 07-20 11:02     194.54       sl  07-21 07:36  07-20 12:03     1    -1.81    -0.50    -1.31
7a315fa6  bnb-ai-scal long  07-20 11:16     567.76       tp  07-21 04:01  07-21 00:46     2     1.35     0.74     0.60
391d12f8  eth-ai-34d2 short 07-20 15:45    1897.97       sl  07-20 18:01  07-20 16:02     1    -1.63    -0.55    -1.08
c8532ce7  bnb-ai-scal long  07-21 02:46     573.24       tp  07-21 08:00  07-21 03:16     1     0.57     0.15     0.42   Y
53aae63b  bnb-ai-scal short 07-21 04:31     575.09       sl  07-21 08:21  07-21 06:45     3    -0.55    -0.37    -0.18
89389863  bnb-ai-scal short 07-21 11:15     577.48       tp  07-21 14:25  07-21 11:36     1     0.32    -0.12     0.45
ebe17087  bnb-ai-scal long  07-22 01:16     574.39       sl  07-22 03:14  07-22 03:17     1    -0.62    -0.55    -0.07
90f9897c  eth-ai-34d2 short 07-22 02:01    1933.80       sl  07-22 14:05  07-22 03:10     1    -1.63    -0.01    -1.62
4c024522  bnb-ai-scal long  07-22 06:46     568.93       tp  07-22 14:05  07-22 11:16     2     0.93     0.35     0.57
728b0af3  bnb-ai-scal long  07-22 13:46     573.10       tp  07-22 14:04  07-22 14:07     1    -0.03    -0.02    -0.02   Y
5f579c7d  sol-ai-6486 long  07-22 20:01      77.70       sl  07-23 12:42  07-23 06:26     6    -2.61    -1.22    -1.40
4712b346  eth-ai-34d2 short 07-22 20:42    1968.09       tp  07-23 12:36  07-22 21:50     1     6.11     2.63     3.48
b63fb201  tao-ai-rang long  07-23 03:01     195.45       sl  07-23 15:11  07-23 04:46     2    -1.26    -0.21    -1.04
160ca22f  bnb-ai-scal short 07-23 07:46     569.75       tp  07-23 15:14  07-23 08:31     1     0.71    -0.40     1.11
c4e5a640  bnb-ai-scal short 07-23 10:01     569.55       tp  07-23 13:52  07-23 11:16     2     0.24    -0.34     0.58   Y
4f2685fe  bnb-ai-scal long  07-23 11:31     570.81       sl  07-23 12:45  07-23 12:47     2    -0.57    -0.39    -0.18   Y
09439ab2  eth-ai-34d2 long  07-23 12:46    1904.20       sl  07-23 15:03  07-23 13:02     1    -1.47     0.15    -1.63
b185ec1e  bnb-ai-scal short 07-23 15:46     566.95       sl  07-24 03:15  07-23 18:45     3    -0.62    -0.18    -0.44
7aeeb76f  bnb-ai-scal short 07-23 19:32     567.44       sl  07-24 07:13  07-23 20:47     2    -0.87    -0.30    -0.57   Y
f24c4c65  bnb-ai-scal short 07-23 21:31     567.69       sl  07-24 03:22  07-23 23:15     2    -0.64    -0.16    -0.48   Y
d4a47962  sol-ai-6486 long  07-23 22:00      75.97       sl  07-24 11:25  07-23 23:30     1    -2.29     0.23    -2.53
da33c188  bnb-ai-scal long  07-23 23:46     568.08       sl  07-24 12:47  07-24 05:16     6    -0.67    -0.15    -0.52   Y
3b45d7f6  tao-ai-rang short 07-24 06:01     193.15       tp  07-24 12:02  07-24 07:16     1     1.28    -0.85     2.13
f6268beb  bnb-ai-scal long  07-24 06:16     568.58       sl  07-24 11:46  07-24 07:17     2    -0.66     0.11    -0.77
69495760  bnb-ai-scal short 07-24 07:31     570.76       tp  07-24 12:56  07-24 09:01     3     0.93     0.18     0.75   Y
9964c546  bnb-ai-scal long  07-24 12:15     566.56       sl  07-24 12:57  07-24 12:59     1    -0.62    -0.71     0.09
0d2940ff  bnb-ai-scal long  07-24 13:16     561.26       sl  07-24 13:53  07-24 13:56     1    -0.70    -0.74     0.03
75bebcc1  bnb-ai-scal long  07-24 14:16     558.05       tp  07-24 19:02  07-24 14:31     1     1.41    -0.07     1.48
73539b23  bnb-ai-scal short 07-24 19:31     565.44       sl  07-25 13:03  07-24 22:45     4    -0.55    -0.11    -0.44
9fe0f6bc  bnb-ai-scal long  07-25 01:46     565.52       tp  07-26 05:20  07-25 03:16     2     0.96    -0.06     1.03   Y
a186f8bb  sol-ai-6486 short 07-26 13:01      74.84       sl  07-26 15:20  07-26 14:08     2    -6.22    -4.97    -1.26
1851311f  tao-ai-rang short 07-26 14:01     196.86       sl  07-27 02:46  07-26 19:47     2    -5.29    -2.04    -3.25
2a7fcb46  bnb-ai-scal long  07-26 17:02     573.46     open  07-27 08:53  07-27 06:02     4    -0.31     1.29    -1.60
ff4de176  eth-ai-34d2 short 07-26 21:49    1927.45       sl  07-26 22:00  07-26 22:02     1    -4.11    -5.13     1.02
fb754e21  eth-ai-34d2 short 07-27 00:01    1953.10       sl  07-27 06:06  07-27 01:10     1    -5.30     1.14    -6.44
----------------------------------------------------------------------------------------------------------------------

positions: 35   ambiguous same-bar tp+sl: 0
taker fee used: blofin=7.4bp, hyperliquid=3.5bp

                                           gross      fees       net
REAL, as traded (management ON)            -6.14      7.02    -13.16
NO MANAGEMENT, all 35 replayed            -18.83      7.42    -26.25
NO MANAGEMENT, entry-conflicts dropped    -19.98      6.20    -26.18   (9 entries blocked)

By strategy (net USD):
strategy                       n      real   no-mgmt      diff  blocked
bnb-ai-scalper-edbb           22     -1.82      0.08      1.90        9
eth-ai-34d2                    6     -1.78     -8.05     -6.27        0
sol-ai-6486                    3     -5.95    -11.13     -5.18        0
tao-ai-range-rotation-d257     4     -3.61     -7.08     -3.47        0

Outcome mix without management:
  tp     12 of 35   (of the non-blocked: 7)
  sl     22 of 35   (of the non-blocked: 18)
  open    1 of 35   (of the non-blocked: 1)

real win rate 10/35   no-mgmt win rate 11/35

Largest differences (no-mgmt minus real, net USD):
  fb754e21  eth-ai-34d2            short sim=sl     -5.30  real(signal_close      )    1.14 ->   -6.44
  4712b346  eth-ai-34d2            short sim=tp      6.11  real(signal_close      )    2.63 ->   +3.48
  1851311f  tao-ai-range-rotation- short sim=sl     -5.29  real(signal_close      )   -2.04 ->   -3.25
  d4a47962  sol-ai-6486            long  sim=sl     -2.29  real(signal_close      )    0.23 ->   -2.53
  3b45d7f6  tao-ai-range-rotation- short sim=tp      1.28  real(signal_close      )   -0.85 ->   +2.13
  09439ab2  eth-ai-34d2            long  sim=sl     -1.47  real(signal_close      )    0.15 ->   -1.63
  90f9897c  eth-ai-34d2            short sim=sl     -1.63  real(signal_close      )   -0.01 ->   -1.62
  2a7fcb46  bnb-ai-scalper-edbb    long  sim=open   -0.31  real(signal_close      )    1.29 ->   -1.60
  75bebcc1  bnb-ai-scalper-edbb    long  sim=tp      1.41  real(signal_close      )   -0.07 ->   +1.48
  5f579c7d  sol-ai-6486            long  sim=sl     -2.61  real(Closed on exchange)   -1.22 ->   -1.40

mean holding time   real 2.5 h   no-mgmt 8.4 h
```

Two horizons for the total, because a position that lives longer can block the strategy's
next entry (`uq_strat_pos_one_open`):

- **all 35 replayed** — every real entry is still taken, overlaps ignored: **−26.25**
- **entry-conflicts dropped** — a new entry is skipped while the previous position is still
  open in the counterfactual: **−26.18**, with 9 BNB entries never taken

The two agree, so the conclusion does not depend on how overlaps are handled.

## 5. Reading it

**Management is earning its keep, by about $13 over the week — it roughly halves the loss.**

The mechanism is asymmetry, not accuracy. Split the 35 positions by whether the
counterfactual did better or worse:

```
no-mgmt better on 14 positions, worse on 21
sum of the wins   +13.74
sum of the losses -26.83
```

The AI is wrong often — in 14 of 35 cases it cut a trade that would have gone on to reach
its target, and gave up $13.74 doing so. But the losses it avoids are bigger than the gains
it forfeits, because **the trades it exits early are disproportionately the ones heading for
a full stop-out**. 22 of 35 positions would have ended on their stop without it. Cutting at
−$0.01 instead of −$1.63 (`90f9897c`) is the shape of most of the benefit.

Note the win rate barely moves (10/35 vs 11/35). The difference is entirely in size, not
frequency.

### Per strategy, the picture is not uniform

| strategy | real | no management | verdict |
|---|---|---|---|
| `bnb-ai-scalper-edbb` | −1.82 | +0.08 | management **costs** ~$1.90 |
| `eth-ai-34d2` | −1.78 | −8.05 | management saves $6.27 |
| `sol-ai-6486` | −5.95 | −11.13 | management saves $5.18 |
| `tao-ai-range-rotation-d257` | −3.61 | −7.08 | management saves $3.47 |

**BNB is the exception, and it is the strategy that manages most** — 45 of the 70 actions,
25 of them partial closes. On a scalper with a tight stop, letting the trade run to its own
TP or SL beat the constant trimming. The other three strategies hold wider stops, and there
management is what keeps a bad trade from costing full risk.

`da33c188` shows the extreme of the BNB pattern: six closing orders on one position
(0.09 → 0.045 → 0.0225 → 0.01125 → 0.005625 → 0.005625), each paying a fee, ending at −$0.15
where a clean exit would have been −$0.67. It works, but it is a lot of machinery for
six cents.

### The two biggest single trades

- **`fb754e21` (ETH short, 07-27)** — the AI closed it at 01:10 for **+$1.14**. Left alone, the
  entry stop at 1972.23 was taken at 06:06 for **−$5.30**. A single decision worth $6.44.
- **`4712b346` (ETH short, 07-22)** — the AI closed it at 21:50 for +$2.63. Left alone, the
  entry target at 1907.2 was reached the next day for **+$6.11**. Closing early cost $3.48.

Same strategy, same week, opposite lessons. With six ETH positions in the sample, neither is
evidence of a rule.

## 6. Limits of this analysis

- **35 positions over one week, all losing overall.** Treat the dollar totals as direction,
  not measurement. The whole book is −$13; the differences discussed are dollars.
- **Single-path.** Only the positions were replayed, the strategies were not re-run. Without
  management the AI would have made different *entry* decisions too (it would have seen
  itself still in a trade), and none of that is modelled beyond the entry-conflict variant.
- **`2a7fcb46` never resolved** — it is still inside its entry TP/SL at the end of the window
  and is marked at the last close.
- **ETH prices are blofin, the trades were hyperliquid.** Levels differ by a few basis points;
  on the three ETH stop-outs the level is far enough from entry that this does not change
  which side was hit.
- **Exit fees are modelled**, not real, for the counterfactual exits — at the measured taker
  rate. Entry fees are the real ones.
- **Slippage is not modelled** on counterfactual exits: they fill exactly at TP/SL. The
  validation table in §3 shows real stops slip a few cents either way, so this is small and
  unbiased.

## 7. What this points at

- **Do not disable post-entry management.** Over this week it is worth ~$13 on a −$13 book.
- **The one place worth testing a switch-off is `bnb-ai-scalper-edbb`**, where 25 partial
  closes in seven days produced a result slightly worse than doing nothing, and paid fees for
  it. A cheaper change than removing management would be to stop partial-closing below a
  minimum size — the 0.005625 legs on `da33c188` cannot be paying for themselves.
- **The 74% figure is the real headline**: only 9 of 35 positions were allowed to reach their
  own TP or SL. These strategies are, in practice, discretionary exits with a stop as a
  backstop — not TP/SL strategies. Any tuning of entry targets is tuning something that
  almost never fires.
