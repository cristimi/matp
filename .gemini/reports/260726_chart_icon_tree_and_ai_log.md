# Chart follow-up 2: round icon toggle in the Tree, and charts on AI log entries

**Date:** 2026-07-26
**Branch:** none — landed straight on `main` per CLAUDE.md.
**Follows:** `.gemini/reports/260726_chart_sol_ingestion_and_tree.md`

Three requests:

1. Chart on the Tree's **pending orders**, not just positions.
2. Chart on **AI signal log** entries.
3. In the Tree, the toggle should be a **round icon before the Close position button**,
   and the same on pending orders — not the full-width bar.

---

## 1. New endpoint: `GET /ai/signals/:id/candles`

An AI log entry is not an order or a position, and reusing the existing endpoints would have
shown the wrong thing. Two deliberate differences:

- **The geometry is that row's own snapshot**, not the strategy's newest. The point of the
  chart on a log entry is to show the range *the model was looking at when it decided* —
  showing today's range next to a three-day-old decision would be misleading.
- **The candle window ends near the trigger**, not at "now": `triggered_at + 40 bars`. You see
  the market as it stood at decision time, plus what happened next.
- The overlay comes from the order the signal produced. A `hold`, a gate rejection or an
  `amend_order` with no linked order has none, so the chart is candles + range only.

Windowing filters on the candles' own `t` field rather than on the Redis entry ID, because
the entry ID is the time the bar was *written* — roughly one full bar later than its open.
`readCandles` over-fetches (6×, capped at the 2000-bar stream) and then slices. If the window
is empty because the moment predates what the stream retains, it falls back to the recent
bars rather than returning nothing.

Route is declared before `/signals`, per the Express ordering rule.

### Verification

A `geometric_range` cycle carrying the Phase 0 slope fields:

```
$ …/ai/signals/4729/candles?limit=120
symbol     : ETH-USDT | tf: 1h | candles: 120
window     : 1784638800000 -> 1785067200000
geometry_at: 1785070831316 (this row, not the newest)
shape      : no_pattern | slopes: 0.95339202 0.72031746
swings     : 9 highs, 14 lows
overlay    : {"side": null, "status": null, "placed_at": null, "filled_at": null,
              "entry_price": null, "stop_price": null, "target_price": null,
              "closed_at": null, "close_price": null, "current_price": 1885.49}
note       : Candles from blofin — hyperliquid is not ingested.
```

Null overlay is correct — that cycle proposed `amend_order` and has no `order_id`.

A cycle that *did* produce an order (an older row, so no slope keys — the optional-field
handling from the first build):

```
$ …/ai/signals/4699/candles?limit=80
symbol : ETH-USDT | candles: 80 | window: 1784782800000 -> 1785067200000
geom   : no_pattern | has slope keys: False
overlay: {"side": "buy", "status": "pending", "placed_at": 1785056481618,
          "filled_at": null, "entry_price": 1878.596984, "stop_price": 1873.13,
          "target_price": 1894.716365, "closed_at": null, "close_price": null,
          "current_price": 1885.49}
```

Missing row, and the existing list route still resolving (route-ordering check):

```
$ …/ai/signals/99999999/candles
HTTP/1.1 404 Not Found

$ …/ai/signals?limit=1
{"signals":[{"id":"4732","strategy_id":"sol-ai-6486","triggered_at":"2026-07-26T13:31:07.992Z",…
```

---

## 2. The round icon toggle

`ExpandableChart` gained a third `variant`:

| variant | used by | look |
|---|---|---|
| `footer` (default) | Positions, Orders | full-bleed card footer, matching `ActionBand` |
| `inline` | AI signal log | full-width, bordered, inside the expanded card |
| `icon` | Tree positions, Tree pending orders | 38 px round button |

The icon is an inline two-candlestick SVG drawn in `currentColor`, so the button owns the
colour: outlined blue when closed, filled blue when open, so it reads as a toggle rather than
a link.

**Layout problem and how it is solved.** The icon has to sit *in a row* with the Close
position button, but the chart panel must still open full-width underneath — if the component
were dropped into a flex row as-is, the panel would become a flex item beside the buttons.
So the icon variant takes an `actions` prop and owns the row itself:

```tsx
<ExpandableChart
  path={`/positions/${p.id}/candles`}
  variant="icon"
  actions={isOpen ? <button …>Close position</button> : undefined}
/>
```

It renders `[icon] [actions]` in one flex row, then the panel below it. The close button is
handed `flex: 1, width: 'auto'` to override its full-width block styling. A closed position
passes no `actions`, so the icon simply sits alone.

Pending-order cards use the same variant with no `actions`.

### The `symbol` prop is gone

`ExpandableChart` no longer takes `symbol`. Price decimals now come from `payload.symbol`,
which is authoritative and always present in the response. This was necessary rather than
cosmetic: an AI log row carries only a `strategy_id`, so the call site has no symbol to pass.
All four call sites were updated.

---

## 3. Verification

```
$ cd dashboard-api && npx tsc --noEmit
api tsc exit: 0

$ cd dashboard-ui && npx tsc --noEmit
ui tsc exit: 0

$ npx vitest run
 Test Files  1 passed (1)
      Tests  28 passed (28)
```

```
$ ./scripts/redeploy.sh dashboard-api
matp-dashboard-api-1   dashboard-api   Up 3 seconds (health: starting)
✓ dashboard-api redeployed.

$ ./scripts/redeploy.sh dashboard-ui
matp-dashboard-ui-1   dashboard-ui   Up 3 seconds
   live dashboard-ui asset: index-CFtsCQ-9.js
✓ dashboard-ui redeployed.
```

Served bundle:

```
$ docker compose exec -T dashboard-ui grep -rl 'Show chart' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-CFtsCQ-9.js        # the icon toggle's aria-label

$ docker compose exec -T dashboard-ui grep -rl '/ai/signals/' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-CFtsCQ-9.js        # the AI-log chart path

$ docker compose exec -T dashboard-ui grep -rl 'lightweight-charts' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-CFtsCQ-9.js

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-CFtsCQ-9.js
```

The layer boundary still holds — only the adapter imports the engine:

```
$ grep -rl "from 'lightweight-charts'" dashboard-ui/src
dashboard-ui/src/charts/adapters/lightweightCharts/riskRewardPrimitive.ts
dashboard-ui/src/charts/adapters/lightweightCharts/index.ts
```

All four call sites:

```
$ grep -rn "ExpandableChart path\|<ExpandableChart" dashboard-ui/src/pages/
Orders.tsx:239        <ExpandableChart path={`/orders/${order.id}/candles`} />
Positions.tsx:351     <ExpandableChart path={`/positions/${position.id}/candles`} />
StrategyTree.tsx:1030 <ExpandableChart … variant="icon" actions={…Close position…} />
StrategyTree.tsx:1105 <ExpandableChart path={`/orders/${o.id}/candles`} variant="icon" />
AiSignalLog.tsx:361   <ExpandableChart path={`/ai/signals/${row.id}/candles`} variant="inline" />
```

---

## Notes

- The AI log card uses `inline`, not `icon` — the expanded card is a full-width detail panel
  with no action buttons to sit beside, so a full-width toggle is the right shape there. Say
  the word if the icon is wanted there too.
- The icon is 38 px. That is slightly under the 44 px touch guideline, chosen to match the
  height of the Close position button beside it; the surrounding row padding brings the
  effective target close to 44 px.
