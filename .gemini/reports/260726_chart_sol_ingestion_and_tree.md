# Chart follow-up: ingest every live symbol, and put the chart on Tree positions

**Date:** 2026-07-26
**Branch:** none — landed straight on `main` per CLAUDE.md.
**Follows:** `.gemini/reports/260726_position_risk_reward_chart.md`

Two problems reported after the overlay shipped:

1. `sol-ai-6486` had an **open** position but SOL-USDT was not being ingested, so its chart
   had nothing to draw.
2. The chart existed on the Positions and Orders pages but not on a position inside the
   Strategy Tree.

---

## 1. Ingestion — every symbol with an enabled strategy

SOL was not a one-off. Only BTC and ETH were ingested, while six strategies were enabled
across six symbols, so the same empty chart was waiting for BNB, TAO and XRP too.

```
$ docker compose exec -T postgres psql -U matp -d matp -c \
  "SELECT s.id, s.symbol, s.interval, s.enabled, s.strategy_source,
          count(sp.id) FILTER (WHERE sp.status='open') AS open_pos
     FROM strategies s LEFT JOIN strategy_positions sp ON sp.strategy_id = s.id
    GROUP BY 1,2,3,4,5 ORDER BY s.symbol;"

             id             |  symbol   | interval | enabled | strategy_source | open_pos
----------------------------+-----------+----------+---------+-----------------+----------
 bnb-ai-scalper-edbb        | BNB-USDT  | 1h       | t       | ai_engine       |        0
 ai-btc-6f8c                | BTC-USDT  | 1h       | t       | ai_engine       |        0
 matp-test-harness-fe19     | BTC-USDT  | 1h       | f       | tradingview     |        0
 tv-btc-test-hl-94e1        | BTC-USDT  | 1h       | f       | tradingview     |        0
 tv_test_harness            | BTC-USDT  | 1h       | f       | signal_engine   |        0
 social-btc-astro           | BTC-USDT  | 1m       | t       | signal_engine   |        0
 eth-ai-34d2                | ETH-USDT  | 1h       | t       | ai_engine       |        0
 hype-breakout-da2e         | HYPE-USDT | 1h       | f       | ai_engine       |        0
 hype-test-7db4             | HYPE-USDT | 4h       | f       | tradingview     |        0
 sol-ai-6486                | SOL-USDT  | 1h       | t       | ai_engine       |        1
 sui-manual-59d9            | SUI-USDT  | 1h       | f       | tradingview     |        0
 tao-ai-range-rotation-d257 | TAO-USDT  | 1h       | t       | ai_engine       |        0
 xrp-ai-3844                | XRP-USDT  | 1h       | t       | ai_engine       |        0
(13 rows)
```

```yaml
# docker-compose.yml — market-ingestion
INGESTION_SUBSCRIPTIONS: BTC-USDT:1h,BTC-USDT:4h,BTC-USDT:1m,ETH-USDT:1h,SOL-USDT:1h,BNB-USDT:1h,TAO-USDT:1h,XRP-USDT:1h
```

HYPE-USDT and SUI-USDT are deliberately excluded — both strategies are `enabled = false`, so
no new position can open there. Their historical closed positions will show the endpoint's
"no candle stream" note until someone re-enables them and adds the subscription.

### Verification

```
$ ./scripts/redeploy.sh market-ingestion
matp-market-ingestion-1   matp-market-ingestion   "python -m app.main"   market-ingestion   Up 15 seconds
✓ market-ingestion redeployed.

$ docker compose logs market-ingestion --tail 25
… Symbol resolved: SOL-USDT -> SOL/USDT:USDT (tickSize=0.01)
… Symbol resolved: BNB-USDT -> BNB/USDT:USDT (tickSize=0.01)
… Symbol resolved: TAO-USDT -> TAO/USDT:USDT (tickSize=0.01)
… Symbol resolved: XRP-USDT -> XRP/USDT:USDT (tickSize=0.0001)
… Warmup done: TAO-USDT 1h — 500 new bars written, 0 already present
… Warmup done: BNB-USDT 1h — 500 new bars written, 0 already present
… Warmup done: SOL-USDT 1h — 500 new bars written, 0 already present
… Warmup done: XRP-USDT 1h — 500 new bars written, 0 already present
… Starting 8 watch loop(s) for exchange=blofin
```

```
$ for s in BTC ETH SOL BNB TAO XRP; do docker compose exec -T redis redis-cli XLEN stream:candles:blofin:$s-USDT:1h; done
BTC-USDT:1h  1122
ETH-USDT:1h   501
SOL-USDT:1h   500
BNB-USDT:1h   500
TAO-USDT:1h   500
XRP-USDT:1h   500
```

