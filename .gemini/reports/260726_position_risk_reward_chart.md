# Position/order risk-reward chart overlay

**Date:** 2026-07-26
**Branch:** `feat/position-chart-overlay` — fast-forward merged into `main`, deleted locally
and on `origin`.
**Implements:** `.gemini/reports/2026-07-26_range_chart_visualization_options.md`

### Landing

The merge was a true fast-forward (`git merge --ff-only`, `Updating 2f32f4b..71f5d00`), so
there is no merge commit; `main` now points at the report commit. The five commits, in order:

| Commit | Phase |
|---|---|
| `49badc1` | 0 — geometry.py slope + time anchors |
| `14f498a` | 1 — ETH-USDT:1h ingestion |
| `a50755b` | 2 — dashboard-api chart endpoints |
| `b53778a` | 3 — dashboard-ui core + adapter + embed |
| `71f5d00` | this report; **`main` HEAD after the merge** |

A per-row expandable candle chart on the Positions and Orders pages, showing the AI-detected
range boundaries plus a static TradingView-style risk/reward overlay. Built as two layers
with a hard boundary: an engine-agnostic core and a `lightweight-charts` adapter that is the
only code in the app allowed to import a charting library.

---

## Pre-flight

The investigation report assumed no DB migration was needed. Re-checked against the current
tree — still true. `geometry_data` is a `jsonb` column, so the new keys need no schema change.

```
$ ls db/migrations | tail -5
060_ai_close_gate.sql
061_ai_sizing_retune.sql
062_social_signal_log_image.sql
063_social_extraction_cache.sql
_archive
README.md
```

No migration was added in any phase of this work.

---

## Phase 0 — geometry.py: slope + time anchors

`detect_geometry()` fitted both trendlines, evaluated them **only at the final bar**, and
discarded the slopes, intercepts and swing indices. A consumer could therefore draw two flat
levels at "now" but not the sloped channel/triangle/wedge the LLM actually reasoned about.

Added to the returned dict — on both the success path and the insufficient-swings fallback.
Additive only: no threshold, no `MAX_SWINGS`, no classification logic was touched.

| Field | Meaning |
|---|---|
| `upper_slope`, `lower_slope` | price change per bar of each fitted line |
| `anchor_ts` | open-time (ms) of the bar used as `x = 0` in the fit |
| `bar_seconds` | bar duration (median gap between opens) |
| `first_swing_ts` | oldest swing in the fit — where a drawn line should start |
| `swing_highs`, `swing_lows` | every detected swing as `[open_time_ms, price]` |

All four timestamp/duration fields come back `None` (and the swing lists empty) when the
caller passes candles with no `timestamp` key, so callers that don't supply times are
unaffected.

### Tests

`ai-signal-generator` has no pytest in its image (the Dockerfile copies `app/` only, not
`tests/`), so the suite runs in a throwaway container off the same image with the repo
mounted:

```
$ docker compose run --rm --no-deps --entrypoint sh \
    -v /home/cristi/matp/ai-signal-generator:/src -w /src ai-signal-generator \
    -c "pip install --quiet --no-cache-dir pytest; python -m pytest tests/test_geometry.py tests/test_builder_geometry.py -v"

============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: /src
collected 28 items

tests/test_geometry.py::test_horizontal_channel PASSED                   [  3%]
tests/test_geometry.py::test_ascending_channel PASSED                    [  7%]
tests/test_geometry.py::test_descending_channel PASSED                   [ 10%]
tests/test_geometry.py::test_ascending_triangle PASSED                   [ 14%]
tests/test_geometry.py::test_descending_triangle PASSED                  [ 17%]
tests/test_geometry.py::test_rising_wedge PASSED                         [ 21%]
tests/test_geometry.py::test_falling_wedge PASSED                        [ 25%]
tests/test_geometry.py::test_no_pattern_diverging PASSED                 [ 28%]
tests/test_geometry.py::test_broadening PASSED                           [ 32%]
tests/test_geometry.py::test_position_in_range PASSED                    [ 35%]
tests/test_geometry.py::test_too_few_candles PASSED                      [ 39%]
tests/test_geometry.py::test_insufficient_swings PASSED                  [ 42%]
tests/test_geometry.py::test_empty_candles PASSED                        [ 46%]
tests/test_geometry.py::test_chart_replay_fields_present_and_sane PASSED [ 50%]
tests/test_geometry.py::test_slopes_match_the_synthetic_trendlines PASSED [ 53%]
tests/test_geometry.py::test_slope_projects_boundary_back_to_the_first_swing PASSED [ 57%]
tests/test_geometry.py::test_anchor_ts_follows_the_lookback_slice PASSED [ 60%]
tests/test_geometry.py::test_insufficient_swings_still_carries_window_fields PASSED [ 64%]
tests/test_geometry.py::test_untimed_candles_leave_chart_fields_empty PASSED [ 67%]
tests/test_geometry.py::test_output_keys_present PASSED                  [ 71%]
tests/test_geometry.py::test_fit_quality_values PASSED                   [ 75%]
tests/test_builder_geometry.py::test_no_pattern_weak_renders_honest_no_reliable_pattern_block PASSED [ 78%]
tests/test_builder_geometry.py::test_no_pattern_strong_is_surfaced_as_unclassified PASSED [ 82%]
tests/test_builder_geometry.py::test_moderate_fit_is_tradeable_but_flagged PASSED [ 85%]
tests/test_builder_geometry.py::test_named_shape_renders_title_not_unclassified PASSED [ 89%]
tests/test_builder_geometry.py::test_chart_replay_fields_do_not_change_the_rendered_section PASSED [ 92%]
tests/test_builder_geometry.py::test_use_geometry_off_is_omitted PASSED  [ 96%]
tests/test_builder_geometry.py::test_empty_geometry_data_is_omitted PASSED [100%]

============================== 28 passed in 2.67s ==============================
```

