# Chart timeframe picker, detail strip move, vertical zoom

Date: 2026-07-26

## What was asked

1. Let the user choose the timeframe on the expandable candle chart.
2. Move Risk / Reward / R:R into the position details *under* the chart.
3. Remove "To target" and "To stop".
4. Remove the "Timeframe" label (the picker speaks for itself).
5. Default the chart to two rungs below the strategy's own interval
   (ladder `1m 5m 15m 30m 1h 4h 1d` — a 1h strategy opens on 15m).
6. Add vertical zoom from the side of the chart.

## What changed

| File | Change |
|---|---|
| `docker-compose.yml` | `INGESTION_SUBSCRIPTIONS` grown from 8 to 42 streams — the full 7-rung ladder for each of the 6 symbols with an enabled strategy. Without this the picker would have had one live button. |
| `dashboard-api/src/chartData.ts` | `CHART_TIMEFRAMES` ladder, `normalizeTimeframe()` (`?tf=` guard), `defaultTimeframe()` (two rungs down), `availableTimeframes()`; payload gains `available_timeframes`; a note is added when a requested rung is not ingested; the AI-log lookahead window is now counted in bars of the timeframe on screen. |
| `dashboard-api/src/routes/{positions,orders,ai}.ts` | All three `/candles` endpoints accept `?tf=`. |
| `dashboard-ui/src/charts/core/types.ts` | `ChartPayload.available_timeframes`; `ChartHandle.zoomPrice()` / `.resetPriceZoom()` added to the engine-agnostic contract. |
| `dashboard-ui/src/charts/adapters/lightweightCharts/index.ts` | Implements the two new methods via `IPriceScaleApi.setVisibleRange`; price-axis drag and double-click-reset enabled explicitly; auto-scale restored whenever new candles arrive. |
| `dashboard-ui/src/components/ExpandableChart.tsx` | `TimeframeTabs` (unlabelled) + `ZoomButton` rail; Risk/Reward/R:R moved to a details strip below the chart; "To target" / "To stop" removed; refetch on switch keeps the old chart on screen instead of remounting it. |

`vertTouchDrag` stays `false` on purpose: turning it on would make the price axis
finger-draggable but would also stop a finger dragged down the candles from
scrolling the page. Hence the explicit `+ / − / ⤢` rail on the right-hand side.

## Proof

### Ingestion — 42 streams, all warmed, no errors

```
$ docker compose logs market-ingestion --since 5m | grep "Starting .* watch loop"
market-ingestion-1  | 2026-07-26 15:20:38,260 INFO __main__: Starting 42 watch loop(s) for exchange=blofin

$ docker compose exec -T redis redis-cli --scan --pattern 'stream:candles:*' | wc -l
42

$ docker compose logs market-ingestion --since 4m | grep -c "Closed bar"
30
$ docker compose logs market-ingestion --since 4m | grep -ci "error"
0
```

### Default timeframe — 1h strategy opens on 15m

```
$ docker compose exec -T nginx wget -qO- \
    "http://dashboard-api:8003/positions/1851311f-c48e-486b-a8ba-85ffce129add/candles"
{"symbol":"TAO-USDT","exchange":"blofin","timeframe":"15m","timeframe_requested":"15m",
 "available_timeframes":["1m","5m","15m","30m","1h","4h","1d"],"bar_seconds":900,
 "candles":[{"time":1784808900000,"open":196.21,"high":196.66,"low":195.81,"close":196.27,...
```

### Switching, and the `?tf=` guard

```
tf=1m    -> 1m  60     301 bars  note=None
tf=4h    -> 4h  14400  301 bars  note=None
tf=1d    -> 1d  86400  301 bars  note=None
tf=bogus -> 15m 900    301 bars  note=None      # off-ladder value ignored, default used
```

### All three chart endpoints

```
orders/7348375c-.../candles          -> tf=15m avail=1m,5m,15m,30m,1h,4h,1d bars=301
orders/7348375c-.../candles?tf=5m    -> tf=5m  avail=1m,5m,15m,30m,1h,4h,1d bars=301
ai/signals/4748/candles              -> tf=15m avail=1m,5m,15m,30m,1h,4h,1d bars=300
ai/signals/4748/candles?tf=30m       -> tf=30m avail=1m,5m,15m,30m,1h,4h,1d bars=300
```

### Type-check and unit tests

```
$ npx tsc --noEmit        # dashboard-api  -> clean
$ npx tsc --noEmit        # dashboard-ui   -> clean (exit 0)

$ npx vitest run src/charts
 ✓ src/charts/core/__tests__/riskReward.test.ts (28 tests) 514ms
 Test Files  1 passed (1)
      Tests  28 passed (28)
```

### Served bundle is the new one

```
$ docker compose exec -T dashboard-ui grep -rl 'Stretch the price axis' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-n4JYkDq6.js
$ docker compose exec -T dashboard-ui grep -rl 'Fit the price axis to the candles' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-n4JYkDq6.js
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-n4JYkDq6.js
$ docker compose exec -T dashboard-ui grep -rl 'To target' /usr/share/nginx/html
OK: 'To target' no longer in bundle
```

### Containers

```
$ docker compose ps market-ingestion dashboard-api dashboard-ui
matp-market-ingestion-1   Up   (running)
matp-dashboard-api-1      Up   (healthy)
matp-dashboard-ui-1       Up   (running)
```

## Note for later

Only the 1h rung had real history before today; the finer rungs started from a
500-bar REST warmup at 15:20 on 2026-07-26. Charts older than that on 1m/5m
will show the window the stream retains, not the market at the time of the
trade. This fills itself in as the streams run.
