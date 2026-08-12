# Charts now use the market the trade actually lives in

Follow-up to `btc-ai-position-chart-investigation.md`, which found that a position
on a **Hyperliquid testnet (demo)** account was being charted against **Blofin live**
candles — two different markets, ~1% apart on 2026-08-11.

Rule implemented: **a chart of a trade always sources its candles from that
account's own exchange AND mode, first, every time.** market-ingestion's Redis
stream is demoted to a fallback for when that venue cannot be reached, and when it
is used the chart says so in red.

---

## What changed

| File | Change |
|---|---|
| `order-executor/app/adapters/base.py` | New `get_candles(symbol, timeframe, limit, end_ms)` on the adapter contract + shared `CANDLE_BAR_SECONDS`. Never raises; `[]` means unavailable. |
| `order-executor/app/adapters/hyperliquid.py` | Implemented via `candleSnapshot` (bar count → time window). Uses `self.base_url`, so a demo account reads **testnet** bars. |
| `order-executor/app/adapters/blofin.py` | Implemented via `/api/v1/market/candles`; base-asset volume column, `after` for windowing, 500/call ceiling. |
| `order-executor/app/adapters/binance.py` | Implemented via `/fapi/v1/klines`; `endTime` for windowing, 1500/call ceiling. |
| `order-executor/app/main.py` | New `GET /accounts/{id}/candles/{symbol}?tf=&limit=&end_ms=`, echoing `mode`. |
| `dashboard-api/src/chartData.ts` | `assemble()` tries the account's own venue first via the executor; stream only as fallback. New payload fields `mode` and `candle_source`. All three builders (position / order / AI-signal) now pass the account id. |
| `dashboard-ui/src/components/ExpandableChart.tsx` | Always-visible "Candles from" label: `hyperliquid · demo`, or red `blofin (not this account)` on the fallback. |
| `docker-compose.yml` | Corrected the market-ingestion comment — those streams are the chart's fallback now, not its source. |

Exchange isolation is preserved: dashboard-api never talks to an exchange, only to
order-executor, and stays venue-agnostic.

---

## Verification (live containers)

### The executor serves each venue's own market

```
$ docker compose exec -T nginx wget -qO- "http://order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/candles/BTC-USDT?tf=15m&limit=5"
{"symbol":"BTC-USDT","timeframe":"15m","mode":"demo","candles":[
 {"time":1786554900000,"open":63348.0,"high":63351.0,"low":63232.0,"close":63267.0,"volume":0.03437},
 ...
 {"time":1786558500000,"open":63128.0,"high":63168.0,"low":63037.0,"close":63084.0,"volume":0.07626}]}

$ ... /accounts/blofin-blofin-demo-v5vr/candles/BTC-USDT?tf=15m&limit=3
{"symbol":"BTC-USDT","timeframe":"15m","mode":"demo","candles":[
 ...
 {"time":1786558500000,"open":63445.4,"high":63445.4,"low":63366.1,"close":63408.8,"volume":44.0224}]}
```

Same symbol, same minute, **63 084 vs 63 408** — proof the two markets are not
interchangeable.

### The BTC AI position chart before vs after

Before (Blofin live bars under Hyperliquid-testnet lines):

```
overlay: entry 63918.4  tp 64236.305  sl 62654.1  current 63425.6
bars after fill 91
max high after fill 64441.3   <-- ABOVE the 64236 target
bars breaching TP: 3
--- around the fill (08-11 18:30) ---
08-11 18:30 63463.1 63487.8 63226.0 63283.8    <-- entry 63918.4 is 640 ABOVE this bar
```

After:

```
$ docker compose exec -T nginx wget -qO- "http://dashboard-api:8003/positions/9c43a165-.../candles"
{'symbol': 'BTC-USDT', 'exchange': 'hyperliquid', 'mode': 'demo',
 'candle_source': 'exchange', 'timeframe': '15m', 'bar_seconds': 900,
 'available_timeframes': ['1m','5m','15m','30m','1h','4h','1d'],
 'note': "Candles from this account's own hyperliquid demo market. A demo market
          has its own prices, so they will not match public charts of BTC-USDT."}
overlay entry 63918.4 tp 64236.305 sl 62654.1 current 63185
candles 300 08-09 15:45 -> 08-12 18:30
bars after fill 96
max high after fill 63955 vs TP 64236.305
bars breaching TP: 0
--- around the fill ---
08-11 17:45 63729 63907 63729 63815
08-11 18:00 63814 63814 63726 63781
08-11 18:15 63765 63804 63748 63804
08-11 18:30 63803 64004 63500 63771   <-- entry 63918.4 sits INSIDE this bar
08-11 18:45 63771 63901 63709 63760
```

Three of the four reported symptoms are gone in one change:

