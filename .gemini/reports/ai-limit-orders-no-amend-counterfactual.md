# What AI limit orders would have done with no amends — 14-day counterfactual

**Date:** 2026-07-27
**Window:** 2026-07-13 → 2026-07-27 (14 days)
**Question asked:** if the AI could not amend a resting limit order after placing it,
what would the results have been?
**Answer:** worse. Amending is currently protective, not harmful. Details below.
**Nothing was changed.** Analysis only.

---

## 1. The population

```
$ psql -c "SELECT strategy_id, symbol, order_type, status, count(*)
           FROM orders WHERE signal_source='ai_engine'
             AND received_at >= now() - interval '14 days'
           GROUP BY 1,2,3,4 ORDER BY 1,3,4;"

        strategy_id         |  symbol   | order_type |  status   | count
----------------------------+-----------+------------+-----------+-------
 bnb-ai-scalper-edbb        | BNB-USDT  | market     | filled    |    70
 eth-ai-34d2                | ETH-USDT  | limit      | cancelled |     7
 eth-ai-34d2                | ETH-USDT  | limit      | filled    |     5
 eth-ai-34d2                | ETH-USDT  | limit      | pending   |     1
 eth-ai-34d2                | ETH-USDT  | limit      | rejected  |     6
 eth-ai-34d2                | ETH-USDT  | market     | filled    |     8
 hype-breakout-da2e         | HYPE-USDT | market     | filled    |     5
 sol-ai-6486                | SOL-USDT  | market     | filled    |    10
 tao-ai-range-rotation-d257 | TAO-USDT  | market     | filled    |    12
```

**`eth-ai-34d2` is the only strategy that placed a limit order in the window.** Every other
AI strategy traded at market, and a market order cannot be amended — it fills on arrival. So
the whole question reduces to ETH's 19 limit orders.

Of those 19: **6 were rejected at placement** (never reached the exchange, see §5), leaving
**13 orders that actually rested**. Over the same window the strategy fired **63 amends**
against **19 placements** — about 3.3 price changes per order.

## 2. Method

Baseline for each order is its **original** price / stop / target, taken from
`orders.raw_webhook`, which is never overwritten by an amend. The order is then replayed
against blofin ETH-USDT candles, using the finest series that covers the date (1m back to
07-26, 5m to 07-24, 15m to 07-21, 1h before that).

Two horizons, because "never filled" is ambiguous on its own:

- **real** — the order dies exactly when the real one died (cancelled or filled). Every
  other decision the strategy made is preserved; only the amends vanish.
- **open** — the order rests until it fills or until now. What "place it and walk away"
  would really have produced.

Fill rule: a buy fills when a bar's low reaches the price, a sell when the high does. After
the fill, the first of stop/target touched ends the trade.

**Same-bar ordering** is settled rather than guessed. A resting entry always sits *between*
the market and its own stop, so price must cross the entry before it can reach the stop —
the fill provably comes first. A target inside the fill bar is genuinely ambiguous and is
flagged. After this correction the flag count is **0**: no result in this report rests on a
coin flip.

Scripts: `sim2.py` (main), `band_on_amends.py` (§4), in the session scratchpad.

## 3. Result

