# Prompt 3b-2 — Exit Calculator Wired to Live Feed

## What was built

- **Migration 028** (`db/migrations/028_exit_reason.sql`): adds `exit_reason` (varchar 20) and
  `size_pct` (numeric) columns to `shadow_signals`; drops the old 3-column unique constraint and
  replaces it with a functional 4-key unique index:
  `(strategy_id, signal, signal_bar_time, COALESCE(exit_reason, ''))`.

- **`signal-engine/app/strategies/base.py`**: `Signal` gains two optional fields:
  `exit_reason: str | None` and `size_pct: float | None`.

- **`signal-engine/app/shadow_store.py`**: INSERT updated to include `exit_reason` and `size_pct`;
  `ON CONFLICT` updated to the new 4-key expression.

- **`signal-engine/app/redis_reader.py`**: `read_forming_candle(redis, exchange, symbol, timeframe)`
  added — reads `candle:forming:{exchange}:{symbol}:{timeframe}` and returns a typed candle dict.

- **`signal-engine/app/strategies/test_harness.py`**:
  - `self.last_rsi: float | None = None` added to `__init__`.
  - `mark_flat()` method added — clears `_position_side`.
  - `evaluate()` stores `self.last_rsi = float(rsi.iloc[-1])` after computing RSI.

- **`signal-engine/app/engine.py`**: fully rewritten:
  - Warmup now tracks bracket on entries/flips (bracket created at entry price, cleared on flip),
    but does **not** feed 1h high/low as exit ticks.
  - Three concurrent tasks launched after warmup:
    - `_entry_loop` — 1h subscription; RSI condition-modify on each close; bracket create/clear on entries/flips.
    - `_near_tick_loop` — ~1s poll of forming 1m candle; point-price update (`high=low=price=close`).
    - `_safety_net_loop` — closed 1m subscription; real `high/low` to catch wicks.
  - `_store_exit_leg` helper builds a `Signal` with `exit_reason` + `size_pct` and calls
    `store_shadow_signal`.
  - `probe_exit` async function + `__main__` block for one-shot live test.

---

## Step 6 — Startup logs

```
signal-engine-1  | 2026-06-23 16:21:07,316 [INFO] app.engine: engine: loaded strategy=tv_test_harness symbol=BTC-USDT tf=1h mode=shadow
signal-engine-1  | 2026-06-23 16:21:07,490 [INFO] app.redis_reader: redis_reader: loaded 500 historical bars blofin BTC-USDT 1h
  [... warmup signals ...]
signal-engine-1  | 2026-06-23 16:21:27,726 [INFO] app.engine: engine: warmup complete strategy=tv_test_harness bars=500 position=short bracket=active
signal-engine-1  | 2026-06-23 16:21:27,730 [INFO] app.engine: engine: entering live subscription strategy=tv_test_harness BTC-USDT 1h
signal-engine-1  | 2026-06-23 16:21:27,759 [INFO] app.engine: engine: near-tick monitor started strategy=tv_test_harness
signal-engine-1  | 2026-06-23 16:21:27,761 [INFO] app.engine: engine: 1m safety-net subscribed strategy=tv_test_harness
signal-engine-1  | 2026-06-23 16:21:27,790 [INFO] app.redis_reader: redis_reader: subscribed to candles:closed:blofin:BTC-USDT:1h
signal-engine-1  | 2026-06-23 16:21:27,793 [INFO] app.redis_reader: redis_reader: subscribed to candles:closed:blofin:BTC-USDT:1m
signal-engine-1  | 2026-06-23 16:21:28,765 [INFO] app.engine: engine: near-tick exit strategy=tv_test_harness reason=tp1 size=50.0 price=62563.60
signal-engine-1  | 2026-06-23 16:21:28,774 [INFO] app.engine: engine: near-tick exit strategy=tv_test_harness reason=tp2 size=50.0 price=62563.60
```

All three loops confirmed: warmup → `entering live subscription` → `near-tick monitor started` →
`1m safety-net subscribed` → both pub/sub channels subscribed. The near-tick loop fired a live
tp1 + tp2 exit within 1 second of going live (the warmup-recovered short bracket hit TP at live
price 62563.60).

## Grep count (wiring keywords in deployed engine.py)

```
$ docker compose exec signal-engine grep -c "read_forming_candle\|active_bracket\|mark_flat" app/engine.py
8
```

## probe_exit output

```
$ docker compose exec signal-engine python -m app.engine probe_exit
probe_exit: symbol=BTC-USDT forming_t=1782231720000
probe_exit: live_price=62580.2000  entry=62268.8557
probe_exit: tp1=62580.2000  stop=61832.9737  trail_arm=62517.9311
probe_exit: legs=[{'exit_reason': 'tp1', 'size_pct': 50.0}]
probe_exit: bracket.closed=False
```

Entry set to `live_price / 1.005 = 62268.86`. TP1 = `entry * 1.005 = 62580.20` — exactly the
live price, so `high >= tp1` fires the tp1 leg (50% size). No DB write was performed.

## shadow_signals counts (unchanged entry rows; new exit rows visible)

```sql
SELECT signal, exit_reason, count(*) FROM public.shadow_signals
WHERE strategy_id='tv_test_harness' GROUP BY 1,2 ORDER BY 1,2;

   signal    | exit_reason | count
-------------+-------------+-------
 close_long  |             |    23
 close_short | tp1         |     1
 close_short | tp2         |     1
 close_short |             |    22
 open_long   |             |    23
 open_short  |             |    23
```

Entry signals unchanged (all have `exit_reason = NULL`). Two new bracket-exit rows appeared:
`close_short / tp1` and `close_short / tp2` — the live near-tick exit recorded immediately after
the short bracket recovered from warmup.

## Deferred

State at engine restart: bracket peak/leg state resets to entry price only; exits missed while
down are not back-filled. The shadow-vs-TV diff will surface any gap; 1m-history catch-up deferred
to a later phase.
