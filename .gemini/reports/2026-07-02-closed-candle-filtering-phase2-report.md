# Closed-Candle-Only OHLCV Filtering — Phase 2 Report

**Date:** 2026-07-02
**Service changed:** `ai-signal-generator` only
**Follows:** Phase 1 (candle-close-aligned scheduler wake, commit `4df7b3a`) — see
`.gemini/reports/2026-07-02-candle-scheduling-report.md`

---

## Problem

Phase 1 aligned the scheduler's wake time to `candle_close + buffer_seconds`, but that only
guarantees the *previous* candle period is final — it does not guarantee `fetch_ohlcv`'s raw
response has no trailing partial candle. As soon as a period's boundary passes, the exchange
starts accumulating trades into the *next* candle; by the time the scheduler wakes (even 150s
later) that next candle can already have trades and gets returned as the last array entry. If
indicators (RSI/MACD/EMA/BB/VWAP) or geometry (swing/channel/wedge detection) are computed
against that mutable last candle, the pattern or level can shift or vanish between consecutive
cycles purely because the candle kept filling in — not because the market changed.

Two existing call sites read `candles[-1]` on purpose and must **not** be touched:
- `node_guard.py` sizing math uses `ohlcv_data['current_price']` as the live execution price —
  it should track the freshest trade, including a partial candle's close.
- `event_watcher.py::_check_volume_spike` explicitly wants the *current, still-forming* 1h
  candle's volume to detect a spike as it happens.

So the fix could not simply drop the trailing candle globally — it had to separate "data used
for pattern/indicator analysis" from "live price."

## Fix

`ai-signal-generator/app/data/ohlcv.py`:
- New pure helper `_split_closed_candles(candles, timeframe, now_epoch)` — drops any trailing
  candle whose period end (`timestamp/1000 + tf_sec`) is still in the future.
- `fetch_ohlcv()` now returns **both** `candles` (raw, unchanged, may include a forming last
  candle — used by `event_watcher` and for `current_price`) and a new `closed_candles` field
  (used for indicator/geometry input). `current_price` still reads `candles[-1]['close']`
  (live/freshest price, unchanged behavior). The 24h/7d `%` change lookback indices are now
  computed against `closed_candles` instead of `candles`, so those percentages aren't skewed by
  a partial trailing period.

`ai-signal-generator/app/graph/nodes/node_ingest.py`:
- `compute_indicators()` and `detect_geometry()` now receive `ohlcv_data['closed_candles']`
  instead of `ohlcv_data['candles']`.

No DB migration — this is a pure in-process data-shaping change, no new config/columns.

## Unit tests

New file `ai-signal-generator/tests/test_ohlcv.py` — 5 tests: trailing-partial-candle dropped,
all-closed input unchanged, empty input, unknown-timeframe fallback, and a `fetch_ohlcv`
integration test (mocked exchange) asserting `closed_candles` excludes the forming candle while
`candles`/`current_price` still reflect it.

### Full pasted test run (inside container, after redeploy)

```
$ docker compose exec -T ai-signal-generator python -m pytest /tmp/tests/ -v
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0 -- /usr/local/bin/python
collecting ... collected 31 items

../tmp/tests/test_geometry.py::test_horizontal_channel PASSED            [  3%]
../tmp/tests/test_geometry.py::test_ascending_channel PASSED             [  6%]
../tmp/tests/test_geometry.py::test_descending_channel PASSED            [  9%]
../tmp/tests/test_geometry.py::test_ascending_triangle PASSED            [ 12%]
../tmp/tests/test_geometry.py::test_descending_triangle PASSED           [ 16%]
../tmp/tests/test_geometry.py::test_rising_wedge PASSED                  [ 19%]
../tmp/tests/test_geometry.py::test_falling_wedge PASSED                 [ 22%]
../tmp/tests/test_geometry.py::test_no_pattern_diverging PASSED          [ 25%]
../tmp/tests/test_geometry.py::test_position_in_range PASSED             [ 29%]
../tmp/tests/test_geometry.py::test_too_few_candles PASSED               [ 32%]
../tmp/tests/test_geometry.py::test_insufficient_swings PASSED           [ 35%]
../tmp/tests/test_geometry.py::test_empty_candles PASSED                 [ 38%]
../tmp/tests/test_geometry.py::test_output_keys_present PASSED           [ 41%]
../tmp/tests/test_geometry.py::test_fit_quality_values PASSED            [ 45%]
../tmp/tests/test_ohlcv.py::test_drops_trailing_partial_candle PASSED    [ 48%]
../tmp/tests/test_ohlcv.py::test_keeps_all_when_last_candle_already_closed PASSED [ 51%]
../tmp/tests/test_ohlcv.py::test_empty_input_returns_empty PASSED        [ 54%]
../tmp/tests/test_ohlcv.py::test_unknown_timeframe_falls_back_to_1h PASSED [ 58%]
../tmp/tests/test_ohlcv.py::test_fetch_ohlcv_separates_closed_candles_from_live_price PASSED [ 61%]
../tmp/tests/test_scheduling.py::test_parse_interval_seconds_units PASSED [ 64%]
../tmp/tests/test_scheduling.py::test_parse_interval_seconds_invalid_unit PASSED [ 67%]
../tmp/tests/test_scheduling.py::test_1h_just_after_boundary PASSED      [ 70%]
../tmp/tests/test_scheduling.py::test_4h_just_after_boundary PASSED      [ 74%]
../tmp/tests/test_scheduling.py::test_1h_just_before_boundary PASSED     [ 77%]
../tmp/tests/test_scheduling.py::test_15m_just_before_boundary PASSED    [ 80%]
../tmp/tests/test_scheduling.py::test_1h_exactly_on_boundary PASSED      [ 83%]
../tmp/tests/test_scheduling.py::test_5m_exactly_on_boundary PASSED      [ 87%]
../tmp/tests/test_scheduling.py::test_wake_exactly_on_buffer_point_floors_to_min_sleep PASSED [ 90%]
../tmp/tests/test_scheduling.py::test_zero_buffer_exactly_on_boundary_floors_to_min_sleep PASSED [ 93%]
../tmp/tests/test_scheduling.py::test_naive_next_boundary_would_be_wrong_regression PASSED [ 96%]
../tmp/tests/test_scheduling.py::test_sleep_never_negative_across_a_full_period PASSED [100%]

============================= 31 passed in 14.03s ==============================
```