(BTC-USDT:1h exceeds 500 because `XADD … MAXLEN ~` trims approximately and this stream has
been running for days; `STREAM_MAXLEN = 2000` is the real bound.)

A first `XLEN` read taken during the redeploy showed `SOL-USDT:1h 0`. That was the read
landing mid-warmup, not a failure — the log line above shows all 500 bars written 33 seconds
after the warmup started.

### The reported SOL position, end to end

```
$ docker compose exec -T postgres psql -U matp -d matp -c \
  "SELECT id, symbol, side, status, entry_price FROM strategy_positions
    WHERE symbol='SOL-USDT' AND status='open';"
a186f8bb-801c-4d1e-9b5e-af531dc8ff6e|SOL-USDT|short|open|74.84

$ …/positions/a186f8bb-801c-4d1e-9b5e-af531dc8ff6e/candles?limit=3
symbol   : SOL-USDT | exchange: blofin | timeframe: 1h | candles: 4
note     : None
last bar : {'time': 1785070800000, 'open': 74.84, 'high': 74.85, 'low': 74.73,
            'close': 74.82, 'volume': 2459.47}
overlay  : {"side": "short", "status": "open", "placed_at": 1785070879907,
            "filled_at": 1785070883416, "entry_price": 74.84,
            "stop_price": 75.2516, "target_price": 74.0168,
            "closed_at": null, "close_price": null, "current_price": 74.82}
geometry : none
```

Real candles, no fallback note (this account is on blofin), and a complete short-side
overlay: stop above entry, target below, currently a little in profit.

**`geometry: none` is correct, not a gap.** `sol-ai-6486` runs the `trend_following` template
with `use_geometry` off, so it has never written a `geometry_data` row. The chart shows
candles and the risk-reward box, with no range boundaries — `computeGeometryModel()` returns
null and the adapter simply adds no boundary series.

---

## 2. The chart on Tree positions

`StrategyTree.tsx`'s `PositionCard` cycles through three states on tap:
`header → details → orders`. The chart went into the **orders** track, the deepest level, so
expanding a strategy or a position still costs no candle fetch — the chart is one more
deliberate tap, and is itself collapsed until pressed.

```tsx
// StrategyTree.tsx — inside the orders track
<ExpandableChart path={`/positions/${p.id}/candles`} symbol={symbol} variant="inline" />
```

`ExpandableChart` gained a `variant` prop that changes **only** the toggle's chrome:

| variant | used by | look |
|---|---|---|
| `footer` (default) | Positions, Orders | full-bleed card footer, matching `ActionBand` |
| `inline` | Tree orders track | bordered, rounded, transparent — sits inside the indented box |

No logic differs between the two, and the panel itself is unchanged.

### Verification

```
$ npx tsc --noEmit
tsc exit: 0

$ npx vitest run
 ✓ src/charts/core/__tests__/riskReward.test.ts (28 tests) 637ms
 Test Files  1 passed (1)
      Tests  28 passed (28)

$ ./scripts/redeploy.sh dashboard-ui
matp-dashboard-ui-1   matp-dashboard-ui   dashboard-ui   Up 3 seconds
   live dashboard-ui asset: index-DUJ2OhGK.js
✓ dashboard-ui redeployed.

$ docker compose exec -T dashboard-ui grep -rl 'Hide chart' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-DUJ2OhGK.js

$ docker compose exec -T dashboard-ui grep -rl 'lightweight-charts' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-DUJ2OhGK.js

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-DUJ2OhGK.js

$ grep -n "ExpandableChart" dashboard-ui/src/pages/StrategyTree.tsx
12:import { ExpandableChart } from '../components/ExpandableChart';
1026:          <ExpandableChart path={`/positions/${p.id}/candles`} symbol={symbol} variant="inline" />
```

The layer boundary from the original build still holds — only the adapter imports the engine:

```
$ grep -rl "from 'lightweight-charts'" dashboard-ui/src
dashboard-ui/src/charts/adapters/lightweightCharts/index.ts
dashboard-ui/src/charts/adapters/lightweightCharts/riskRewardPrimitive.ts
```

---

## Not done

- **Pending-order cards in the Tree** have no chart. The request was for positions; the same
  one-liner with `path={`/orders/${o.id}/candles`}` would add it.
- **HYPE-USDT / SUI-USDT** are not ingested (disabled strategies, see above).