```
======================================================================================================================
HORIZON = real   order dies when the real one did (cancelled/filled)
======================================================================================================================
order      side  real end     orig px         fill      result         exit  $ no-amend   $ real  flag
----------------------------------------------------------------------------------------------------------------------
6111ad88   buy   cancelled    1834.22  07-17 05:00          sl  07-17 05:00       -0.78     0.00
05724398   sell  cancelled    1865.12            - never_filled            -        0.00     0.00
29edf6d1   buy   cancelled    1806.10            - never_filled            -        0.00     0.00
98840167   buy   filled       1865.70  07-19 05:00          tp  07-19 10:00        1.02    -0.04
d5f658c5   buy   cancelled    1843.10            - never_filled            -        0.00     0.00
2c2aa89f   sell  filled       1896.50  07-20 15:00  still_open            -       -0.52    -0.55
9bce8aac   sell  filled       1963.20            - never_filled            -        0.00     2.63
4f7d1faa   buy   filled       1906.44  07-23 12:30          sl  07-23 12:45       -1.20     0.15
9c76b7d0   sell  cancelled    1903.17            - never_filled            -        0.00     0.00
322986c6   buy   cancelled    1821.42            - never_filled            -        0.00     0.00
86ee9b20   buy   pending      1869.56            - never_filled            -        0.00     0.00
abec4581   sell  filled       1925.45  07-26 21:48          sl  07-26 21:48       -1.90    -5.13
1323d6c3   sell  cancelled    1970.55  07-27 06:06          sl  07-27 06:06       -2.60     0.00
----------------------------------------------------------------------------------------------------------------------
orders on the exchange: 13      filled without amends: 6   (targets reached: 1)
                                 filled with 40% band : 7   (targets reached: 2)
same-bar cases with no finer candles to settle the order: 0
  REAL, as traded (with amends) :    -2.94 USD
  NO AMENDS                     :    -5.98 USD   diff -3.03
  NO AMENDS + 40% entry band    :    -4.92 USD   diff -1.97

======================================================================================================================
HORIZON = open   order rests until it fills, or until now
======================================================================================================================
order      side  real end     orig px         fill      result         exit  $ no-amend   $ real  flag
----------------------------------------------------------------------------------------------------------------------
6111ad88   buy   cancelled    1834.22  07-17 05:00          sl  07-17 05:00       -0.78     0.00
05724398   sell  cancelled    1865.12  07-18 18:00          sl  07-19 02:00       -0.76     0.00
29edf6d1   buy   cancelled    1806.10            - never_filled            -        0.00     0.00
98840167   buy   filled       1865.70  07-19 05:00          tp  07-19 10:00        1.02    -0.04
d5f658c5   buy   cancelled    1843.10            - never_filled            -        0.00     0.00
2c2aa89f   sell  filled       1896.50  07-20 15:00          sl  07-20 18:00       -1.50    -0.55
9bce8aac   sell  filled       1963.20  07-26 23:00          sl  07-27 06:00       -1.34     2.63
4f7d1faa   buy   filled       1906.44  07-23 12:30          sl  07-23 12:45       -1.20     0.15
9c76b7d0   sell  cancelled    1903.17  07-26 15:15          sl  07-26 15:15       -0.60     0.00
322986c6   buy   cancelled    1821.42            - never_filled            -        0.00     0.00
86ee9b20   buy   pending      1869.56            - never_filled            -        0.00     0.00
abec4581   sell  filled       1925.45  07-26 21:48          sl  07-26 21:48       -1.90    -5.13
1323d6c3   sell  cancelled    1970.55  07-27 06:06          sl  07-27 06:06       -2.60     0.00
----------------------------------------------------------------------------------------------------------------------
orders on the exchange: 13      filled without amends: 9   (targets reached: 1)
                                 filled with 40% band : 11   (targets reached: 3)
same-bar cases with no finer candles to settle the order: 0
  REAL, as traded (with amends) :    -2.94 USD
  NO AMENDS                     :    -9.66 USD   diff -6.71
  NO AMENDS + 40% entry band    :    -7.64 USD   diff -4.69
```

All figures gross of fees. Fees at this size are roughly $0.10-0.50 per round trip, which
would push the no-amend columns further down (they trade more often than the real path did).

### Reading it

**Amending wins on both horizons**, by $3.03 (real) and $6.71 (open). It is not close.

The mechanism is not that amends produce better entries — it is that **amends and cancels
keep the strategy out of trades that were about to lose**. Four of the five worst no-amend
lines are stops that the real strategy never took, because the price had been walked away or
the order was cancelled first:

| order | no amends | as traded | what the amend did |
|---|---|---|---|
| `1323d6c3` | −2.60 | 0.00 | walked the sell up, then cancelled it before price came back |
| `9bce8aac` | 0.00 | **+2.63** | walked the sell up to 1967.6 and caught the move the original 1963.2 missed |
| `4f7d1faa` | −1.20 | +0.15 | re-fitted the buy down and out of a stop-out |
| `6111ad88` | −0.78 | 0.00 | re-priced then cancelled |

Two lines go the other way — the amends made things worse:

| order | no amends | as traded | what the amend did |
|---|---|---|---|
| `98840167` | **+1.02** | −0.04 | original target 1875.22 was reached at 07-19 10:00; the amends had moved it to 1894.48, which was never reached, and the AI later scratched the trade |
| `abec4581` | −1.90 | **−5.13** | the amends widened the stop from 1929.11 to 1934.40, so the same loss cost 2.7× more |

`abec4581` is the interesting one: widening a stop during an amend is how a $1.90 loss became
a $5.13 loss. That is a bigger single-trade effect than anything the entry band does.

## 4. The 40% entry band, tested on the path the order really took

The `$ band` figures in §3 apply the band to the *un-amended* order, which is not the
proposal. The proposal is amends **plus** the band. That can only be tested where the amend
prices still exist — container logs, which reach back 2 days. For ETH order `86ee9b20`:

