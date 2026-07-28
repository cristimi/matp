# Paper Trade Forensics — 2026-07-28

**Type:** Investigation only. No code, config, schema or data was modified. Every query in
this report is a `SELECT`.

**Period covered by the data:** 2026-06-19 → 2026-07-28 (40 days).
**Note up front:** the brief assumed "the last ~2-3 months". The database does not contain
2-3 months of trading. The oldest row in every trading table is 2026-06-19, which is the day
the first strategy was created (`hype-test-7db4`, `ai-btc-6f8c`, `tv-btc-test-hl-94e1`, all
`created_at = 2026-06-19`). There is no evidence of a purge — the platform's trading history
simply starts there. All conclusions below are therefore drawn from 40 days, not 90.

---

## 1. Schema discovered

### 1.1 Tables that actually hold the trading record

| Table | Rows | Date range | What it holds |
|---|---:|---|---|
| `ai_signal_log` | 5135 | 2026-06-19 → 2026-07-28 | One row per AI decision cycle |
| `orders` | 507 | 2026-06-19 → 2026-07-28 | One row per order intent (open, close, amend) |
| `strategy_positions` | 166 | 2026-06-19 → 2026-07-28 | **The P&L record.** One row per position |
| `signal_log` | 463 | 2026-06-19 → 2026-07-28 | Inbound webhook receipts |
| `shadow_signals` | 529 | 2026-06-20 → 2026-07-27 | Local signal-engine shadow proposals |
| `social_signal_log` | 270 | 2026-06-11 → 2026-07-27 | Social-listener extractions |
| `order_execution_log` | 220 | — | Exchange-side execution attempts |
| `order_price_history` | 84 | from 2026-07-13 | SL/TP amendment history (migration 065) |
| `social_shadow_orders` | 23 | — | Social-listener shadow decisions |
| `spread_positions` | 3 | — | Spread trades |
| `funding_harvest_plans` | 2 | — | Funding-harvest plans |
| `strategy_performance` | **0** | — | **Empty — never written** |
| `strategy_stats` | **0** | — | **Empty — never written** |

Verbatim row counts are in Appendix A.2.

### 1.2 `strategy_positions` — the only table with realized P&L

Key columns: `strategy_id, exchange, symbol, side, entry_price, size, leverage,
pnl_unrealized, pnl_realized, status, opening_order_id, closing_order_id, opened_at,
closed_at, closing_price, liquidation_price, close_reason`.

`pnl_realized` is **net of fees** where fee data exists. `order-listener/app/webhook_handler.py:1433`
(`sync_position_pnl`) computes it as
`SUM(closing orders.pnl) - SUM(closing orders.exchange_fee) - opening order.exchange_fee`,
with `COALESCE(..., 0)` on each fee. **Missing fees are silently treated as zero**, so
`pnl_realized` understates costs on any position whose fees were not captured.

### 1.3 `orders` — entry/exit prices, brackets and fees

Key columns: `symbol, side, signal, order_type, size, price, tp_price, sl_price, status,
exchange_order_id, pnl, actual_fill_price, exchange_fee, closes_position_id, signal_log_id`.

`sl_price` on the opening order is what makes an R-multiple computable. It is present on
**163/163** closed positions.

### 1.4 `ai_signal_log` — the AI decision record

Key columns: `strategy_id, triggered_at, trigger_reason, cycle_interval, prompt_template,
data_sources_used text[], context_tokens, proposed_action, confidence numeric(4,3),
reasoning, gate_passed, gate_rejection_reason, webhook_fired, order_id, dry_run,
llm_provider, llm_model, llm_tier, geometry_data jsonb, missing_inputs text[],
input/output/total tokens, scout token counts, fallback_attempts jsonb`.

`confidence` is the model's own stated confidence, stored to 3 decimals. `reasoning` is the
free-text thesis (populated on 4961/5135 rows). Link to outcome is
`ai_signal_log.order_id → orders.id → strategy_positions.opening_order_id`.

### 1.5 What is NOT persisted — this is the important part

| Field | Status |
|---|---|
| **MFE / MAE** (max favourable / adverse excursion) | **Not persisted anywhere.** Grep across the repo finds MFE/MAE only in `social-listener/app/backtest_replay.py`, an offline replay script. No live trade records it. |
| **Any price series / candles** | **Not stored.** No OHLC table exists in Postgres. Redis holds only short-lived `cvd:*` keys. MFE/MAE cannot be reconstructed after the fact. |
| **Slippage** | **Not stored as a field.** `orders.actual_fill_price` vs `orders.price` exists for limit orders, but market orders carry no intended reference price, so entry slippage is not measurable for the 148 market entries. |
| **Funding paid/received** | **Not persisted per position.** No column anywhere. |
| `ai_signal_log.outcome_pnl` / `outcome_pct` / `outcome_filled_at` | Columns exist, **0 of 5135 rows populated**. The AI→outcome feedback loop was never wired. Outcomes must be reconstructed by join. |
| `strategy_performance`, `strategy_stats` | 0 rows. Never written. |
| Full LLM input payload | Not stored. Only `data_sources_used` (config flags) and `missing_inputs` (delivery check, from 2026-07-11). The exact prompt sent is not recoverable. |

---

## 2. Headline finding

**The system does not have an inverted edge. It has no edge at all, and then it pays fees.**
Across all 158 closed positions with a computable R-multiple, the gross win rate is 46.8%
(74/158) against a break-even requirement of 47.6% at the observed reward:risk. Gross
expectancy is **-0.009 R per trade**, with a bootstrap 95% confidence interval of
[-0.133, +0.123] — dead centre on zero. A binomial test against the break-even win rate
returns **p = 0.87**. This is a coin flip, not an inverted signal, so there is nothing to
invert.

The realized loss of **-149.31 USDT over 163 closed positions** comes from three places, none
of which is "the model calls direction backwards": round-trip fees costing **0.13 R per
trade** on average (up to 0.31 R on the BNB scalper), position sizing that put more risk on
the losing trades (risk-weighted gross result is **-0.152 R** versus -0.009 R equal-weighted),
and a small number of oversized stop-outs on non-AI test strategies (`tv-btc-test-hl-94e1`
alone accounts for -82.65 USDT of the -149.31 total).

The AI engine on its own is the one slice where the *money* loss is statistically real:
-0.51 USDT per trade over N=82, p = 0.020, 95% CI [-0.95, -0.11]. But its *gross directional*
expectancy is -0.042 R with CI [-0.217, +0.145] — still indistinguishable from random. The
loss is the cost structure biting a zero-edge signal, not a bad signal.

---

## 3. Analyses

### 3.1 Baseline

**Signal funnel (AI engine, whole period):**

```
 AI signals logged                         | 5135
 AI signals dry_run=true                   |  284
 AI signals with an action                 | 4292
 AI action = hold                          | 3673
 AI entry proposals (open_*/place_limit_*) |  189
   ... gate_passed                         |  128
   ... webhook_fired                       |  119
   ... order created                       |  119
   ... became a position                   |   84
   ... position closed                     |   82
```

