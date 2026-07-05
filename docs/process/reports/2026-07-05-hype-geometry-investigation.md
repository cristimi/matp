# Investigate: Hype AI strategy repeatedly reports "not getting the geometry"

**Scope:** read-only investigation of `ai-signal-generator`. No code, config, or DB rows
were changed. Strategy identity confirmed as `hype-breakout-da2e` (not assumed).

## Root cause classification: **E — something else**

`detect_geometry()` is not starved, not erroring, and is not failing the documented
`MIN_R2_PATTERN` check. It is computing a real, high-confidence trendline pair — but the
shape those trendlines describe (upper boundary rising, lower boundary falling — a
**broadening/diverging formation**) has **no matching branch** in the shape-classification
`if/elif` chain in `detect_geometry()`. It falls through to the final `else: shape =
'no_pattern'` catch-all. Per `builder.py`'s `_render_geometry()`, `no_pattern` and "no data
at all" render identically — the whole `GEOMETRIC PATTERN` section is omitted — so the LLM
sees nothing and (correctly, from its own perspective) reports geometry as missing.

This is a **classifier gap**, not a data, config, or interval problem.

## Phase 1 — Config truth

```sql
SELECT s.id, s.name, s.symbol,
       a.use_geometry, a.use_technical, a.lookback_days,
       a.interval_no_position, a.interval_position_open, a.template_id
FROM strategies s
JOIN ai_strategy_config a ON a.strategy_id = s.id
WHERE s.name ILIKE '%hype%' OR s.symbol ILIKE '%HYPE%';
```
```
        id          |     name      |  symbol   | use_geometry | use_technical | lookback_days | interval_no_position | interval_position_open |   template_id
--------------------+---------------+-----------+--------------+---------------+---------------+----------------------+------------------------+-----------------
 hype-breakout-da2e | HYPE Breakout | HYPE-USDT | t            | t             |            90 | 1h                    | 15m                    | geometric_range
(1 row)
```

`use_geometry` is **TRUE**. Geometry runs on the **1h** timeframe while flat (no position),
15m while a position is open. Rules out Category A (disabled) outright.

## Phase 2 — Container logs

```
docker logs matp-ai-signal-generator-1 2>&1 | grep -iE "geometry|OHLCV|open_orders" | tail -60
```
**No matches** across the full ~21h / 3658-line log buffer. There is no `geometry:<exc>` or
`ohlcv:<exc>` entry anywhere — rules out Category D (erroring) and Category B (starved via
fetch failure).

Filtering on the strategy id instead shows normal hourly cycles with no errors, e.g.:
```
2026-07-05 08:02:30,382 [INFO] app.scheduler: Triggering cycle strategy=hype-breakout-da2e reason=scheduled
2026-07-05 08:02:34,058 [WARNING] app.data.sentiment: fetch_open_interest error [blofin HYPE/USDT:USDT]: blofin fetchOpenInterest() is not supported yet
2026-07-05 08:02:35,609 [INFO] httpx: HTTP Request: GET http://order-listener:8001/strategies/hype-breakout-da2e/orders "HTTP/1.1 200 OK"
2026-07-05 08:02:55,413 [INFO] app.graph.nodes.node_dispatch: strategy=hype-breakout-da2e action=hold gate=False reason=hold_or_adjust — no webhook
```
(The only warning present is an unrelated, pre-existing Blofin `fetchOpenInterest` limitation —
not attributed to this issue.)

## Phase 3 — What the LLM actually saw