7 of those are new, plus one in the builder suite asserting the new keys **do not** change
the rendered GEOMETRIC PATTERN prompt section — the fields are for the UI, and must not leak
into what the LLM reads or shift any existing line.

### Live in the running container

After `./scripts/redeploy.sh ai-signal-generator`, running the deployed code against a
synthetic ascending channel (0.15/bar on both boundaries, hourly bars):

```
$ docker compose exec -T ai-signal-generator python -c "…detect_geometry(synthetic)…"
{
  "shape": "ascending_channel",
  "upper_boundary": 121.85,
  "lower_boundary": 101.85,
  "upper_touches": 5,
  "lower_touches": 5,
  "convergence_pct_per_bar": -0.0,
  "pattern_age_bars": 58,
  "position_in_range_pct": 71.43,
  "fit_quality": "strong",
  "upper_slope": 0.15,
  "lower_slope": 0.15,
  "anchor_ts": 1753000000000,
  "bar_seconds": 3600,
  "first_swing_ts": 1753075600000,
  "swing_highs": [[1753025200000, 111.05], [1753075600000, 113.15], [1753126000000, 115.25]],
  "swing_lows":  [[1753050400000, 92.1],   [1753100800000, 94.2],   [1753151200000, 96.3]]
}
```

Both recovered slopes match the synthetic 0.15/bar exactly, and `bar_seconds` matches the
one-hour fixture.

### Fresh clone of the pushed branch

```
$ git clone --depth 1 --branch feat/position-chart-overlay https://github.com/cristimi/matp.git verify-clone
$ grep -n "upper_slope\|anchor_ts\|bar_seconds\|first_swing_ts\|swing_highs" \
      verify-clone/ai-signal-generator/app/data/geometry.py | tail -8
204:                'upper_slope':             0.0,
206:                'anchor_ts':               _candle_ts(candles, 0),
207:                'bar_seconds':             _bar_seconds(candles),
208:                'first_swing_ts':          None,
209:                'swing_highs':             _swing_points(swing_highs, candles),
307:            'upper_slope':             round(upper_slope, 8),
309:            'anchor_ts':               _candle_ts(candles, 0),
310:            'bar_seconds':             _bar_seconds(candles),
311:            'first_swing_ts':          _candle_ts(candles, oldest_idx),
312:            'swing_highs':             _swing_points(swing_highs, candles),
```

### First live cycle writing the new fields

The 13:00 UTC hourly cycle is the first to run on the deployed code. Both geometry-enabled
strategies wrote the new keys:

```
$ docker compose exec -T postgres psql -U matp -d matp -c \
  "SELECT strategy_id, triggered_at, prompt_template, proposed_action
     FROM ai_signal_log WHERE geometry_data ? 'upper_slope' ORDER BY triggered_at DESC;"

 strategy_id |         triggered_at          | prompt_template | proposed_action
-------------+-------------------------------+-----------------+-----------------
 eth-ai-34d2 | 2026-07-26 13:00:31.316441+00 | geometric_range | amend_order
 ai-btc-6f8c | 2026-07-26 13:00:23.835179+00 | regime_router   |
(2 rows)
```