843 of 5135 cycles (16.4%) produced no action at all — every one of them has
`gate_rejection_reason = 'llm_failed'`. Of the 189 entry proposals, 61 were blocked by the
guard: 31 `cooldown_active`, 30 `confidence_below_threshold`.

**Closed positions and P&L, all sources (N=163):**

```
               grp               |  n  | wins | winrate_pct | net_usdt | avg_win | avg_loss | exp_usdt
---------------------------------+-----+------+-------------+----------+---------+----------+----------
 AI engine                       |  82 |   30 |        36.6 |   -41.83 |   0.882 |   -1.313 |   -0.510
 ALL                             | 163 |   67 |        41.1 |  -149.31 |   1.895 |   -2.878 |   -0.916
 signal engine (other)           |   1 |    0 |         0.0 |    -0.24 |         |   -0.241 |   -0.241
 signal engine (tv_test_harness) |  59 |   30 |        50.8 |    -4.22 |   0.857 |   -1.032 |   -0.072
 tradingview/manual              |  21 |    7 |        33.3 |  -103.02 |  10.680 |  -12.699 |   -4.906
```

**In R multiples (N=158 — 5 positions have no `closing_price` and are excluded):**

```
               grp               | n_r | wins_r | winrate_r | avg_r_win | avg_r_loss | expectancy_r | total_r | sd_r
---------------------------------+-----+--------+-----------+-----------+------------+--------------+---------+-------
 AI engine                       |  81 |     37 |      45.7 |     0.637 |     -0.614 |       -0.042 |   -3.43 | 0.849
 ALL                             | 158 |     74 |      46.8 |     0.655 |     -0.595 |       -0.009 |   -1.49 | 0.833
 signal engine (tv_test_harness) |  56 |     30 |      53.6 |     0.625 |     -0.500 |        0.103 |    5.75 | 0.716
 tradingview/manual              |  20 |      7 |      35.0 |     0.881 |     -0.767 |       -0.190 |   -3.80 | 1.065
```

R is defined as `(exit − entry) / |entry − opening-order stop|`, signed by direction, using
`orders.actual_fill_price` as entry where present. Sign of R agrees with sign of net P&L on
146 of 156 comparable trades; the 10 disagreements are all sub-0.25 R moves where fees
flipped the sign, which is expected.

**Coverage is continuous.** Every one of the 40 days has at least one AI signal cycle; no
gaps. Position counts per day range 0–15 (see Appendix A.6). Signal volume rises sharply from
~20/day in late June to ~250/day after 2026-07-09, as more strategies came online.

**Before vs after costs.** Fee data **is** persisted (`orders.exchange_fee`) but **coverage is
incomplete**:

```
               grp               |  n  | gross_pnl | fees_recorded | net_pnl | missing_open_fee | missing_close_fee
---------------------------------+-----+-----------+---------------+---------+------------------+-------------------
 AI engine                       |  82 |    -25.23 |         15.59 |  -41.83 |                2 |                17
 ALL                             | 163 |   -134.68 |         21.00 | -149.31 |               61 |                78
```

61/163 positions have no opening fee and 78/163 no closing fee. `order-listener/app/reconciler.py:615`
documents the cause: `exchange_fee` stays NULL on any order that does not fill immediately.
**Therefore the true cost of trading is higher than the 21.00 USDT recorded**, and
`pnl_realized` is optimistic on those rows. Funding is not captured at all.

### 3.2 Is it actually worse than random?

**No. It is indistinguishable from random.** This is the most important result in the report.

```
=== ALL closed positions ===
  N(R)=158  wins=74  win_rate=0.468
  Wilson 95% CI          : [0.392, 0.546]
  Clopper-Pearson 95% CI : [0.389, 0.549]
  avg R win=+0.655  avg R loss=-0.595  break-even win rate=0.476
  binomial p vs 50%            = 0.4741
  binomial p vs break-even 0.476 = 0.8737
  expectancy R = -0.0094  sd=0.833  t=-0.142  p=0.8868
  bootstrap 95% CI expectancy R : [-0.1332, +0.1226]
  N(USDT)=163  net=-149.31  per-trade=-0.9160  t=-1.767  p=0.0773
  bootstrap 95% CI USDT/trade   : [-1.9382, +0.1307]

=== AI engine only ===
  N(R)=81  wins=37  win_rate=0.457
  Wilson 95% CI          : [0.353, 0.565]
  Clopper-Pearson 95% CI : [0.346, 0.571]
  avg R win=+0.637  avg R loss=-0.614  break-even win rate=0.491
  binomial p vs 50%            = 0.5052
  binomial p vs break-even 0.491 = 0.5794
  expectancy R = -0.0424  sd=0.849  t=-0.449  p=0.6534
  bootstrap 95% CI expectancy R : [-0.2174, +0.1453]
  N(USDT)=82  net=-41.83  per-trade=-0.5101  t=-2.327  p=0.0200
  bootstrap 95% CI USDT/trade   : [-0.9543, -0.1121]
```

Reading these:

- The gross win rate confidence interval [0.39, 0.55] comfortably contains both 50% and the
  47.6% break-even point. **The sample cannot distinguish this system from a coin flip.**
- The *money* result for the AI engine **is** significantly negative (p = 0.020). That is not
  a contradiction: the directional call is a coin flip, and costs plus sizing turn a coin flip
  into a reliable small loss.
- **Inverting every trade would not produce a working system.** Inverted AI-engine gross
  expectancy would be +0.042 R. Aggregate round-trip cost is 0.117 R (risk-weighted) to
  0.167 R (median) per trade. Inverted-minus-costs is still negative, and that ignores the
  17 positions with no recorded closing fee. There is no edge here to flip.

To detect a genuine 0.1 R edge at this trade distribution (sd ≈ 0.83) with 80% power would
need roughly 540 closed trades. We have 158. **The sample is simply too small to say anything
except "no measurable edge".**

### 3.3 Decomposition — where is the loss coming from?

**(a) Directional accuracy.** Gross win rate 46.8% vs 47.6% break-even. Shortfall attributable
to direction: essentially zero (−0.009 R/trade, p = 0.89). **Direction is not the problem.**

**(b) Costs.** This is the largest identifiable, non-random drain.

```
        strategy_id         | n  | min_r | med_r | avg_r | max_r  | agg_r
----------------------------+----+-------+-------+-------+--------+-------
 eth-ai-34d2                | 16 | 0.026 | 0.103 | 1.188 | 17.439 | 0.098
 bnb-ai-scalper-edbb        | 27 | 0.118 | 0.433 | 0.765 |  7.394 | 0.306
 hype-breakout-da2e         |  4 | 0.047 | 0.237 | 0.269 |  0.558 | 0.145
 tv_test_harness            | 17 | 0.177 | 0.270 | 0.300 |  0.442 | 0.291
 sol-ai-6486                |  4 | 0.047 | 0.111 | 0.122 |  0.220 | 0.127
 tao-ai-range-rotation-d257 | 10 | 0.043 | 0.114 | 0.118 |  0.215 | 0.104
 ai-btc-6f8c                |  4 | 0.036 | 0.058 | 0.061 |  0.094 | 0.049
```