```sql
SELECT triggered_at, cycle_interval, data_sources_used, proposed_action, left(reasoning, 300) AS reasoning
FROM ai_signal_log
WHERE strategy_id = 'hype-breakout-da2e'
ORDER BY triggered_at DESC LIMIT 8;
```
```
         triggered_at          | cycle_interval |                        data_sources_used                        | proposed_action  |  reasoning (truncated)
-------------------------------+----------------+-----------------------------------------------------------------+------------------+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 2026-07-05 14:02:30.205605+00 | 1h             | {technical,geometry,fear_greed,funding_rate,open_interest,news} | hold             | The GEOMETRIC PATTERN section, which is critical for this geometry-driven strategy, is entirely missing from the input. ...
 2026-07-05 13:02:32.329105+00 | 1h             | {technical,geometry,fear_greed,funding_rate,open_interest,news} | hold             | The GEOMETRIC PATTERN section is missing from the analysis, which is critical for identifying valid trade setups ...
 2026-07-05 12:02:30.610145+00 | 1h             | {technical,geometry,fear_greed,funding_rate,open_interest,news} | hold             | No geometric pattern data was provided in the analysis, which is fundamental for this strategy's range and breakout detection. ...
 2026-07-05 11:02:30.449011+00 | 1h             | {technical,geometry,fear_greed,funding_rate,open_interest,news} | hold             | The GEOMETRIC PATTERN section is missing from the analysis, which is critical for this strategy. ...
 2026-07-05 10:02:30.332145+00 | 1h             | {technical,geometry,fear_greed,funding_rate,open_interest,news} | hold             | The 'GEOMETRIC PATTERN' section is missing from the input ...
 2026-07-05 09:02:30.403971+00 | 1h             | {technical,geometry,fear_greed,funding_rate,open_interest,news} | hold             | The GEOMETRIC PATTERN section, which is fundamental for this strategy's range and breakout analysis, is entirely missing. ...
 2026-07-05 08:02:30.38272+00  | 1h             | {technical,geometry,fear_greed,funding_rate,open_interest,news} | hold             | The price has broken below the lower boundary of the ascending channel (Current Price 68.848 vs Lower Boundary 71.930864). ...
 2026-07-05 07:02:30.208437+00 | 1h             | {technical,geometry,fear_greed,funding_rate,open_interest,news} | place_limit_long | Ascending Channel detected with strong fit quality and 2 touches on both boundaries. Placing a long limit order at the lower boundary (71.813838) ...
```