- the entry line now crosses the candle it was filled on (63 500–64 004);
- no bar breaches the target any more, matching the exchange never triggering it;
- `current_price` is 63 185 (the venue's own price) instead of Blofin's 63 425, so
  the P&L on the chart matches the exchange.

### Every rung of the picker works on the new source

```
hyperliquid 1m: exchange 1m 300 bars 08-12 13:42 -> 08-12 18:41
hyperliquid 5m: exchange 5m 300 bars 08-11 17:45 -> 08-12 18:40
hyperliquid 30m: exchange 30m 300 bars 08-06 13:00 -> 08-12 18:30
hyperliquid 4h: exchange 4h 300 bars 06-23 20:00 -> 08-12 16:00
hyperliquid 1d: exchange 1d 300 bars 10-17 00:00 -> 08-12 00:00
```

### Order chart and AI-signal chart (windowed) both switched over

```
ORDER CHART tf=1h: exchange=hyperliquid mode=demo candle_source=exchange
  candles 300  07-31 07:00 -> 08-12 18:00

AI SIGNAL CHART (id 6109, trigger 08-11 18:40, 40-bar lookahead):
  exchange=hyperliquid mode=demo candle_source=exchange
  candles 300  08-09 01:45 -> 08-12 04:30
```

The signal chart's window still ends 40×15m after the trigger (04:40) — the
`end_ms` windowing survived the source change.

### The Blofin position keeps working, on its own demo market

```
$ ... /positions/d5626cc9-.../candles
{'exchange': 'blofin', 'mode': 'demo', 'candle_source': 'exchange',
 'timeframe': '1m', 'available_timeframes': ['1m','5m','15m','30m','1h','4h','1d']}
entry 63989.3 current 63450.2
```

### Bonus: symbols with no ingested stream now chart at all

`HYPE-USDT` is deliberately not in `INGESTION_SUBSCRIPTIONS`, so its charts used to
be empty ("No candle stream ingested for HYPE-USDT"):

```
$ ... /orders/3a8d9e5b-.../candles
{'symbol': 'HYPE-USDT', 'exchange': 'blofin', 'mode': 'demo',
 'candle_source': 'exchange', 'timeframe': '30m', ...}
candles 300
```

### The fallback still works, and admits what it is

Run with a dead `EXECUTOR_URL` in a throwaway container on the same network:

```
$ docker compose run -d --rm --no-deps --name matp-dapi-fallbacktest \
    -e EXECUTOR_URL=http://127.0.0.1:9 dashboard-api
$ docker compose exec -T nginx wget -qO- "http://matp-dapi-fallbacktest:8003/positions/9c43a165-.../candles"
{'exchange': 'blofin', 'mode': None, 'candle_source': 'stream', 'timeframe': '15m',
 'note': 'Could not reach hyperliquid for candles — showing ingested blofin bars
          instead. This is a different market, so the entry, stop and target lines
          may not line up with them.'}
candles 301 08-09 15:30 -> 08-12 18:30
```

The UI renders that state as a red `blofin (not this account)` label beside the
timeframe tabs.

### Failure shapes are safe

```
$ ... /candles/NOTACOIN-USDT?tf=15m   -> {"...","mode":"demo","candles":[]}
$ ... /candles/BTC-USDT?tf=7m         -> {"...","mode":"demo","candles":[]}
```

Empty, never an exception — `[]` is treated as "unavailable", never as "no market".

### Tests and stack

```
$ docker compose exec -T order-executor python -m pytest tests -q
86 passed, 1 warning in 81.07s

$ npx vitest run          (dashboard-ui)
Test Files  2 passed (2)     Tests  42 passed (42)

$ npx tsc --noEmit        (dashboard-api, dashboard-ui)   clean

$ docker compose ps
matp-order-executor-1   Up 14 minutes (healthy)
matp-dashboard-api-1    Up 10 minutes (healthy)
matp-dashboard-ui-1     Up 3 minutes
(all 15 services up)

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-DAp23xzm.js
$ docker compose exec -T dashboard-ui grep -rl 'Candles from' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-DAp23xzm.js
```

---

## Still open (not fixed here)

Two findings from the investigation are untouched, deliberately:

1. **The AI prices TP/SL off the wrong market.** `ai-signal-generator` fetches
   candles through ccxt with no sandbox flag
   (`app/data/ohlcv.py:105-109`), so a demo account's model reasons about
   **mainnet** prices and `node_guard` derives both levels from them, while the
   order fills at testnet prices. On 2026-08-11 that turned a requested 1.0% stop /
   1.5% target (R:R 1.5) into R:R 0.25. A plan is proposed separately.
2. **Stale AI geometry drawn as trend lines.** `latestGeometry()`
   (`dashboard-api/src/chartData.ts`) has no age limit and returns a
   `no_pattern`/`weak` read from 2026-08-02 for a strategy whose `use_geometry` is
   `false`; `geometryLines.ts:52` then re-anchors it to the newest bar on screen.