(`agg_r` = total fees ÷ total risk deployed, the robust measure; `avg_r` is inflated by a few
positions with a near-zero stop distance.) N here is only the 84 positions with **both** fees
recorded.

**`bnb-ai-scalper-edbb` pays 0.31 R in fees per trade** and `tv_test_harness` 0.29 R. A
strategy whose average winner is +0.64 R and which pays 0.31 R round-trip needs a ~57% win
rate before it makes a cent. It ran at 40.7%.

**(c) Sizing.** Equal-weighted gross expectancy is -0.009 R. Risk-weighted it is -0.233 R:

```
    grp    |  n  | net_usdt | risk_usdt | rw_net_r | rw_gross_r
-----------+-----+----------+-----------+----------+------------
 AI engine |  82 |   -41.83 |    165.77 |   -0.252 |     -0.152
 other     |  81 |  -107.48 |    411.15 |   -0.261 |     -0.266
 ALL       | 163 |  -149.31 |    576.92 |   -0.259 |     -0.233
```

The gap between -0.009 (equal-weighted) and -0.233 (risk-weighted) means **the larger bets
lost and the smaller bets won**. With a zero-edge signal that is pure variance — but it is
also why the account curve looks much worse than the trade statistics do.

**(d) Stop placement.** The R distribution of AI-engine trades:

```
        bucket         | n  |  net   |  avgr
-----------------------+----+--------+--------
 a >=0.9R              | 11 |  14.86 |  1.545
 b 0.4..0.9R           |  6 |   6.30 |  0.620
 c 0..0.4R             | 21 |   4.96 |  0.136
 d -0.4..0R            | 17 | -10.90 | -0.182
 e -0.9..-0.4R         | 11 | -15.22 | -0.624
 f <=-0.9R (full stop) | 15 | -40.61 | -1.137
```

Two things stand out. **Full stop-outs average -1.137 R, not -1.0 R** — stops are slipping
about 14% past their intended level, which is a real, measurable 0.14 R of extra cost on 15
trades. And **21 trades sit in the 0..0.4 R bucket for a combined +4.96 USDT gross** — winners
are being cut before they cover their own fees. By close reason:

```
    close_reason    | n  | w  |  expr  |  net
--------------------+----+----+--------+--------
 signal_close       | 34 | 14 |  0.219 |   0.49
 (null)             | 13 |  9 |  0.117 |   6.21
 Closed on exchange | 35 |  7 | -0.365 | -48.52
```

**Whether trades that got stopped would have gone on to reach target cannot be answered.**
MFE/MAE is not persisted and no price history is stored, so there is no way to reconstruct it.
This is the single highest-value missing instrumentation in the system.

**(e) Fill / adverse selection.** Limit entry orders that reached the exchange:
17 filled, 19 cancelled, 1 still pending — a **46% fill rate** (17/37). Rejected and
conflict-blocked orders never reached the market and are excluded.

```
 order_type |  n  | wins |  wr  |  expr  |   net
------------+-----+------+------+--------+---------
 limit      |  15 |    3 | 20.0 | -0.384 |  -25.31
 market     | 148 |   64 | 43.2 |  0.027 | -124.00
```

AI-engine only, tested:

```
  limit   N=14 wins=4 wr=0.286 CP95=[0.084,0.581] expR=-0.384 p_vs_50%=0.1796
  market  N=67 wins=33 wr=0.493 CP95=[0.368,0.618] expR=+0.029 p_vs_50%=1.0000
```

This is the classic adverse-selection signature — limits fill precisely when price is running
through them — but **N=14 and p=0.18. It does not reach significance.** Treat it as a
hypothesis. The performance of the 19 *unfilled* limit proposals cannot be evaluated: no price
data is retained after cancellation.

### 3.4 Does the model's confidence discriminate?

**Weak positive trend, not statistically significant. N is too small to act on.**

```
Confidence vs R multiple, N=81
  Spearman rho = +0.1631   t=+1.469   approx p = 0.1417
Confidence vs net USDT, N=82
  Spearman rho = +0.1997   t=+1.823   approx p = 0.0684

Buckets (R-multiple basis):
  a <0.70      N= 7  wins= 3  wr=0.429  Wilson95=[0.158,0.750]  expR=-0.323
  b 0.70-0.74  N=37  wins=13  wr=0.351  Wilson95=[0.218,0.512]  expR=-0.116
  c 0.75-0.79  N=27  wins=15  wr=0.556  Wilson95=[0.373,0.724]  expR=-0.071
  d >=0.80     N=10  wins= 6  wr=0.600  Wilson95=[0.313,0.832]  expR=+0.504

High (conf>=0.78) N=25 expR=+0.1456   Low (<0.78) N=56 expR=-0.1263
  Welch t=+1.334  approx p=0.1823
```

The bucket means are monotone, which is more than nothing — but every Wilson interval overlaps
every other one, the top bucket is **N=10**, and the high-vs-low split fails at p = 0.18. The
honest statement is: **confidence might discriminate, and the data cannot tell us.** It is the
one hypothesis in this report worth deliberately powering up.

**Confidence is clustered in a very narrow band.** Across all 5135 signals: median 0.55,
IQR 0.50–0.65, sd 0.219 (N=3959 non-null; 1176 rows are NULL, mostly `llm_failed`). Across the
189 *entry* proposals — the ones that matter — the entire range is 0.60 to 0.90, and 50 of 189
sit on exactly 0.700, 38 on 0.780, 24 on 0.750:

```
 confidence | count
------------+-------
      0.600 |     3
      0.620 |     1
      0.650 |    11
      0.680 |    14
      0.700 |    50
      0.710 |     1
      0.720 |    15
      0.730 |     4
      0.750 |    24
      0.780 |    38
      0.800 |    19
      0.850 |     3
      0.900 |     6
```

The models are emitting round numbers from a habitual band, not a graded belief. Anything
below 0.60 is invisible because the guard's `confidence_below_threshold` rejects it (30 cases),
so the observed distribution is also truncated from below.

### 3.5 Slices for surviving pockets

**Every slice below is under-powered.** With 82 AI trades split across 6 symbols, 8 templates,
11 model/tier combinations, 3 sessions and 7 weekdays, that is ~35 comparisons on the same
data. At a 5% threshold roughly two "findings" are expected from noise alone. Nothing here
should be treated as a result.

