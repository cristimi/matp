# Prompt 3b-3: Restart catch-up for open positions

## Summary

When signal-engine restarts holding an open position, it now replays all closed 1-minute bars
from the entry bar forward through the bracket before starting the live loops. Any exit that
occurred while the engine was down is recorded at its **real historical minute and price**,
not at the moment-of-restart price. Shadow-only; no trade execution.

**RSI condition-modify is skipped during catch-up** — no aligned 1h RSI history is available
at that point. The un-tightened stop is the conservative choice. Condition-modify resumes
on the next live 1h close if the bracket is still open.

---

## Catch-up log lines (first restart)

```
2026-06-23 20:26:45,270 [INFO] app.engine: engine: warmup complete strategy=tv_test_harness bars=500 position=short bracket=active
2026-06-23 20:26:47,737 [INFO] app.redis_reader: redis_reader: loaded 2000 historical bars blofin BTC-USDT 1m
2026-06-23 20:26:47,749 [INFO] app.engine: engine: catch-up replaying 1346 1m bars strategy=tv_test_harness from t=1782165600000
2026-06-23 20:26:47,759 [INFO] app.engine: engine: catch-up exit strategy=tv_test_harness reason=trail at bar_t=1782167280000 price=64038.8000
2026-06-23 20:26:47,760 [INFO] app.engine: engine: entering live subscription strategy=tv_test_harness BTC-USDT 1h
```

The exit was recorded at `bar_t=1782167280000` = **2026-06-22 22:28:00 UTC** (a historical
minute from the previous day), not at the restart time.

---

## Exit rows with bar_time (showing historical timestamps)

```
   signal    | exit_reason | size_pct |    signal_bar_time     | bar_close_price
-------------+-------------+----------+------------------------+-----------------
 close_short | trail       |      100 | 2026-06-22 22:28:00+00 | 64038.80
 close_short | tp1         |       50 | 2026-06-23 16:21:00+00 | 62563.60
 close_short | tp2         |       50 | 2026-06-23 16:21:00+00 | 62563.60
(3 rows)
```

The `trail` exit at `2026-06-22 22:28:00` is the catch-up exit — recorded at the real
historical 1m bar where the trailing stop was breached, not at restart time.

The `tp1` and `tp2` rows at `2026-06-23 16:21` are the pre-existing artifact from Prompt
3b-2 (near-tick fired at restart time); left untouched per spec.

---

## Idempotency proof — two restart counts must match

**Before second restart:** `count = 3`

**Catch-up log lines (second restart):**
```
2026-06-23 20:28:15,293 [INFO] app.engine: engine: warmup complete strategy=tv_test_harness bars=500 position=short bracket=active
2026-06-23 20:28:21,198 [INFO] app.redis_reader: redis_reader: loaded 2000 historical bars blofin BTC-USDT 1m
2026-06-23 20:28:21,219 [INFO] app.engine: engine: catch-up replaying 1348 1m bars strategy=tv_test_harness from t=1782165600000
2026-06-23 20:28:21,233 [INFO] app.engine: engine: catch-up exit strategy=tv_test_harness reason=trail at bar_t=1782167280000 price=64038.8000
2026-06-23 20:28:21,233 [INFO] app.engine: engine: entering live subscription strategy=tv_test_harness BTC-USDT 1h
```

**After second restart:** `count = 3`

Counts are **identical**. The `ON CONFLICT (strategy_id, signal, signal_bar_time, COALESCE(exit_reason, '')) DO NOTHING`
constraint in `shadow_store.py` ensures re-replay writes no duplicates.

---

## Changes made

`signal-engine/app/engine.py`:
- `bh` dict gains `"entry_bar_time": None` field.
- Warmup and live `_entry_loop` both set `bh["entry_bar_time"] = sig.signal_bar_time` when
  a bracket is created; clear it to `None` on close signals.
- After the warmup-complete log, a catch-up block fires if `bh["bracket"]` is active:
  reads up to 2000 1m bars, filters from `entry_bar_time + tf_ms`, feeds each bar's real
  `high`/`low` to `bracket.update()`, stores exits at the historical `bar["t"]`.
- Partial catch-up (entry predates the 1m stream window) is logged as a warning, not an error.
