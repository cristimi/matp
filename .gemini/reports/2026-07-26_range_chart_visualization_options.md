# Candlestick chart with orders, positions and the AI-identified range

**Date:** 2026-07-26
**Scope:** investigation only — no code was changed.
**Goal:** show, first for the geometric-range strategy, a candle chart overlaid with
(a) the range boundaries from the last AI analysis, (b) pending orders, (c) open positions.

---

## 1. Verdict

Feasible, and most of the required data already exists in the stack. Two data gaps must be
closed before any chart library can draw the picture correctly. Neither gap needs a database
migration.

---

## 2. What already exists

### 2.1 Candles — Redis streams from `market-ingestion`

`market-ingestion` writes closed candles to a Redis stream and publishes each close on a
pub/sub channel (`market-ingestion/app/redis_store.py`), capped at `STREAM_MAXLEN = 2000`.

```
$ docker compose exec -T redis redis-cli --scan --pattern 'stream:candles:*'
stream:candles:blofin:BTC-USDT:4h
stream:candles:blofin:BTC-USDT:1m
stream:candles:blofin:BTC-USDT:1h

$ docker compose exec -T redis redis-cli XLEN stream:candles:blofin:BTC-USDT:4h
655
```

Key layout (from `redis_store.py`):

```python
def _stream_key(exchange, symbol, timeframe):   return f"stream:candles:{exchange}:{symbol}:{timeframe}"
def _forming_key(exchange, symbol, timeframe):  return f"candle:forming:{exchange}:{symbol}:{timeframe}"
def _closed_channel(exchange, symbol, tf):      return f"candles:closed:{exchange}:{symbol}:{tf}"
```

`dashboard-api` already has a Redis client (`dashboard-api/src/redis.ts`) and a WebSocket
layer (`src/ws/orderFeed.ts`, `src/ws/pnlFeed.ts`), so both a REST candles endpoint and a
live "candle closed" push are incremental work, not new infrastructure.

### 2.2 The identified range — `ai_signal_log.geometry_data`

Every AI cycle persists the geometry as a `jsonb` column (`ai_signal_log.geometry_data`,
written in `ai-signal-generator/app/graph/nodes/node_dispatch.py`). Live sample:

```
$ docker compose exec -T postgres psql -U matp -d matp -t -c \
  "SELECT strategy_id, triggered_at, proposed_action, jsonb_pretty(geometry_data)
     FROM ai_signal_log
    WHERE geometry_data IS NOT NULL AND prompt_template='geometric_range'
    ORDER BY triggered_at DESC LIMIT 1;"

 eth-ai-34d2 | 2026-07-26 10:00:36.028211+00 | amend_order | {
             |                               |               "shape": "no_pattern",
             |                               |               "fit_quality": "strong",
             |                               |               "lower_touches": 5,
             |                               |               "upper_touches": 4,
             |                               |               "lower_boundary": 1876.436032,
             |                               |               "upper_boundary": 1891.856189,
             |                               |               "pattern_age_bars": 36,
             |                               |               "position_in_range_pct": 46.46,
             |                               |               "convergence_pct_per_bar": -0.0124
             |                               |             }
```

### 2.3 Orders and positions

Both tables carry everything a price-line overlay needs.

`orders`: `symbol, side, order_type, price, tp_price, sl_price, status, strategy_id,
received_at, exchange_order_id, actual_fill_price`.

`strategy_positions`: `symbol, side, entry_price, size, leverage, liquidation_price,
status, opened_at, closed_at, closing_price, pnl_unrealized`.

`dashboard-api` already serves both (`src/routes/orders.ts`, `src/routes/positions.ts`).

### 2.4 UI stack

`dashboard-ui` is React 18 + Vite + TypeScript + Tailwind, with **Recharts 2.12** already
installed and used on `pages/Dashboard.tsx` and `pages/StrategyDetail.tsx`. Adding a chart
library is a normal npm dependency plus an image rebuild
(`./scripts/redeploy.sh dashboard-ui`).

---

## 3. The two gaps

### Gap A — the stored range has no slope (blocking for tilted patterns)

`detect_geometry()` fits a straight line through the last `MAX_SWINGS = 4` swing highs and
the last 4 swing lows, then **evaluates both lines only at the final bar** and returns those
two scalars (`ai-signal-generator/app/data/geometry.py`):