The newest payload (swing arrays removed for length):

```
$ … SELECT jsonb_pretty(geometry_data - 'swing_highs' - 'swing_lows') … LIMIT 1;
{
    "shape": "no_pattern",
    "anchor_ts": 1784638800000,
    "bar_seconds": 3600,
    "fit_quality": "strong",
    "lower_slope": 0.72031746,
    "upper_slope": 0.95339202,
    "lower_touches": 5,
    "upper_touches": 4,
    "first_swing_ts": 1784926800000,
    "lower_boundary": 1878.596984,
    "upper_boundary": 1894.716365,
    "pattern_age_bars": 39,
    "position_in_range_pct": 45.93,
    "convergence_pct_per_bar": -0.0124
}

$ … jsonb_array_length(swing_highs) … swing_lows …
9 highs, 14 lows

$ … SELECT geometry_data->'swing_highs' … LIMIT 1;
[[1784678400000, 1943.8], [1784736000000, 1955.9], [1784764800000, 1941.0],
 [1784797200000, 1930.7], [1784876400000, 1909.3], [1784937600000, 1863.7], …]
```

Real slopes on real ETH data (+0.95 and +0.72 per hour), `bar_seconds` matching the 1h cycle,
and swings as `[open_time_ms, price]` pairs. No migration was needed — `geometry_data` is
`jsonb` and simply gained keys.

Pushed as `49badc1`.

---

## Phase 1 — ETH-USDT candles

`eth-ai-34d2` is the only `geometric_range` strategy, but ingestion covered BTC only.
`ai-signal-generator` fetched ETH candles itself each cycle (via ccxt) and discarded them, so
the UI had nothing to plot.

```yaml
# docker-compose.yml — market-ingestion
INGESTION_SUBSCRIPTIONS: BTC-USDT:1h,BTC-USDT:4h,BTC-USDT:1m,ETH-USDT:1h
```

Only that service's env block was touched; no `market-ingestion` Python was changed.

```
$ ./scripts/redeploy.sh market-ingestion
…
$ docker compose exec -T redis redis-cli XLEN stream:candles:blofin:ETH-USDT:1h
500

$ docker compose exec -T redis redis-cli GET candle:forming:blofin:ETH-USDT:1h
{"t": 1785067200000, "o": 1885.3, "h": 1893.0, "l": 1884.71, "c": 1888.73, "v": 912.793}

$ docker compose exec -T redis redis-cli XREVRANGE stream:candles:blofin:ETH-USDT:1h + - COUNT 1
1785068168361-0
t  1785063600000
o  1885.03
h  1887.0
l  1883.76
c  1885.0
v  187.111

$ docker compose exec -T redis redis-cli --scan --pattern 'stream:candles:*' | sort
stream:candles:blofin:BTC-USDT:1h
stream:candles:blofin:BTC-USDT:1m
stream:candles:blofin:BTC-USDT:4h
stream:candles:blofin:ETH-USDT:1h
```

The stream was checked at 245 bars mid-warmup and again once it reached the full 500-bar
warmup; the forming candle is being written live.

Pushed as `14f498a`.

---

## Phase 2 — dashboard-api chart endpoints

`GET /positions/:id/candles` and `GET /orders/:id/candles`, mounted on the existing routers
at the same root-path convention as `/positions/:id/orders` (nginx adds the `/api/dashboard`
prefix; Express has none). The order route is declared ahead of `/:id`. Shared logic lives in
`dashboard-api/src/chartData.ts`.

One response carries candles + geometry + overlay, because the chart cannot place its box
until it has all three.

### A closed position (filled, then closed)