(Full geometry + scheduling suites included to confirm no regression from the `ohlcv.py`/
`node_ingest.py` changes; `pytest` was installed ad-hoc in the running container as in Phase 1 —
it's not a prod dependency.)

## Live verification against real exchange data

```
$ docker compose exec -T ai-signal-generator python3 - <<'EOF'
import asyncio
from app.data.ohlcv import fetch_ohlcv

async def main():
    r = await fetch_ohlcv("binance", "BTC/USDT", "15m", 2)
    print("candles:", len(r['candles']))
    print("closed_candles:", len(r['closed_candles']))
    print("last raw candle ts/close:", r['candles'][-1]['timestamp'], r['candles'][-1]['close'])
    print("last closed candle ts/close:", r['closed_candles'][-1]['timestamp'], r['closed_candles'][-1]['close'])
    print("current_price:", r['current_price'])

asyncio.run(main())
EOF
candles: 500
closed_candles: 499
last raw candle ts/close: 1782999900000 61941.98
last closed candle ts/close: 1782999000000 61961.64
current_price: 61941.98
```

Confirms: Binance's live 15m response for BTC/USDT does have a still-forming trailing candle
right now (`closed_candles` is one shorter than `candles`), `current_price` correctly tracks the
live/forming candle's close (61941.98), and the last *closed* candle (61961.64, one period
earlier) is what indicators/geometry will actually see.

## Deploy verification

```
$ ./scripts/redeploy.sh ai-signal-generator
...
▶ Verifying …
NAME                         IMAGE                      COMMAND                  SERVICE               STATUS
matp-ai-signal-generator-1   matp-ai-signal-generator   "uvicorn app.main:ap…"   ai-signal-generator   Up (health: starting)
✓ ai-signal-generator redeployed.

$ docker compose exec -T ai-signal-generator python -c "from app.graph.nodes.node_ingest import node_ingest; print('import OK')"
import OK

$ docker compose exec -T ai-signal-generator curl -sf http://localhost:8005/health
{"status":"ok","service":"ai-signal-generator"}
```

Live container logs after redeploy show a real startup cycle running clean end-to-end with the
new ingest wiring — `hype-breakout-da2e` fetched data, ran indicators/geometry against the
filtered candle set, got an LLM decision, and dispatched:

```
ai-signal-generator-1  | 2026-07-02 13:50:43,758 [INFO] app.graph.nodes.node_analyze: LLM [google/gemini-2.5-flash] → action=hold confidence=0.500
ai-signal-generator-1  | 2026-07-02 13:50:43,820 [INFO] app.graph.nodes.node_dispatch: strategy=hype-breakout-da2e action=hold gate=False reason=hold_or_adjust — no webhook
ai-signal-generator-1  | 2026-07-02 13:50:44,861 [INFO] app.scheduler: Scheduler strategy=hype-breakout-da2e sleeping 705s until candle-close+buffer wake (11.8min)
```

No errors, no `data_fetch_errors` entries for `indicators:`/`geometry:` in that cycle.

---

## Files changed

- `ai-signal-generator/app/data/ohlcv.py` — added `_split_closed_candles()`, new
  `closed_candles` field on `fetch_ohlcv()`'s return dict, 24h/7d `%` change now computed off
  `closed_candles`
- `ai-signal-generator/app/graph/nodes/node_ingest.py` — `compute_indicators`/`detect_geometry`
  now consume `closed_candles` instead of `candles`
- `ai-signal-generator/tests/test_ohlcv.py` — new (5 tests)

**Untouched by design:** `event_watcher.py::_check_volume_spike` (still wants the live/forming
candle's volume) and `node_guard.py` sizing math (still reads `current_price`, which stays live).

---

## Status

Phase 2 complete, deployed to `main`'s live stack, verified against real exchange data and a
live end-to-end cycle. This closes out the "Candle-Aligned Scheduling & Closed-Candle-Only
Analysis" arc (Phase 1 scheduler wake + Phase 2 candle filtering).
