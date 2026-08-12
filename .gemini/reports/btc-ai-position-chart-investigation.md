# BTC AI position chart — why it looks wrong

Investigation only. **No code was changed.**

Subject: `ai-btc-6f8c` ("BTC AI Range Rotation"), open position
`9c43a165-99bd-429f-a0ce-a56d99178796`, opening order
`e315b257-36f7-4df4-9681-12a45fe1dc33`.

Four complaints were reported, all four reproduce, and there are **two independent
root causes** behind them.

---

## The stored facts

```
$ docker compose exec -T postgres psql -U matp -d matp -x -c "SELECT ... FROM strategy_positions WHERE id='9c43a165-...'"
 strategy_id | ai-btc-6f8c
 exchange    | auto          (account: hyperliquid-hyperliquid-hqdy)
 symbol      | BTC-USDT   side | long   leverage | 20
 entry_price | 63918.4
 opened_at   | 2026-08-11 18:40:32.74+00
 mfe_price   | 64013.0     mae_price | 63062.0
```

```
$ ... orders WHERE id='e315b257-...'
 signal | open_long   order_type | market   status | filled
 tp_price               | 64236.305
 sl_price               | 62654.1
 actual_fill_price      | 63918.4
 mark_price_at_decision | 63786.0
 signal_metadata        | {"entry_ref": 63786.0, "sl_source": "strategy",
                           "sl_distance_pct": 1.7745, "ref_price_source": "exchange_mark",
                           "sizing": {"effective_risk_usd": 2.0, "margin_usd": 10.0,
                                      "risk_clamped_by_margin_cap": true}, ...}
```

Account modes:

```
$ ... exchange_accounts
 hyperliquid-hyperliquid-hqdy | hyperliquid | mode=demo
 blofin-blofin-demo-v5vr      | blofin      | mode=demo
```

`order-executor/app/adapters/hyperliquid.py:66` — `mode=demo` ⇒
`https://api.hyperliquid-testnet.xyz`. **This position lives on Hyperliquid
testnet.**

The chart's candles do not:

```
$ ... GET /positions/9c43a165-.../candles
 symbol BTC-USDT | exchange blofin | timeframe 15m | bar_seconds 900
 note: "Candles from blofin — hyperliquid is not ingested."
 overlay: entry 63918.4  stop 62654.1  target 64236.305  current 63425.6
```

`docker-compose.yml:230` — `INGESTION_EXCHANGE: blofin`, `INGESTION_MODE: live`.
So the chart shows **real-market** Blofin candles under **testnet** price levels.

---

## Root cause 1 — Hyperliquid testnet is a different market

Real market at the moment of entry (08-11 18:40), three independent venues:

```
== hyperliquid BTC/USDC:USDC (mainnet, public)     == binance BTC/USDT
   08-11 18:15 63563 63574 63448 63449               08-11 18:15 63589.98 63603.47 63475.20 63475.66
   08-11 18:30 63449 63484 63229 63279               08-11 18:30 63475.67 63506.68 63251.59 63307.68
   08-11 18:45 63279 63353 63278 63344               08-11 18:45 63307.67 63387.82 63306.49 63373.07

== blofin (what the chart draws)
   08-11 18:30 63463.1 63487.8 63226.0 63283.8
   08-11 18:45 63277.1 63363.7 63277.1 63352.5
```

Real BTC was **≈ 63 280**. Hyperliquid **testnet** at the same moment reported
mark **63 786** and filled the market order at **63 918.4** — about **1.0 %
higher than the real world**. Testnet drift is not constant; it flips sign. Today
at 17:30 it is *lower*: testnet mark 63 265 vs mainnet/blofin 63 447.

This alone explains complaints 1 and 3.

### 1. "The entry line doesn't cross the candles"

Entry line = 63918.4. Blofin candles for the 17 hours after the fill sit
400–700 below it:

```
bars after fill: 91
min low after fill  63211.5  (08-11 19:00)
max high after fill 64441.3  (08-12 12:30)
```

The line only gets touched a full day later. At the entry itself it floats ~640
above the bar it is supposed to start on. Nothing is drawn wrong — the two prices
come from two different markets.

### 3. "Candles go above TP but the trade did not close"

The TP is really parked on the exchange:

```
$ GET order-executor/accounts/hyperliquid-hyperliquid-hqdy/trigger-orders/BTC-USDT
[{"oid":57727487163,"tpsl":"sl","triggerPx":"62654.0","sz":"0.00314","side":"sell"},
 {"oid":57727487162,"tpsl":"tp","triggerPx":"64236.0","sz":"0.00314","side":"sell"}]
```

Hyperliquid **testnet** candles for the window where the chart shows a TP breach:

```
$ POST https://api.hyperliquid-testnet.xyz/info  candleSnapshot BTC 15m 08-12 10:00→14:00
   08-12 12:00 63955 63955 63836 63952
   08-12 12:15 63947 63953 63940 63953
   08-12 12:30 63953 63953 63940 63953
   max high 63955.0 (08-12 11:00)
```

Testnet never got closer than **281** to the 64 236 trigger. Blofin printed
64 441.3 at 12:30. The position's own excursion tracker agrees with the exchange:
`mfe_price = 64013.0`, below TP. **Not closing was correct.**

### Side effect: the P&L on the chart is also off-market

`overlay.current_price` = 63425.6 (last Blofin close) ⇒ chart shows −0.77 %.
Live exchange mark is 63 260 ⇒ real −1.03 %, `unrealized_pnl = -2.06757`.

---

## Root cause 2 — the AI priced TP/SL off mainnet, the order filled on testnet