```
$ docker compose exec -T nginx wget -qO- \
    "http://dashboard-api:8003/positions/09439ab2-7d72-4303-ad4a-8b0d5cf5df19/candles?limit=3"

{"symbol":"ETH-USDT","exchange":"blofin","timeframe":"1h","timeframe_requested":"1h",
 "bar_seconds":3600,
 "candles":[
   {"time":1785056400000,"open":1880.56,"high":1884.91,"low":1880.49,"close":1882.94,"volume":188.363},
   {"time":1785060000000,"open":1883.13,"high":1886.71,"low":1882.01,"close":1885.02,"volume":507.943},
   {"time":1785063600000,"open":1885.03,"high":1887,"low":1883.76,"close":1885,"volume":187.111},
   {"time":1785067200000,"open":1885.3,"high":1893,"low":1884.71,"close":1886.53,"volume":1009.319}],
 "geometry":{"shape":"no_pattern","fit_quality":"strong","lower_touches":5,"upper_touches":4,
   "lower_boundary":1877.876667,"upper_boundary":1893.762973,"pattern_age_bars":38,
   "position_in_range_pct":45.47,"convergence_pct_per_bar":-0.0124},
 "geometry_at":1785067224598,
 "overlay":{"side":"long","status":"closed","placed_at":1784721652162,"filled_at":1784810797196,
   "entry_price":1904.2,"stop_price":1891.1,"target_price":1955,
   "closed_at":1784811735908,"close_price":1906.8,"current_price":1886.53},
 "note":"Candles from blofin — hyperliquid is not ingested."}
```

Four candles for `limit=3`: three closed bars plus the forming one, which is appended when it
is newer than the last closed bar.

### A resting order (never filled)

```
$ docker compose exec -T nginx wget -qO- \
    "http://dashboard-api:8003/orders/86ee9b20-b1de-4ea8-ae41-48f51b514ee2/candles?limit=3"

 …same candles/geometry…
 "overlay":{"side":"buy","status":"pending","placed_at":1785056481618,"filled_at":null,
   "entry_price":1877.876667,"stop_price":1872.5,"target_price":1888.8,
   "closed_at":null,"close_price":null,"current_price":1886.53}
```

`filled_at: null` is what suppresses the inner progress box — a resting order shows only the
outer stop→target span.

### Default limit, and a missing row

```
$ …/positions/09439ab2-…/candles          # no limit param
candles: 301 first: 1783987200000 last: 1785067200000 tf: 1h

$ …/positions/00000000-0000-0000-0000-000000000000/candles
HTTP/1.1 404 Not Found

$ docker compose logs dashboard-api --tail 8
dashboard-api-1  | Database pool initialized.
dashboard-api-1  | Initializing Redis...
dashboard-api-1  | Redis clients created, connecting...
dashboard-api-1  | Redis client and subscriber initialized.
dashboard-api-1  | PnL WebSocket server ready on /ws/pnl
dashboard-api-1  | WebSocket server ready. Subscribed to: orders:received, orders:routed, orders:filled, orders:failed
dashboard-api-1  | [livePnl] starting ticker, interval=1000ms
dashboard-api-1  | Dashboard API listening on :8003
```

300 closed bars + the forming one; no errors in the log.

### One thing the first build got wrong

The first cut resolved the candle stream from the account's exchange alone. `eth-ai-34d2`
trades on **hyperliquid**, but `market-ingestion` only ingests from **blofin**, so the
endpoint returned an empty chart:

```
"exchange":"hyperliquid","timeframe":null,"candles":[],
"note":"No candle stream ingested for ETH-USDT on hyperliquid…"
```

Fixed by probing the account's venue first and then `INGESTION_EXCHANGE`, and saying so in
`note` when the fallback is used (visible in the output above). Empty `candles` now means the
symbol really is not ingested anywhere.

### End-to-end, once the live geometry landed

The same order endpoint after the 13:00 cycle — the chart-replay fields now flow all the way
from `geometry.py` through `ai_signal_log` to the API response:

```
$ …/orders/86ee9b20-b1de-4ea8-ae41-48f51b514ee2/candles?limit=2

timeframe      : 1h | bar_seconds: 3600 | candles: 3
geometry keys  : ['anchor_ts', 'bar_seconds', 'convergence_pct_per_bar', 'first_swing_ts',
                  'fit_quality', 'lower_boundary', 'lower_slope', 'lower_touches',
                  'pattern_age_bars', 'position_in_range_pct', 'shape', 'swing_highs',
                  'swing_lows', 'upper_boundary', 'upper_slope', 'upper_touches']
slopes         : upper 0.95339202  lower 0.72031746
anchor_ts      : 1784638800000  first_swing_ts: 1784926800000  bar_seconds: 3600
swings         : 9 highs, 14 lows
overlay        : {"side": "buy", "status": "pending", "placed_at": 1785056481618,
                  "filled_at": null, "entry_price": 1878.596984, "stop_price": 1873.13,
                  "target_price": 1894.716365, "closed_at": null, "close_price": null,
                  "current_price": 1885.31}
```