**By symbol (AI engine, N=82):**
```
  symbol   | n  | w  |  expr  |  net
-----------+----+----+--------+--------
 TAO-USDT  | 10 |  4 |  0.271 |   0.75
 BNB-USDT  | 27 |  8 |  0.042 |  -7.60
 BTC-USDT  | 19 | 10 |  0.006 |  -1.18
 ETH-USDT  | 16 |  5 | -0.189 | -17.35
 SOL-USDT  |  4 |  1 | -0.288 |  -7.12
 HYPE-USDT |  6 |  2 | -0.537 |  -9.32
```
TAO is the only positive-P&L symbol at **N=10 — noise level, do not act on it.**

**By direction — the asymmetry check the brief asked for:**
```
=== AI engine LONG ===   N(R)=45  win_rate=0.467  expectancy R = -0.1154  CI [-0.3455, +0.1227]
                         N(USDT)=46  net=-34.04  per-trade=-0.7399  p=0.0201
=== AI engine SHORT ===  N(R)=36  win_rate=0.444  expectancy R = +0.0489  CI [-0.2228, +0.3459]
                         N(USDT)=36  net=-7.79   per-trade=-0.2164  p=0.4511
=== ALL LONG ===         N(R)=86  win_rate=0.465  expectancy R = -0.0648  CI [-0.2489, +0.1202]
=== ALL SHORT ===        N(R)=72  win_rate=0.472  expectancy R = +0.0567  CI [-0.1183, +0.2412]
```
**There is no meaningful long/short asymmetry.** The R confidence intervals overlap almost
completely. Longs lose more money mainly because they were sized larger (46 longs, -34.04 USDT
vs 36 shorts, -7.79 USDT), not because the calls were worse.

**By session (AI engine, opened_at UTC):**
```
      session       | n  | w  |  expr  |  net
--------------------+----+----+--------+--------
 00-08 UTC (Asia)   | 32 | 14 |  0.101 |  -5.50
 08-14 UTC (Europe) | 20 |  8 | -0.074 |  -5.63
 14-24 UTC (US)     | 30 |  8 | -0.179 | -30.69
```

**By weekday (AI engine):** best Sat (N=7, +0.316 R), worst Tue (N=5, -0.474 R). **Both are
noise. Five-trade and seven-trade cells carry no information.**

**By prompt template (AI engine):**
```
 prompt_template | n  | w |  expr  |  net
-----------------+----+---+--------+--------
 range_rotation  | 10 | 4 |  0.271 |   0.75
 regime_router   |  4 | 2 |  0.103 |  -0.24
 scalper         | 25 | 7 |  0.073 |  -2.70
 geometric_range | 22 | 8 | -0.152 | -21.24
 mean_reversion  | 15 | 7 | -0.207 |  -6.36
 trend_following |  4 | 1 | -0.288 |  -7.12
 flow_swing      |  2 | 1 | -0.346 |  -4.90
```

**By model / provider / tier (AI engine):**
```
 llm_provider |        llm_model        |    llm_tier     | n  | w  |  expr  |  net
--------------+-------------------------+-----------------+----+----+--------+--------
 groq         | llama-3.3-70b-versatile |                 |  4 |  3 |  1.121 |   4.98
 openrouter   | openai/gpt-oss-20b:free | premium         |  5 |  3 |  0.764 |   1.16
 cerebras     | gemma-4-31b             | fallback        |  5 |  2 |  0.305 |  -7.17
 openrouter   | openai/gpt-oss-20b:free | scout_escalated |  6 |  2 |  0.084 |  -0.56
 google       | gemini-2.5-flash        |                 | 27 | 12 | -0.140 |  -8.20
 cerebras     | gpt-oss-120b            | fallback        |  4 |  2 | -0.192 |  -4.98
 zhipu        | glm-4.5-air             | scout_escalated | 20 |  3 | -0.193 | -11.73
 cerebras     | gpt-oss-120b            | premium         |  4 |  1 | -0.288 |  -7.12
 google       | gemini-2.5-flash        | premium         |  2 |  1 | -0.331 |  -0.80
 zhipu        | glm-4.5-air             | fallback        |  3 |  1 | -0.729 |  -2.12
 zhipu        | glm-4.5                 | premium         |  2 |  0 | -0.750 |  -5.28
```
Eleven cells, largest N=27, most N≤6. **Nothing here is interpretable.** The apparent
llama-3.3-70b result is four trades.

**No market-regime data is persisted per trade.** `volatility_regime` and `funding_rate` were
fetched for the prompt but never written to `ai_signal_log` — only the *names* of the sources
are stored, not their values (`geometry_data` is the sole exception, present on 1016 rows).
**Regime slicing is impossible with the current schema.**

**Social listener:** live since 2026-07-26, **one closed position**. N=1 — insufficient data,
no statistics computed.

### 3.6 Data quality audit

**Completeness on closed positions (N=163):**
```
 closed | has_open_order | has_sl | has_tp | has_cp | has_open_fee | has_close_fee | has_fill
--------+----------------+--------+--------+--------+--------------+---------------+----------
    163 |            163 |    163 |    149 |    158 |          102 |            79 |      163
```
Every closed position has an opening order, a stop and a fill price. 5 lack a
`closing_price` (excluded from all R analysis). 14 lack a take-profit. **Fee coverage is the
weak spot: 63% open, 48% close.**

**Which of the nine plumbed data fields actually reached the LLM.** `data_sources_used`
reflects config flags only (`node_dispatch.py:13`); `missing_inputs` is the real delivery
check (`node_dispatch.py:63`, a truthiness test on the fetched value). `missing_inputs` was
added by migration 052 and first written **2026-07-11** — 1093 signals before that date have
NULL and **are excluded from this analysis**. Over the 4041 signals that do have it:

```
         src         | requested | missing | delivered_pct
---------------------+-----------+---------+---------------
 economic_calendar   |       161 |     161 |           0.0
 fear_greed          |      3621 |     772 |          78.7
 geometry            |       883 |      81 |          90.8
 momentum_divergence |      1112 |      76 |          93.2
 volume_profile      |      2335 |     155 |          93.4
 volatility_regime   |      1876 |      93 |          95.0
 technical           |      4041 |     189 |          95.3
 funding_rate        |      4041 |     101 |          97.5
 mtf_structure       |      2113 |      49 |          97.7
 open_interest       |      4041 |      72 |          98.2
 liquidations        |      1230 |      20 |          98.4
 funding_history     |      2338 |      13 |          99.4
 orderbook           |      3339 |      18 |          99.5
 news                |      3621 |       3 |          99.9
 cvd                 |      3343 |       0 |         100.0
 limit_orders        |      1345 |       0 |         100.0
```

Answering the brief's two specific questions:

- **`economic_calendar` was empty throughout — 161 requests, 161 misses, 0.0% delivered.**
  It was only ever enabled 2026-07-11 → 2026-07-12 and never delivered a single payload. The
  cause is documented in `docs/ROADMAP.md`: the Finnhub economic-calendar endpoint is
  paid-tier and returns 403 on the free key. **All 161 of those signals were generated from a
  prompt whose SCHEDULED EVENTS section was absent.** (The renderer omits the section rather
  than printing an empty one, so the model was not shown a lying header — but the prompt
  design assumed data that never arrived.)