```python
last_idx       = len(candles) - 1
upper_boundary = upper_slope * last_idx + upper_intercept
lower_boundary = lower_slope * last_idx + lower_intercept
...
return {
    'shape':                   shape,
    'upper_boundary':          round(upper_boundary, 6),
    'lower_boundary':          round(lower_boundary, 6),
    'upper_touches':           upper_touches,
    'lower_touches':           lower_touches,
    'convergence_pct_per_bar': round(conv_rate, 4),
    'pattern_age_bars':        pattern_age_bars,
    'position_in_range_pct':   round(pos_in_range, 2),
    'fit_quality':             fit_quality,
}
```

`upper_slope`, `lower_slope`, the intercepts and the swing indices are all computed and then
discarded. Consequence: the eight sloped shapes the detector can classify
(`ascending_channel`, `descending_channel`, `ascending_triangle`, `descending_triangle`,
`rising_wedge`, `falling_wedge`, `broadening`) **cannot be drawn back through history** — the
chart could only show two flat lines pinned at the last bar, which would misrepresent the
pattern the LLM actually reasoned about.

**Fix:** add to the returned dict (and therefore to the `jsonb` payload — no migration, the
column is schemaless):

| Field | Purpose |
|---|---|
| `upper_slope`, `lower_slope` | price change per bar, so the line can be extended left/right |
| `anchor_ts` | open-time (ms) of the bar used as `x = 0` in the fit |
| `bar_seconds` | timeframe in seconds, so the UI converts bar index → timestamp |
| `first_swing_ts` | where the drawn trendline should start |
| `swing_highs`, `swing_lows` | `[ts, price]` pairs — lets the chart mark the touch points |

Touched files: `ai-signal-generator/app/data/geometry.py` (return dict only) and the
existing tests `tests/test_geometry.py`, `tests/test_builder_geometry.py`. `node_dispatch.py`
serialises whatever dict it is given, so it needs no change.

### Gap B — no candles for ETH-USDT

The geometric-range strategy runs on ETH, but ingestion only covers BTC:

```
$ docker compose exec -T postgres psql -U matp -d matp -c \
  "SELECT s.id, s.symbol, s.interval, c.template_id
     FROM ai_strategy_config c JOIN strategies s ON s.id=c.strategy_id ORDER BY 4;"

             id             |  symbol   | interval |   template_id
----------------------------+-----------+----------+-----------------
 xrp-ai-3844                | XRP-USDT  | 1h       | breakout
 bnb-ai-scalper-edbb        | BNB-USDT  | 1h       | flow_swing
 eth-ai-34d2                | ETH-USDT  | 1h       | geometric_range
 hype-breakout-da2e         | HYPE-USDT | 1h       | mean_reversion
 tao-ai-range-rotation-d257 | TAO-USDT  | 1h       | range_rotation
 ai-btc-6f8c                | BTC-USDT  | 1h       | regime_router
 sol-ai-6486                | SOL-USDT  | 1h       | trend_following
```

```yaml
# docker-compose.yml — market-ingestion
INGESTION_SUBSCRIPTIONS: BTC-USDT:1h,BTC-USDT:4h,BTC-USDT:1m
INGESTION_WARMUP_CANDLES: "500"
```

`ai-signal-generator` fetches its own OHLCV per cycle through ccxt
(`app/data/ohlcv.py`, called from `graph/nodes/node_ingest.py:130`) and does not persist it —
the candles the LLM saw are gone once the cycle ends.

Three ways to close this gap:

| Option | How | Notes |
|---|---|---|
| **B1 — extend ingestion** (recommended) | add `ETH-USDT:1h` (later the other five symbols) to `INGESTION_SUBSCRIPTIONS` | Candles land in Redis, `dashboard-api` reads them, everything stays behind the existing exchange boundary. One extra websocket per symbol. |
| **B2 — endpoint on `ai-signal-generator`** | expose `GET /candles?symbol=&interval=&bars=` reusing `app/data/ohlcv.py`, proxied through nginx | Fastest to build, no extra ingestion load, but adds a second candle path and re-hits the exchange on every chart open. |
| **B3 — public OHLCV via `order-executor` adapter** | add a public `fetch_ohlcv` to the adapter layer | Most consistent with the "all exchange calls go through order-executor adapters" rule, but the largest change of the three. |

---

## 4. Chart library options

All four are permissively licensed, install from npm, and bundle into the image — no external
CDN, which matters because the stack is self-hosted behind nginx and must not depend on
outbound calls at page load. Versions checked on 2026-07-26 via `npm view <pkg> version`.

### Option 1 — TradingView `lightweight-charts` **5.2.0** (Apache-2.0) — *recommended*

Repo: `github.com/tradingview/lightweight-charts`

- Purpose-built for this exact picture: candlestick series, canvas-rendered, smooth pan/zoom,
  comfortable with the full 2000-bar stream.