Pushed as `a50755b`.

---

## Phase 3 — dashboard-ui: two layers and the embed

### The layer split

```
src/charts/
├── core/                       Layer A — pure TypeScript, zero chart-library imports
│   ├── types.ts                candles, geometry payload, overlay, box model,
│   │                           and the ChartAdapter / ChartHandle interfaces
│   ├── riskReward.ts           computeRiskReward() + snapToBar / snapToSeries
│   ├── geometryLines.ts        computeGeometryModel() — boundary lines in (time, price)
│   ├── index.ts
│   └── __tests__/riskReward.test.ts
├── adapters/
│   └── lightweightCharts/      Layer B — the ONLY folder importing the engine
│       ├── index.ts            implements ChartAdapter: series, price lines, markers
│       └── riskRewardPrimitive.ts   canvas renderer via priceToCoordinate/timeToCoordinate
└── index.ts                    registry: the single import to change to swap engines
```

`src/components/ExpandableChart.tsx` imports `chartAdapter` and the two pure functions — never
the library. `Positions.tsx` and `Orders.tsx` each gained one import and one line.

Layer A returns plain geometry only — the outer box (stop→target, from the placed bar), the
inner progress box (entry→current, from the fill bar), and the derived risk % / reward % /
R:R / progress-to-target % / progress-to-stop %. No pixels, no DOM, no engine types.

### Layer A tests — 28, and they pass with no chart library installed

```
$ npx vitest run
 RUN  v3.2.7 /home/cristi/matp/dashboard-ui
 ✓ src/charts/core/__tests__/riskReward.test.ts (28 tests) 484ms
 Test Files  1 passed (1)
      Tests  28 passed (28)
```

The "engine-agnostic" claim was tested by removing the library from `node_modules` and
re-running:

```
$ mv node_modules/lightweight-charts /tmp/…/lwc-hidden
### lightweight-charts is NOT installed:
0
 RUN  v3.2.7 /home/cristi/matp/dashboard-ui
 ✓ src/charts/core/__tests__/riskReward.test.ts (28 tests) 942ms
 Test Files  1 passed (1)
      Tests  28 passed (28)
### restored:
node_modules/lightweight-charts
```

### Engine isolation in the source

```
$ grep -rl "from 'lightweight-charts'" dashboard-ui/src
dashboard-ui/src/charts/adapters/lightweightCharts/riskRewardPrimitive.ts
dashboard-ui/src/charts/adapters/lightweightCharts/index.ts

$ grep -rn "lightweight" dashboard-ui/src/charts/core dashboard-ui/src/pages dashboard-ui/src/components
dashboard-ui/src/charts/core/__tests__/riskReward.test.ts:3: * and nothing it imports, may reach for lightweight-charts or any engine.
```

Exactly the two adapter files. The only other hit is a comment in the test header.

### Type check

```
$ cd dashboard-ui && npx tsc --noEmit
tsc exit: 0

$ cd dashboard-api && npx tsc --noEmit
dashboard-api tsc exit: 0
```

### The served bundle

```
$ ./scripts/redeploy.sh dashboard-ui
$ docker compose ps dashboard-ui
dashboard-ui Up 4 seconds

$ docker compose exec -T dashboard-ui grep -rl 'lightweight-charts' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-CwpQoEdU.js

$ docker compose exec -T dashboard-ui grep -rl 'Hide chart' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-CwpQoEdU.js

$ docker compose exec -T dashboard-ui grep -rl 'No stop or target recorded' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-CwpQoEdU.js

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-CwpQoEdU.js
```

The live asset hash matches the file containing the new strings, so nginx is serving this
build.

**One check from the brief could not work as written.** `grep -rl 'ChartAdapter'
/usr/share/nginx/html` finds nothing, and always would: `ChartAdapter` is a TypeScript
`interface`, erased at compile time and never emitted to JavaScript. The three greps above
are the runtime-observable equivalents — the adapter's engine name, the expand control, and
the fallback copy from the embed component.

```
$ docker compose exec -T dashboard-ui grep -rl 'ChartAdapter' /usr/share/nginx/html
(not found — TypeScript interfaces are erased at compile time)
```

### Mobile

- The expand control is a full-width button with `min-height: 44px`.
- The chart is `width: 100%` and calls `fitContent()` on mount, so the whole box is visible
  without horizontal scrolling; one-finger drag pans and pinch zooms, and mouse-wheel
  scroll-hijacking is disabled so the page still scrolls past the chart.