- **`liquidations` was NOT empty.** 1230 requests, 98.4% delivered, from 2026-07-11 onward.
  The ROADMAP note saying `use_liquidations` is false on every strategy is out of date.
- **`fear_greed` failed 21% of the time (772 misses)** — by a wide margin the least reliable
  field that is actually enabled. Worth a look on its own.

**Schema/behaviour changes mid-period that make early and late data non-comparable:**

```
         src         | first_seen | last_seen  |  n
---------------------+------------+------------+------
 btc_dominance       | 2026-06-19 | 2026-07-05 |  196
 fear_greed          | 2026-06-19 | 2026-07-28 | 4511
 funding_rate        | 2026-06-19 | 2026-07-28 | 5125
 news                | 2026-06-19 | 2026-07-28 | 4511
 open_interest       | 2026-06-19 | 2026-07-28 | 5125
 technical           | 2026-06-19 | 2026-07-28 | 5125
 geometry            | 2026-07-04 | 2026-07-28 | 1274
 cvd                 | 2026-07-11 | 2026-07-28 | 3359
 economic_calendar   | 2026-07-11 | 2026-07-12 |  173
 funding_history     | 2026-07-11 | 2026-07-28 | 2349
 limit_orders        | 2026-07-11 | 2026-07-28 | 1354
 liquidations        | 2026-07-11 | 2026-07-28 | 1233
 momentum_divergence | 2026-07-11 | 2026-07-28 | 1121
 mtf_structure       | 2026-07-11 | 2026-07-28 | 2126
 orderbook           | 2026-07-11 | 2026-07-28 | 3354
 volatility_regime   | 2026-07-11 | 2026-07-28 | 1888
 volume_profile      | 2026-07-11 | 2026-07-28 | 2350
```

**2026-07-11 is a hard break.** Eleven data fields switched on that day (the Wave 1–4 cutover).
Before it the model saw 6–7 inputs; after it, up to 18. `btc_dominance` was switched off on
2026-07-05 and never returned. Prompt templates also arrived staggered across the period
(`scalper` from 2026-07-09, `flow_swing` from 2026-07-25). Splitting closed AI trades on that
boundary:

```
               era                | n  | w  |  wr  |  expr  |  net
----------------------------------+----+----+------+--------+--------
 era1 pre-2026-07-11 (7 fields)   | 26 | 12 | 46.2 | -0.090 |  -5.64
 era2 from 2026-07-11 (18 fields) | 56 | 18 | 32.1 | -0.021 | -36.19
```

**Adding eleven data fields did not change gross expectancy** (-0.090 R → -0.021 R, both
statistically zero at these N). It is worth knowing that a large plumbing investment produced
no measurable signal improvement — though with N=26 and N=56 this cannot rule out a small one.

**LLM reliability by model** (`gate_rejection_reason='llm_failed'`, all 5135 signals):
```
 llm_provider |          llm_model           |  n  | failed | fail_pct
--------------+------------------------------+-----+--------+----------
 openrouter   | mistralai/mistral-medium-3-5 |  33 |     33 |    100.0
 openrouter   | openai/gpt-oss-20b:free      | 374 |    244 |     65.2
 google       | gemini-flash-latest          |  87 |     56 |     64.4
 openrouter   | tencent/hy3                  |  21 |     12 |     57.1
 zhipu        | glm-4.5                      | 221 |     90 |     40.7
 openrouter   | poolside/laguna-xs-2.1:free  |  53 |     20 |     37.7
 groq         | llama-3.3-70b-versatile      | 921 |    255 |     27.7
 google       | gemini-2.5-flash             | 955 |    101 |     10.6
 cerebras     | zai-glm-4.7                  | 159 |      6 |      3.8
 groq         | llama-3.1-8b-instant         | 360 |      3 |      0.8
 cerebras     | gpt-oss-120b                 | 512 |      1 |      0.2
 google       | gemini-flash-lite-latest     |  68 |      0 |      0.0
 anthropic    | claude-haiku-4-5-20251001    |  59 |      0 |      0.0
 openrouter   | tencent/hy3:free             | 334 |      0 |      0.0
 zhipu        | glm-4.5-air                  | 697 |      0 |      0.0
 anthropic    | claude-sonnet-4-5-20250929   |  52 |      0 |      0.0
 cerebras     | gemma-4-31b                  | 157 |      0 |      0.0
```
`mistralai/mistral-medium-3-5` failed 33/33. `openai/gpt-oss-20b:free` failed 244/374. These
cycles cost API budget and produced nothing.

---

## 4. Questions the data cannot answer

| Question | Why not | Minimum instrumentation to fix it |
|---|---|---|
| **Would stopped-out trades have reached their target?** | MFE/MAE not persisted; no price history retained. This is the highest-value gap. | Add `mfe_price`, `mae_price`, `mfe_r`, `mae_r` to `strategy_positions`, updated on a poll while the position is open. Cheap: one mark-price read per position per cycle. |
| **Are the stops too tight or too wide?** | Same reason. Without excursion data, stop tuning is guesswork. | Same as above. |
| **How much did entry slippage cost?** | Market orders store no intended reference price. | Persist the decision-time mark price on the order (`intended_price`) alongside `actual_fill_price`. `orders.indicator_price` exists but is unused. |
| **How much did funding cost?** | Never captured per position. | Add `funding_paid` to `strategy_positions`, accumulated from the exchange's funding history at close. |
| **What were the *true* fees?** | 61/163 positions have no opening fee, 78/163 no closing fee, because `exchange_fee` is only written on immediate fill. `pnl_realized` COALESCEs the gap to zero. | Backfill fees from the exchange fill record at position close, not at order-ack time. |
| **Did the 19 unfilled limit proposals turn out to be right?** | No price data retained after cancellation. | Record mark price at cancellation and at +1h/+4h for cancelled proposals. |
| **Does market regime affect performance?** | Regime *values* are never persisted — only the names of the sources requested. | Snapshot the numeric values (`volatility_regime`, `funding_rate`, BTC trend) into a JSONB column on `ai_signal_log` at decision time. |
| **What exactly was the model shown?** | The assembled prompt is not stored; `missing_inputs` only exists from 2026-07-11. | Store a hash plus the rendered context (or at minimum a per-field non-null map) on every `ai_signal_log` row. |
| **Does confidence discriminate?** | N=81, p=0.14. Under-powered, not unanswerable. | Nothing new needed — just ~300 more closed trades with confidence recorded, which already happens. |
| **Is there a real edge anywhere?** | N=158 against sd 0.83 R. Would need ~540 trades for 80% power at 0.1 R. | Nothing new needed — time and trade count. |

---

## 5. What this implies