```
         amend at  order px      stop      R  low until next     gap  gap as % of R  40% band fills?
--------------------------------------------------------------------------------------------------------
   07-26 09:01:21   1869.56   1863.95   5.61         1880.93   11.37           203%               no
   07-26 10:01:18   1876.44   1871.40   5.04         1882.01    5.57           111%               no
   07-26 11:00:55   1877.16   1871.70   5.46         1883.76    6.60           121%               no
   07-26 12:01:16   1877.88   1872.50   5.38         1884.95    7.07           132%               no
   07-26 13:01:28   1878.60   1873.13   5.47         1880.66    2.06            38%              YES
   07-26 17:01:56   1886.53   1878.60   7.93         1910.25   23.72           299%               no
   07-26 19:01:13   1888.71   1881.08   7.63         1910.64   21.93           287%               no
   07-26 22:03:41   1891.99   1884.21   7.78         1934.18   42.19           542%               no
   07-26 22:50:41   1891.99   1884.21   7.78         1938.61   46.62           599%               no
   07-27 01:20:27   1895.28   1884.89  10.39         1935.61   40.33           388%               no
   07-27 02:01:14   1896.37   1885.88  10.49         1941.00   44.63           425%               no
   07-27 03:00:55   1897.46   1886.50  10.96         1941.56   44.10           402%               no
   07-27 07:01:12   1901.84   1890.46  11.38         1960.55   58.71           516%               no
--------------------------------------------------------------------------------------------------------
With a 40% band the order would first have been filled in the interval starting 2026-07-26 13:01:28,
at 1880.78 (price traded down to 1880.66).
```

Verified minute by minute:

```
fill  2026-07-26 13:10:00+00:00
TARGET at 2026-07-26 14:05:00+00:00
gross per ETH 13.936  x size 0.5349 = $7.45
```

**One interval out of thirteen was a near-miss.** In the other twelve the market was 111% to
599% of the risk unit away — not close by any band setting a sane person would pick. The band
is a narrow instrument: it converts genuine near-misses into fills, and near-misses are rare.
When it does fire it is worth real money ($7.45 here, against a −$2.94 fortnight), but it will
fire a handful of times a month, not daily.

## 5. Two findings outside the question asked

**a) A third of limit placements die instantly.** Six of nineteen were rejected by the
exchange, all for the same reason:

```
2026-07-13 08:01:24 | buy  | 1807.234036 | Post only order would have immediately matched, bbo was 1789.7@1790.4
2026-07-19 03:00:36 | sell | 1873.5      | Post only order would have immediately matched, bbo was 1874.2@1874.3
2026-07-19 18:10:20 | buy  | 1871.672    | Post only order would have immediately matched, bbo was 1862.3@1863.3
2026-07-20 16:10:24 | sell | 1900.8      | Post only order would have immediately matched, bbo was 1900.8@1901.4
2026-07-21 16:01:07 | buy  | 1939.98     | Post only order would have immediately matched, bbo was 1932.9@1933.2
2026-07-26 15:01:39 | sell | 1896.62     | Post only order would have immediately matched, bbo was 1897.9@1898.5
```

The model proposed a limit on the **wrong side of the market** — e.g. buy at 1807 when the
market was 1790, buy at 1939.98 when it was 1933. Nothing catches this before it reaches the
exchange. That is a 32% placement failure rate, and it is a bigger loss of opportunity than
the near-miss problem the band addresses.

**b) These orders are post-only.** That directly constrains the entry-band design: pushing
the resting price toward the market risks turning a valid order into one of the rejections
above. The band must be clamped to stay a safe distance behind the best bid/ask, and the
clamp has to be tested, not assumed.

## 6. Limits of this analysis

- **13 orders.** Small. Treat the dollar totals as direction, not as a measurement.
- **Single-path.** Removing amends would also change what the AI did next — it might have
  cancelled and re-placed instead. Only the orders themselves were replayed; the strategy was
  not re-run.
- **One strategy, one coin, one fortnight** that happened to trend up strongly. Resting buy
  limits do badly in that tape by construction.
- **`2c2aa89f`** never resolved inside the real horizon and is valued at the last close.
- **Amend prices are not stored anywhere.** Only ~2 days survive, in container logs. The
  other ~52 amends in this window are gone. This is why §4 covers one order instead of
  nineteen — and it is the strongest argument for recording amend history (proposal Part C),
  independent of any chart.

## 7. Bearing on the proposal

- **Do not remove amends.** They are earning their keep.
- **Entry band: keep it, expect little from it.** One near-miss in thirteen intervals. It is
  cheap and it pays when it fires, but it is not the main lever.
- **The main levers this data actually points at** are (1) the 32% post-only rejection rate,
  and (2) stops being *widened* on amend, which turned −$1.90 into −$5.13 on one trade.
  Neither is in the current proposal.
- **Record amend history first.** Without it this analysis cannot be repeated properly, on
  ETH or anywhere else.

## Files touched

None.