- `series.createPriceLine({ price, color, lineStyle, title })` draws a labelled horizontal
  line on the price axis — a one-to-one match for pending order price, `tp_price`,
  `sl_price`, position entry and liquidation price.
- Sloped range boundaries = two extra `LineSeries` built from `slope * bar + intercept`.
- Entry/exit markers on the candles via `createSeriesMarkers`.
- ~45 KB gzipped, no React dependency (mount into a `div` from a `useEffect`), or use the
  community `lightweight-charts-react-components` wrapper.
- **Trade-off:** shading the area *between* the two boundaries needs a small custom
  primitive (the v5 plugin API supports it, but it is code you write).

### Option 2 — `klinecharts` **10.0.0** (Apache-2.0)

Repo: `github.com/klinecharts/KLineChart`

- Ships an overlay/drawing-tool system out of the box: trend lines, rays, rectangles,
  horizontal segments — so the range zone and the boundary lines need no custom plugin.
- Built-in indicator set, useful later if you want RSI/MACD panes under the candles.
- **Trade-off:** smaller Western community, part of the documentation is Chinese-first.

### Option 3 — `echarts` **6.1.0** (Apache-2.0)

Repo: `github.com/apache/echarts` · React wrapper: `echarts-for-react`

- Native `candlestick` series; `markLine` and `markArea` give labelled price lines and a
  shaded range band with pure configuration, no custom drawing code.
- Very well documented, huge ecosystem.
- **Trade-off:** ~330 KB minified for a single chart; `dataZoom` pan/zoom is less fluid than
  a canvas-native financial chart at 2000 bars.

### Option 4 — Recharts 2.12 (already installed, MIT)

- Zero new dependency and it already matches the look of `Dashboard.tsx` /
  `StrategyDetail.tsx`.
- Candles via a custom `<Bar shape={...}>`; range via `<ReferenceArea y1 y2>`; orders and
  positions via `<ReferenceLine y label>`.
- **Trade-off:** the candle body/wick renderer is hand-written, sloped boundaries need a
  second composed series, there is no pan/zoom, and SVG rendering degrades past a few
  hundred bars. Realistic only as a same-day proof of concept on a fixed 150-bar window.

### Also considered

- `react-financial-charts` **2.0.1** (MIT) — React-native API and rich annotations, but
  development has been quiet and it is heavier to wire up than option 1.
- **Freqtrade's FreqUI** (`github.com/freqtrade/frequi`, GPL-3.0) — worth reading as a
  reference implementation: it plots trades, entry/exit markers and indicator overlays on
  candles using ECharts. It is Vue, so it is a model to copy from, not a drop-in component.

---

## 5. Recommended plan

1. **Gap A** — extend the `detect_geometry()` return dict with slopes, anchor timestamp,
   bar seconds and swing points; update the two geometry tests. No migration.
2. **Gap B1** — add `ETH-USDT:1h` to `INGESTION_SUBSCRIPTIONS`, redeploy `market-ingestion`,
   confirm `stream:candles:blofin:ETH-USDT:1h` fills to the 500-bar warmup.
3. **`dashboard-api`** — `GET /api/candles?exchange=&symbol=&timeframe=&limit=` reading the
   Redis stream plus the forming candle; optionally reuse the existing WS pattern to push
   `candles:closed:*` to the browser.
4. **`dashboard-ui`** — add `lightweight-charts`, build a `RangeChart` component on
   `pages/StrategyDetail.tsx`: candles + two boundary lines from the newest
   `geometry_data` + price lines for pending orders, TP, SL, entry and liquidation.
5. Redeploy with `./scripts/redeploy.sh dashboard-api dashboard-ui` and verify the served
   bundle contains the new component string.

Rough effort: ~half a day for steps 1–3, ~one day for step 4.

---

## 6. Open decision for the user

Which library to adopt — `lightweight-charts` (recommended), `klinecharts` (free drawing
tools), `echarts` (free shaded band, heavier), or a Recharts proof of concept with no new
dependency.

---

## Appendix — unrelated status note

The Gemini 14-day social-listener backtest that was running earlier did not complete. Its
watcher timed out, and the `social-listener` container was recreated at 09:57 (deploy of
commit `38bbe49`), which emptied the container's `/tmp`:

```
$ docker compose exec -T social-listener ls -la /tmp/
total 8
drwxrwxrwt 2 root root 4096 Jun 23 00:00 .
drwxr-xr-x 1 root root 4096 Jul 26 09:57 ..
```

No extraction or replay output survived. The input data (`ohlcv_14d.json`,
`funding_14d.json`) is still on the host, so the run can be repeated — it was not restarted
automatically because it spends Gemini API credit.