**No edge is present, and no promising pocket was found.** That is the honest reading of 158
closed trades. The direction calls are a coin flip (46.8% vs a 47.6% break-even, p = 0.87),
and no symbol, side, session, template or model slice survives contact with its own sample
size. The one slice that even hints at something — trades where the model stated confidence
≥ 0.78 — is N=25 at p = 0.18.

Three practical consequences follow:

1. **Do not invert the system.** The most valuable outcome the brief hoped for is ruled out.
   Inverted gross expectancy would be +0.042 R against 0.12–0.17 R of round-trip cost. A
   reliably-wrong system would be worth having; this one is reliably *nothing*.

2. **The cost structure is currently the only thing that is statistically real.** Fees of
   0.13 R per trade average and 0.31 R on the BNB scalper, plus 0.14 R of slippage past
   stops, plus incomplete fee capture that hides more of it. A strategy trading a zero-edge
   signal at 0.3 R per round trip is guaranteed to lose. Whatever else happens, either the
   stop distances get much wider relative to fees, or the scalper-style strategies stop
   trading.

3. **The measurement system is the bottleneck, not the model.** Six of the ten open questions
   above are unanswerable purely because MFE/MAE and a decision-time price snapshot are not
   recorded. Adding them is a small change and would convert the next 158 trades into a
   dataset that can actually diagnose stop placement, entry timing and regime dependence.
   Adding eleven more data fields to the prompt on 2026-07-11 moved gross expectancy from
   -0.090 R to -0.021 R — which is to say, not at all. More inputs to the model is the lever
   that has already been pulled and did not work; better outcome instrumentation has not been.

One caveat worth restating: the AI engine has been live for 40 days and produced 82 closed
trades. That is early. "No edge detected" at this N is genuinely different from "no edge
exists" — it means the experiment has not run long enough to have an opinion, and the fastest
way to get one is to cut the cost per trade so the signal has room to show itself, and to
record the excursion data so a null result can be diagnosed rather than just observed.

---

## Appendix A — Verification output

### A.1 `ls db/migrations | tail -20`

```
051_geometric_range_moderate_fit.sql
052_ai_signal_log_missing_inputs.sql
053_llm_fallback_and_scout_tiering.sql
054_risk_unit_sizing.sql
055_soften_template_hold_gates.sql
056_llm_keys.sql
057_funding_harvest_plans.sql
058_spread_plans.sql
059_spread_positions.sql
060_ai_close_gate.sql
061_ai_sizing_retune.sql
062_social_signal_log_image.sql
063_social_extraction_cache.sql
064_social_merged_msg_ids.sql
065_order_price_history.sql
066_social_partial_close.sql
067_social_stop_management.sql
068_social_add_and_levels.sql
_archive
README.md
```

### A.2 Row counts and date ranges for every table used

```sql
SELECT 'ai_signal_log' t, count(*), min(triggered_at)::date, max(triggered_at)::date FROM ai_signal_log
UNION ALL SELECT 'orders', count(*), min(received_at)::date, max(received_at)::date FROM orders
UNION ALL SELECT 'strategy_positions', count(*), min(opened_at)::date, max(opened_at)::date FROM strategy_positions
UNION ALL SELECT 'signal_log', count(*), min(received_at)::date, max(received_at)::date FROM signal_log
UNION ALL SELECT 'shadow_signals', count(*), min(generated_at)::date, max(generated_at)::date FROM shadow_signals
UNION ALL SELECT 'social_signal_log', count(*), min(posted_at)::date, max(posted_at)::date FROM social_signal_log
UNION ALL SELECT 'order_execution_log', count(*), null, null FROM order_execution_log
UNION ALL SELECT 'strategy_performance', count(*), null,null FROM strategy_performance
UNION ALL SELECT 'spread_positions', count(*), null,null FROM spread_positions
UNION ALL SELECT 'funding_harvest_plans', count(*), null,null FROM funding_harvest_plans
UNION ALL SELECT 'order_price_history', count(*), null,null FROM order_price_history
UNION ALL SELECT 'strategy_stats', count(*), null,null FROM strategy_stats
UNION ALL SELECT 'social_shadow_orders', count(*), null,null FROM social_shadow_orders;
```

```
           t           | count |    min     |    max
-----------------------+-------+------------+------------
 ai_signal_log         |  5134 | 2026-06-19 | 2026-07-28
 orders                |   507 | 2026-06-19 | 2026-07-28
 strategy_positions    |   166 | 2026-06-19 | 2026-07-28
 signal_log            |   463 | 2026-06-19 | 2026-07-28
 shadow_signals        |   529 | 2026-06-20 | 2026-07-27
 social_signal_log     |   270 | 2026-06-11 | 2026-07-27
 order_execution_log   |   220 |            |
 strategy_performance  |     0 |            |
 spread_positions      |     3 |            |
 funding_harvest_plans |     2 |            |
 order_price_history   |    84 |            |
 strategy_stats        |     0 |            |
 social_shadow_orders  |    23 |            |
(13 rows)
```

(`ai_signal_log` shows 5134 here and 5135 in later queries — one signal was written between
the two runs. The stack is live; this is expected drift, not an inconsistency.)

### A.3 The `base` CTE used by every P&L query

All P&L and R analysis derives from this one CTE, referred to below as `base`:

```sql
WITH base AS (
  SELECT p.id, p.strategy_id, s.strategy_source, p.symbol, p.side,
         p.opened_at, p.closed_at, p.close_reason,
         COALESCE(oo.actual_fill_price, p.entry_price) AS entry,
         p.closing_price AS exitp,
         oo.sl_price, oo.tp_price, oo.order_type,
         p.size, p.leverage, p.pnl_realized,
         oo.exchange_fee AS open_fee, oo.received_at AS order_at,
         (SELECT SUM(c.exchange_fee) FROM orders c WHERE c.closes_position_id=p.id) AS close_fee,
         (SELECT SUM(c.pnl) FROM orders c WHERE c.closes_position_id=p.id) AS gross_pnl
  FROM strategy_positions p
  JOIN strategies s ON s.id = p.strategy_id
  LEFT JOIN orders oo ON oo.id = p.opening_order_id
  WHERE p.status = 'closed'
),
r AS (
  SELECT b.*,
    CASE WHEN sl_price IS NULL OR entry IS NULL OR sl_price = entry THEN NULL
         ELSE abs(entry - sl_price) END AS risk_dist,
    CASE WHEN exitp IS NULL OR entry IS NULL THEN NULL
         WHEN side='long' THEN exitp - entry ELSE entry - exitp END AS move
  FROM base b
)
SELECT *, CASE WHEN risk_dist IS NULL OR move IS NULL THEN NULL
               ELSE round((move/risk_dist)::numeric,3) END AS r_mult
FROM r
```

Coverage check on it:

```
 closed | has_r | has_pnl | no_exit | no_risk
--------+-------+---------+---------+---------
    163 |   158 |     163 |       5 |       0
```