`ai-signal-generator/app/data/ohlcv.py:105` builds a plain ccxt client with no
sandbox flag, so candles come from **Hyperliquid mainnet**.
`node_guard.py:405,424-425` then derives both levels from that price:

```python
current_price = float((state.get('ohlcv_data') or {}).get('current_price') or 0)
sl_price = round(current_price * (1 - sl_pct / 100.0), 4)
tp_price = round(current_price * (1 + tp_pct / 100.0), 4)
```

Solving the stored levels back for the reference price gives one exact answer:

```
sl=62654.1  tp=64236.305
cp=63287.0  ->  sl_pct=1.0000  tp_pct=1.5000   (best fit, err 5e-5)
```

Independently confirmed by the sizing metadata: `0.0032 BTC × 63287 × 1.0 %` =
**2.02 USD** ≈ the recorded `effective_risk_usd: 2.0`.

So **the model asked for stop 1.0 % / target 1.5 % — R:R 1.5** — off a reference
price of 63 287, which is exactly the real market at 18:40. The order then filled
at 63 918.4 on testnet, 1.0 % above that reference:

```
cp=63287.0 (AI reference)  -> sl 1.0000%  tp 1.5000%  R:R 1.50
cp=63786.0 (testnet mark)  -> sl 1.7745%  tp 0.7060%  R:R 0.40
cp=63918.4 (actual fill)   -> sl 1.9780%  tp 0.4974%  R:R 0.25   <-- what the UI shows
```

### 2. "R:R 0.25"

The UI is right. `riskReward.ts:196-199` divides reward % by risk %, both measured
from the stored entry — 317.9 up vs 1264.3 down. The intended 1.5 was destroyed by
the 1 % gap between the price the levels were computed from and the price the trade
filled at. The stop ended up nearly twice as far as planned and the target a
third of the planned distance.

Note `order-listener/app/webhook_handler.py:55-109` (`compute_guaranteed_sl`) does
re-check the stop against the live mark — it keeps the strategy stop
(`sl_source: strategy`) because it is tighter than the liquidation-safe one — but
**nothing re-prices the target** against the venue the order will actually hit.

---

## Root cause 3 — the trend lines are a 10-day-old geometry read

```
$ ... ai_signal_log WHERE geometry_data IS NOT NULL GROUP BY strategy_id
 ai-btc-6f8c        | 617 rows | newest 2026-08-02 10:00:30+00
 eth-ai-34d2        | 608 rows | newest 2026-08-12 17:00:25+00
 hype-breakout-da2e |  41 rows | newest 2026-07-07 09:27:54+00

$ ... ai_strategy_config WHERE strategy_id='ai-btc-6f8c'
 use_geometry | f

$ ... count rows since then for ai-btc-6f8c: 200
```

`use_geometry` is **off**, so no new read will ever be written — but
`chartData.ts:245-259` (`latestGeometry`) has no age limit and happily returns the
row from **2 August**. 200 cycles have run since.

What that does to the drawing (`geometryLines.ts:52` pins the boundary values to
`seriesEnd` — "the last bar on screen" — then projects backwards with the fitted
slope):

```
series            2026-08-09 14:15 -> 2026-08-12 17:15
geometry computed 2026-08-02 10:00
anchor_ts         2026-07-28 10:00     first_swing_ts 2026-07-31 14:00
upper line        61994.2 (left) -> 63642.9 (right)
lower line        62823.0 (left) -> 62474.2 (right)
candles range     low 63211.5  high 65454.6
swing points inside the chart window: 0 of 25
shape no_pattern   fit weak
```

Three separate problems visible in those numbers:

- Every one of the 25 swing points is filtered out (`geometryLines.ts:86-95`) —
  they are all 26 July–2 Aug. So the lines have **no dots to attach to**: exactly
  "not linked with anything".
- A 7-day-old fit projected back 75 hours at 21.98/bar drifts 1 648 points. The
  lower line (62 474–62 823) ends up **entirely below every candle** (min low
  63 211.5), and the "upper" line **starts below the lower one** (61 994 vs
  62 823) — an inverted range.
- The read itself is `shape: no_pattern`, `fit_quality: weak`. The guard at
  `geometryLines.ts:39-40` only skips a no-pattern read when both boundaries are
  0; here they are 62 474 / 63 643, so it draws anyway.

---

## Contrast: the other open position looks fine

`social-btc-astro` on **blofin demo** filled at 63 989.3 with
`mark_price_at_decision = 63989.0`, and Blofin live 1m at 13:19 was
63 995.2 / 63 995.9 / 63 974.6 / 63 979.1. Demo Blofin tracks live Blofin
(mark 63 418.8 vs live 63 447 right now), so its chart lines land on its candles.
**The price mismatch is specific to the Hyperliquid testnet account.**

---

## Summary of what is actually broken

| # | Symptom | Verdict |
|---|---------|---------|
| 1 | Entry line off the candles | Real bug — chart mixes Blofin-live candles with Hyperliquid-testnet prices |
| 2 | R:R 0.25 | UI arithmetic correct; the *trade* is broken — AI priced TP/SL off mainnet (63 287), filled on testnet (63 918.4) |
| 3 | Candles above TP, no close | Not a bug — testnet peaked at 63 955 vs TP 64 236; TP order is live on the exchange |
| 4 | Trend lines attached to nothing | Real bug — 10-day-old `no_pattern`/`weak` geometry, no age limit in `latestGeometry`, boundary re-anchored to the newest bar |

Also noted, not part of the complaint: `docker compose logs` for
`ai-signal-generator` and `order-listener` stop at 2026-08-11 09:25 / 09:30 —
32 hours with no output at all, so this investigation had no service logs to work
from and had to reconstruct the decision from stored numbers.