`data_sources_used` contains `geometry` on **every** row — confirmed as chip-only evidence per
the known trap (Phase 1's toggle, not proof the section rendered). The reasoning text is the
real signal: geometry was **present** through the 07:02 and 08:02 cycles (ascending channel,
strong fit), then **every cycle from 09:02 through 14:02** (6 consecutive hourly cycles)
reports the section missing. This is a real, sustained transition — not a one-off blip — and
lines up with candle closes, confirmed in Phase 4.

## Phase 4 — Live reproduction (decisive)

Fetched the same OHLCV the strategy uses (`blofin`, `HYPE/USDT`, `1h`, `lookback_days=90`)
and ran `detect_geometry()` on the closed candles directly inside the running container:

```
num raw candles: 1440
num closed_candles: 1439
```
(Far above the `SWING_WINDOW*2+3 = 9` floor — rules out Category B/starved.)

```python
result = detect_geometry(closed)
# {'shape': 'no_pattern', 'upper_boundary': 74.854342, 'lower_boundary': 68.043522,
#  'upper_touches': 2, 'lower_touches': 4, 'convergence_pct_per_bar': -0.2507,
#  'pattern_age_bars': 71, 'position_in_range_pct': 18.14, 'fit_quality': 'strong'}
```

`fit_quality: 'strong'` on a `no_pattern` result already contradicts the "genuine no_pattern"
path documented in the module (weak/insufficient fit) — worth digging into directly. Recomputed
the raw fit inputs:

```
recent_highs used: [(48, 66.505), (52, 67.173), (86, 71.993), (95, 71.344)]
recent_lows used:  [(84, 69.993), (101, 69.423), (111, 68.236), (115, 68.345)]
upper_r2: 0.9382   lower_r2: 0.9098    (min = 0.9098, well above MIN_R2_PATTERN=0.30)
upper_pct (%/bar): +0.1658            (rising  → "positive", not flat)
lower_pct (%/bar): -0.0849            (falling → "negative", not flat)
conv_rate: -0.2507                    (negative → diverging, not converging; also not parallel)
```

Walking `detect_geometry`'s own classification chain against these numbers:
- `min(r2) < MIN_R2_PATTERN`? **No** (0.91 ≥ 0.30) → does not hit the documented no-pattern rule
- flat/flat (horizontal_channel)? No — neither side is flat
- pos/pos parallel (ascending_channel)? No — lower side is negative
- neg/neg parallel (descending_channel)? No — upper side is positive
- flat-upper/pos-lower (ascending_triangle)? No — upper isn't flat
- neg-upper/flat-lower (descending_triangle)? No — upper isn't negative
- converging + pos/pos (rising_wedge)? No — not converging, lower isn't positive
- converging + neg/neg (falling_wedge)? No — not converging, upper isn't negative
- → **falls to the final `else: shape = 'no_pattern'`**

The upper boundary is rising while the lower boundary is falling — a classic **broadening /
megaphone formation** — which the shape taxonomy in `detect_geometry()` simply has no label
for. The fit is real and strong; the classifier just has no bucket to put it in, so it lands
in the same `no_pattern` value used for "insufficient/noisy data," which `_render_geometry()`
then drops from the prompt exactly like the empty-`{}` case.

**This is not a one-off snapshot.** Re-running `detect_geometry()` against the closed-candle
list truncated to simulate each of the last 8 hourly candle closes:

```
last_closed_candle_ts        shape               fit_quality  upper_touches  lower_touches  conv
2026-07-05 13:00:00 (now)    no_pattern          strong       2              4              -0.2507
2026-07-05 12:00:00          no_pattern          strong       2              4              -0.251
2026-07-05 11:00:00          no_pattern          weak         2              0               -0.0867
2026-07-05 10:00:00          no_pattern          weak         2              0               -0.0865
2026-07-05 09:00:00          no_pattern          weak         2              0               -0.0872
2026-07-05 08:00:00          no_pattern          weak         2              0               -0.0867
2026-07-05 07:00:00          ascending_channel   strong       2              2                0.0031
2026-07-05 06:00:00          ascending_channel   strong       2              2                0.0031
```

This lines up exactly with the `ai_signal_log` transition in Phase 3: the candle that closed
at 07:00 (feeding the 08:02 cycle) was still `ascending_channel`; from the 08:00 candle close
onward (feeding the 09:02 cycle and every cycle since) it has been `no_pattern`, continuously,
for 6+ hours.

For completeness, checked whether the earlier ("weak", `lower_touches=0`) hours were hitting
the documented `MIN_R2_PATTERN` reject instead of the taxonomy gap:

```
last_closed          upper_r2  lower_r2  min_r2   min_r2 < 0.30 ?
08:00 / 09:00 / 10:00 / 11:00  0.9382    0.4425    0.4425   False
```

`min_r2 = 0.4425` is comfortably above `MIN_R2_PATTERN = 0.30` in every one of those hours
too — so **none** of the 6 consecutive no-geometry cycles were caused by the documented
low-R² rejection. All six landed in the same unhandled-shape `else` branch (the exact slope
signs shift slightly hour to hour as swings roll in/out of the last-4-swing window, but upper
stays rising and lower stays non-parallel/non-matching throughout).

## Conclusion

- **Category:** E — something else (shape-taxonomy gap in `detect_geometry()`), not A/B/C/D.
- `use_geometry` is enabled; the interval (1h) is correct and unchanged; OHLCV fetch is
  healthy with 1439 closed candles; there is no exception anywhere in the geometry/OHLCV
  path; swing detection and trendline fits are working and are of *strong* quality (R² up to
  0.94).
- The actual defect: since the market moved from an ascending channel into a **broadening /
  diverging formation** (upper boundary trending up, lower boundary trending down) at the
  08:00 candle close, `detect_geometry()`'s classification `if/elif` chain has no shape label
  for that configuration and falls through to the generic `no_pattern` catch-all — which is
  visually and semantically indistinguishable, downstream in `_render_geometry()`, from
  "insufficient data." The LLM's repeated "geometry is entirely missing" reasoning is an
  accurate description of what it receives; the actual gap is one level up, in the shape
  classifier's coverage of divergent/broadening structures.
- No fix is proposed or applied per the scope of this investigation.