R-sign vs P&L-sign agreement:

```
  n  | agree | disagree
-----+-------+----------
 156 |   146 |       10
```

### A.4 Headline win rate and expectancy — backing query and output

```sql
WITH t AS (<base>)
SELECT grp, count(*) n,
  count(*) FILTER (WHERE pnl_realized>0) wins,
  round(100.0*count(*) FILTER (WHERE pnl_realized>0)/count(*),1) winrate_pct,
  round(sum(pnl_realized)::numeric,2) net_usdt,
  round(avg(pnl_realized) FILTER (WHERE pnl_realized>0)::numeric,3) avg_win,
  round(avg(pnl_realized) FILTER (WHERE pnl_realized<=0)::numeric,3) avg_loss,
  round(avg(pnl_realized)::numeric,3) exp_usdt
FROM (SELECT *, CASE WHEN strategy_source='ai_engine' THEN 'AI engine'
                     WHEN strategy_id='tv_test_harness' THEN 'signal engine (tv_test_harness)'
                     WHEN strategy_source='signal_engine' THEN 'signal engine (other)'
                     ELSE 'tradingview/manual' END grp FROM t) x
GROUP BY grp
UNION ALL
SELECT 'ALL', count(*), count(*) FILTER (WHERE pnl_realized>0),
  round(100.0*count(*) FILTER (WHERE pnl_realized>0)/count(*),1),
  round(sum(pnl_realized)::numeric,2),
  round(avg(pnl_realized) FILTER (WHERE pnl_realized>0)::numeric,3),
  round(avg(pnl_realized) FILTER (WHERE pnl_realized<=0)::numeric,3),
  round(avg(pnl_realized)::numeric,3)
FROM t ORDER BY 1;
```

```
               grp               |  n  | wins | winrate_pct | net_usdt | avg_win | avg_loss | exp_usdt
---------------------------------+-----+------+-------------+----------+---------+----------+----------
 AI engine                       |  82 |   30 |        36.6 |   -41.83 |   0.882 |   -1.313 |   -0.510
 ALL                             | 163 |   67 |        41.1 |  -149.31 |   1.895 |   -2.878 |   -0.916
 signal engine (other)           |   1 |    0 |         0.0 |    -0.24 |         |   -0.241 |   -0.241
 signal engine (tv_test_harness) |  59 |   30 |        50.8 |    -4.22 |   0.857 |   -1.032 |   -0.072
 tradingview/manual              |  21 |    7 |        33.3 |  -103.02 |  10.680 |  -12.699 |   -4.906
(5 rows)
```

R-multiple version (same CTE, `r_mult` in place of `pnl_realized`) — output in §3.1.

Per-strategy detail:

```
        strategy_id         | n  | avg_risk_usdt | min_risk | max_risk |  net   |  expr
----------------------------+----+---------------+----------+----------+--------+--------
 tv-btc-test-hl-94e1        | 12 |        15.102 |    2.996 |   31.927 | -82.65 | -0.407
 hype-test-7db4             |  7 |        19.860 |   17.773 |   31.228 | -28.11 | -0.112
 eth-ai-34d2                | 16 |         2.275 |    0.010 |    7.784 | -17.35 | -0.189
 hype-breakout-da2e         |  6 |         4.431 |    0.867 |    8.298 |  -9.32 | -0.537
 bnb-ai-scalper-edbb        | 27 |         0.509 |    0.017 |    5.071 |  -7.60 |  0.042
 sol-ai-6486                |  4 |         3.536 |    2.026 |    4.976 |  -7.12 | -0.288
 tv_test_harness            | 59 |         1.167 |    0.690 |   11.941 |  -4.22 |  0.103
 ai-btc-6f8c                | 19 |         3.228 |    0.447 |   12.275 |  -1.18 |  0.006
 social-btc-astro           |  1 |         9.098 |    9.098 |    9.098 |  -0.24 |  0.000
 tao-ai-range-rotation-d257 | 10 |         1.357 |    0.549 |    2.808 |   0.75 |  0.271
 sui-manual-59d9            |  2 |         6.464 |    5.084 |    7.844 |   7.73 |  0.874
(11 rows)
```

### A.5 Statistical tests

Run with `python3` (no scipy available on this host — binomial PMF/CDF, Wilson,
Clopper-Pearson and the bootstrap are implemented directly, exactly, in
`stats.py` / `conf.py`; both scripts are reproduced below so results can be re-run).

`stats.py` core:

```python
def logC(n,k): return math.lgamma(n+1)-math.lgamma(k+1)-math.lgamma(n-k+1)
def binom_pmf(k,n,p): return math.exp(logC(n,k)+k*math.log(p)+(n-k)*math.log(1-p))
def binom_cdf(k,n,p): return sum(binom_pmf(i,n,p) for i in range(0,k+1))
def binom_test_two(k,n,p):
    obs=binom_pmf(k,n,p)
    return min(1.0, sum(binom_pmf(i,n,p) for i in range(n+1)
                        if binom_pmf(i,n,p) <= obs*(1+1e-9)))
def wilson(k,n,z=1.96):
    p=k/n; d=1+z*z/n; c=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return c-h, c+h
# Clopper-Pearson by 60-step bisection on binom_cdf; bootstrap = 4000 resamples, seed 7
```

Full output is quoted in §3.2 and §3.5. Break-even win rate is derived from the observed
payoff ratio as `-avgRloss / (avgRwin - avgRloss)`.

Confidence analysis (`conf.py`) uses a hand-rolled Spearman (Pearson on average ranks) and a
Welch t-test; output quoted in §3.4.

### A.6 Daily coverage (continuity check)

```
     dd     | sig | pos
------------+-----+-----
 2026-06-19 |   5 |   2
 2026-06-20 |  10 |   2
 2026-06-21 |   8 |   3
 2026-06-22 |   9 |   4
 2026-06-23 |  25 |   5
 2026-06-24 |  27 |   5
 2026-06-25 |  17 |   5
 2026-06-26 |  28 |  10
 2026-06-27 |  20 |   4
 2026-06-28 |  18 |   3
 2026-06-29 |  22 |  10
 2026-06-30 |  22 |   5
 2026-07-01 |  23 |   3
 2026-07-02 |  39 |   2
 2026-07-03 |  29 |   1
 2026-07-04 |  42 |   3
 2026-07-05 |  66 |  15
 2026-07-06 |  65 |   4
 2026-07-07 |  84 |   8
 2026-07-08 |  73 |   4
 2026-07-09 | 141 |   4
 2026-07-10 | 216 |   4
 2026-07-11 | 201 |   2
 2026-07-12 | 205 |   3
 2026-07-13 | 191 |   3
 2026-07-14 | 224 |   0
 2026-07-15 | 269 |   1
 2026-07-16 | 267 |   3
 2026-07-17 | 267 |   2
 2026-07-18 | 268 |   1
 2026-07-19 | 284 |   1
 2026-07-20 | 244 |   4
 2026-07-21 | 241 |   3
 2026-07-22 | 254 |   6
 2026-07-23 | 269 |  10
 2026-07-24 | 247 |   7
 2026-07-25 | 211 |   1
 2026-07-26 | 196 |   6
 2026-07-27 | 184 |   6
 2026-07-28 | 124 |   1
(40 rows)
```