- Collapsed rows mount nothing: no fetch, no canvas, no `ResizeObserver`.

Pushed as `b53778a`.

---

## Scope

Files changed on the branch:

```
$ git diff --stat main...HEAD
 ai-signal-generator/app/data/geometry.py           |  77 +++++
 ai-signal-generator/tests/test_builder_geometry.py |  25 ++
 ai-signal-generator/tests/test_geometry.py         | 109 +++++
 dashboard-api/src/chartData.ts                     | 346 ++++++++++++++++
 dashboard-api/src/routes/orders.ts                 |  16 +
 dashboard-api/src/routes/positions.ts              |  17 +
 dashboard-ui/package-lock.json                     | 444 ++++++++++++++++++++-
 dashboard-ui/package.json                          |   7 +-
 .../src/charts/adapters/lightweightCharts/index.ts | 211 ++++++++++
 .../lightweightCharts/riskRewardPrimitive.ts       | 172 ++++++++
 .../src/charts/core/__tests__/riskReward.test.ts   | 375 +++++++++++++++++
 dashboard-ui/src/charts/core/geometryLines.ts      | 103 +++++
 dashboard-ui/src/charts/core/index.ts              |   6 +
 dashboard-ui/src/charts/core/riskReward.ts         | 175 ++++++++
 dashboard-ui/src/charts/core/types.ts              | 150 +++++++
 dashboard-ui/src/charts/index.ts                   |  14 +
 dashboard-ui/src/components/ExpandableChart.tsx    | 206 ++++++++++
 dashboard-ui/src/pages/Orders.tsx                  |   3 +
 dashboard-ui/src/pages/Positions.tsx               |   3 +
 dashboard-ui/src/utils/precision.ts                |  12 +
 docker-compose.yml                                 |   7 +-
 21 files changed, 2474 insertions(+), 4 deletions(-)
```

`docker-compose.yml` changed in two env blocks only: `INGESTION_SUBSCRIPTIONS` on
`market-ingestion` and `INGESTION_EXCHANGE` on `dashboard-api`. `precision.ts` gained one
exported helper (`priceDecimals`), reusing the existing rules table so the chart's price axis
and the table cells cannot drift apart.

`order-executor`, `order-listener` and `strategy-tester` were not touched. No `ports:` mapping
was added. `node_dispatch.py` was read to confirm it serialises whatever dict
`detect_geometry()` returns (`json.dumps(geometry_data)`) and needs no change — it was not
edited.

---

## Notes and known limits

1. **Candles may come from a different venue than the trade.** `eth-ai-34d2` runs on
   hyperliquid; only blofin is ingested. The endpoint falls back and labels it in `note`,
   which the UI shows under the chart. Ingesting hyperliquid would remove the caveat.

2. **The charted timeframe is the strategy's `strategies.interval` (1h), but the geometry the
   LLM saw was computed on its `cycle_interval`** — which varies with state (4h with no
   position, 15m with one open, 5m at risk). This is Open Design Question #4 in
   `docs/ROADMAP.md` (separating `ohlcv_timeframe` from the analysis interval); until that is
   decided, the boundary lines can be drawn on a different resolution than they were fitted
   on. The snapping described above keeps this visually correct rather than corrupting the
   time axis, but the fit itself is still the strategy's, not the chart's.

3. **Stop and target come from the opening order.** A later re-fit amend writes a new order
   row, so an amended stop is not reflected on a position's chart. Charting the amend history
   would need the order timeline the `/positions/:id/orders` endpoint already returns.

4. **The geometry line's right-hand anchor is approximated at the series end.** `geometry_data`
   records the boundary values and `anchor_ts`, but not which bar index the fit ended on, so
   the values are treated as belonging to the newest bar. When geometry is a few minutes stale
   this shifts the line by well under a bar.

5. **`ChartAdapter` cannot be grepped in the bundle** — it is a type, erased at compile time.
   See the Phase 3 section for the runtime-observable checks used instead.

6. **Swapping engines** means: add `src/charts/adapters/<engine>/` implementing `ChartAdapter`,
   change the one import in `src/charts/index.ts`. Nothing in `core/`, `ExpandableChart.tsx`,
   `Positions.tsx` or `Orders.tsx` needs to change. The klinecharts and ECharts options
   surveyed in the investigation report remain viable behind this seam.