Weekly aggregate, all strategies:

```
     wk     | n  | w  |  expr  |  net
------------+----+----+--------+--------
 2026-06-15 |  7 |  5 |  0.134 |  -1.33
 2026-06-22 | 36 | 15 | -0.184 | -89.71
 2026-06-29 | 39 | 17 |  0.032 | -18.93
 2026-07-06 | 29 | 15 |  0.263 |  -0.49
 2026-07-13 | 11 |  5 | -0.078 |  -6.82
 2026-07-20 | 36 |  9 | -0.005 | -14.82
 2026-07-27 |  5 |  1 | -0.650 | -17.20
```

### A.7 Cost queries

Cost expressed in R (fees ÷ risk deployed):

```sql
WITH t AS (<base>), c AS (
  SELECT strategy_id, (open_fee+close_fee)/(risk_dist*size) AS cost_r,
         risk_dist*size AS risk_usdt, open_fee+close_fee AS fee
  FROM t WHERE open_fee IS NOT NULL AND close_fee IS NOT NULL AND risk_dist>0)
SELECT strategy_id, count(*) n,
 round(min(cost_r)::numeric,3) min_R,
 round((percentile_cont(0.5) WITHIN GROUP (ORDER BY cost_r))::numeric,3) med_R,
 round(avg(cost_r)::numeric,3) avg_R,
 round(max(cost_r)::numeric,3) max_R,
 round((sum(fee)/sum(risk_usdt))::numeric,3) agg_R
FROM c GROUP BY 1 ORDER BY 7 DESC;
```

Aggregate:

```
    grp    | n  | fees  | total_risk | cost_per_1r | median_cost_r
-----------+----+-------+------------+-------------+---------------
 AI engine | 65 | 14.35 |     122.92 |       0.117 |         0.167
 other     | 19 |  5.07 |      28.21 |       0.180 |         0.262
 ALL       | 84 | 19.43 |     151.12 |       0.129 |         0.227
```

Risk-weighted gross vs net R (all 163 closed, missing fees counted as zero — so `rw_net_r`
is optimistic):

```sql
WITH t AS (<base>)
SELECT CASE WHEN strategy_source='ai_engine' THEN 'AI engine' ELSE 'other' END grp,
 count(*) n, round(sum(pnl_realized)::numeric,2) net_usdt,
 round(sum(risk_dist*size)::numeric,2) risk_usdt,
 round((sum(pnl_realized)/sum(risk_dist*size))::numeric,3) rw_net_R,
 round((sum(COALESCE(gross_pnl,0))/sum(risk_dist*size))::numeric,3) rw_gross_R
FROM t WHERE risk_dist>0 GROUP BY 1;
```

```
    grp    |  n  | net_usdt | risk_usdt | rw_net_r | rw_gross_r
-----------+-----+----------+-----------+----------+------------
 AI engine |  82 |   -41.83 |    165.77 |   -0.252 |     -0.152
 other     |  81 |  -107.48 |    411.15 |   -0.261 |     -0.266
 ALL       | 163 |  -149.31 |    576.92 |   -0.259 |     -0.233
```

### A.8 Data-quality queries

Delivery rate per data source (only rows where `missing_inputs` exists, i.e. from 2026-07-11):

```sql
WITH s AS (SELECT id, triggered_at, unnest(data_sources_used) src, missing_inputs
           FROM ai_signal_log WHERE missing_inputs IS NOT NULL)
SELECT src, count(*) requested,
       count(*) FILTER (WHERE src = ANY(missing_inputs)) missing,
       round(100.0*count(*) FILTER (WHERE NOT (src = ANY(missing_inputs)))/count(*),1) delivered_pct
FROM s GROUP BY 1 ORDER BY 4 ASC;
```

Rows excluded from that analysis:

```
 null_mi |    min     |    max
---------+------------+------------
    1093 | 2026-06-19 | 2026-07-11
```

`ai_signal_log` field population:

```
 total | has_outcome_pnl | has_outcome_pct | has_filled_at | has_reasoning | has_geom | has_fb
-------+-----------------+-----------------+---------------+---------------+----------+--------
  5135 |               0 |               0 |             0 |          4961 |     1016 |   1187
```

Exchange accounts — both are demo, confirming the whole period is paper trading:

```
              id              |  exchange   | mode |    label    | is_active
------------------------------+-------------+------+-------------+-----------
 blofin-blofin-demo-v5vr      | blofin      | demo | Blofin Demo | t
 hyperliquid-hyperliquid-hqdy | hyperliquid | demo | Hyperliquid | t
```

### A.9 Other queries quoted in the body

```sql
-- close reasons
SELECT close_reason, count(*), round(sum(pnl_realized)::numeric,2)
FROM strategy_positions WHERE status='closed' GROUP BY 1 ORDER BY 2 DESC;

-- limit-order fill rate
SELECT signal, order_type, status, count(*) FROM orders GROUP BY 1,2,3 ORDER BY 4 DESC;

-- confidence distribution on entry proposals
SELECT confidence, count(*) FROM ai_signal_log
WHERE proposed_action IN ('open_long','open_short','place_limit_long','place_limit_short')
GROUP BY 1 ORDER BY 1;

-- LLM failure rate by model
SELECT llm_provider, llm_model, count(*) n,
       count(*) FILTER (WHERE gate_rejection_reason='llm_failed') failed,
       round(100.0*count(*) FILTER (WHERE gate_rejection_reason='llm_failed')/count(*),1) fail_pct
FROM ai_signal_log GROUP BY 1,2 HAVING count(*)>20 ORDER BY 5 DESC;

-- first/last appearance of each data source
WITH s AS (SELECT triggered_at, unnest(data_sources_used) src FROM ai_signal_log)
SELECT src, min(triggered_at)::date, max(triggered_at)::date, count(*) FROM s GROUP BY 1 ORDER BY 2,1;
```

The AI-linked join used for §3.4 and §3.5 (confidence, model, template slices):

```sql
SELECT p.*, a.confidence, a.proposed_action, a.llm_provider, a.llm_model, a.llm_tier,
       a.prompt_template, a.trigger_reason, a.data_sources_used, a.missing_inputs
FROM strategy_positions p
JOIN strategies s ON s.id=p.strategy_id
JOIN orders oo ON oo.id=p.opening_order_id
JOIN ai_signal_log a ON a.order_id=oo.id
WHERE p.status='closed';
-- returns 82 rows, 82 with confidence, 81 with a computable r_mult
```
