# Candle-Aligned Scheduling & Closed-Candle-Only Analysis — Phase 1 Report

**Date:** 2026-07-02
**Service changed:** `ai-signal-generator` only (no `order-executor`/`order-listener` changes)

---

## Migration 037: `candle_close_buffer_seconds`

`db/migrations/037_candle_close_buffer.sql` — next free number confirmed via `ls db/migrations`
(highest existing was `036_geometric_range_template.sql`).

```sql
-- Migration 037: add candle_close_buffer_seconds to ai_strategy_config.
-- Number of seconds past a candle-close wall-clock boundary the scheduler waits
-- before waking, to give the exchange time to finalize the candle.
-- Default 150s (2.5min). Bounded [0, 600]: 0 means "wake exactly at the boundary"
-- (no safety margin, allowed but not recommended); 600s (10min) is a generous
-- upper bound — beyond that the buffer would eat a meaningful fraction of even
-- the shortest supported interval (1m/5m polling).

BEGIN;

ALTER TABLE public.ai_strategy_config
    ADD COLUMN IF NOT EXISTS candle_close_buffer_seconds integer DEFAULT 150 NOT NULL;

ALTER TABLE public.ai_strategy_config
    ADD CONSTRAINT ai_strategy_config_candle_close_buffer_chk
        CHECK (candle_close_buffer_seconds >= 0 AND candle_close_buffer_seconds <= 600);

COMMIT;

-- Self-verification
DO $$
DECLARE
    col_exists boolean;
    col_default text;
    chk_exists boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'ai_strategy_config'
          AND column_name  = 'candle_close_buffer_seconds'
    ) INTO col_exists;

    IF NOT col_exists THEN
        RAISE EXCEPTION 'Migration 037 FAILED: candle_close_buffer_seconds column not found in ai_strategy_config';
    END IF;

    SELECT column_default
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name   = 'ai_strategy_config'
      AND column_name  = 'candle_close_buffer_seconds'
    INTO col_default;

    IF col_default IS NULL OR col_default NOT LIKE '%150%' THEN
        RAISE EXCEPTION 'Migration 037 FAILED: candle_close_buffer_seconds default is not 150 (got: %)', col_default;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ai_strategy_config_candle_close_buffer_chk'
    ) INTO chk_exists;

    IF NOT chk_exists THEN
        RAISE EXCEPTION 'Migration 037 FAILED: bound check constraint missing';
    END IF;

    RAISE NOTICE 'Migration 037 verified OK: candle_close_buffer_seconds column present, default=150, bound check [0,600] present';
END $$;
```

### Applied to live DB — pasted output

```
$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/037_candle_close_buffer.sql
BEGIN
ALTER TABLE
ALTER TABLE
COMMIT
DO
NOTICE:  Migration 037 verified OK: candle_close_buffer_seconds column present, default=150, bound check [0,600] present
```

### Live column confirmation

```
$ docker compose exec -T postgres psql -U matp -d matp -c \
  "SELECT strategy_id, interval_no_position, interval_position_open, interval_at_risk, candle_close_buffer_seconds FROM ai_strategy_config;"
    strategy_id     | interval_no_position | interval_position_open | interval_at_risk | candle_close_buffer_seconds
--------------------+----------------------+------------------------+-------------------+-----------------------------
 hype-breakout-da2e | 2h                   | 15m                    | 5m                |                         150
 ai-btc-6f8c        | 4h                   | 15m                    | 5m                |                         150
(2 rows)
```

`db/init.sql` was **not** regenerated in this report — will do that alongside Phase 2's migration
regen (or now, on request) since both phases touch `ai_strategy_config`.

---

## Alignment function

New file `ai-signal-generator/app/scheduling.py`:

```python
"""
Candle-close-aligned scheduling helpers.

Boundaries are UTC-epoch aligned per standard exchange convention: epoch 0
(1970-01-01T00:00:00 UTC) falls on a boundary for every timeframe, so e.g. 4h
candles close at 00:00/04:00/08:00/... UTC, 1h at every hour, 15m at
:00/:15/:30/:45, etc. Aligning to `now.timestamp() // tf_seconds` reproduces
exactly this convention with no special-casing per unit.
"""
from datetime import datetime

_UNIT_SECONDS = {'m': 60, 'h': 3600, 'd': 86400}


def parse_interval_seconds(interval_str: str) -> int:
    """Converts '4h', '15m', '1d', '10m' etc. to seconds.

    Deliberately generic rather than restricted to ccxt's candle-timeframe
    whitelist: the scheduler's polling-cadence strings (e.g. '10m' for
    interval_position_open) can be values ccxt doesn't support as an actual
    candle timeframe — see node_ingest.py's cycle_interval/timeframe conflation.
    """
    unit  = interval_str[-1]
    value = int(interval_str[:-1])
    mult = _UNIT_SECONDS.get(unit)
    if mult is None:
        raise ValueError(f"Unsupported interval unit in {interval_str!r}")
    return value * mult


def seconds_until_aligned_wake(
    timeframe: str,
    now: datetime,
    buffer_seconds: int,
    min_sleep_seconds: float = 5.0,
) -> float:
    """
    Seconds to sleep from `now` until `buffer_seconds` past the next candle-close
    boundary for `timeframe`.

    The most recently closed candle's buffer point is `last_boundary +
    buffer_seconds`. If that point is still ahead of `now`, wake there — this is
    what makes a scheduler that starts (or resumes) partway through a candle wake
    shortly after the *current* candle's close instead of jumping a full period
    ahead. If that point has already passed (steady-state operation: we just
    consumed it, or resumed after it), wake at the next candle's buffer point
    instead.

    Always returns at least `min_sleep_seconds`, so a `now` that lands exactly on
    (or a hair past) a buffer point wakes almost immediately rather than sleeping
    for a full extra period or a literal 0s.
    """
    tf_sec = parse_interval_seconds(timeframe)
    now_epoch = now.timestamp()
    last_boundary = (now_epoch // tf_sec) * tf_sec
    wake_at = last_boundary + buffer_seconds
    if wake_at < now_epoch:
        wake_at += tf_sec
    return max(min_sleep_seconds, wake_at - now_epoch)
```

**Design note on the edge case** — a naive "always jump to the next boundary" formula
(`next_boundary = (floor(now/tf)+1)*tf`) is wrong: if the scheduler starts 2s after a close, it
would compute a wake almost a full period away instead of ~148s. The fix used here computes the
*most recent* boundary's buffer point first, and only pushes a full period forward if that point
has already passed relative to `now`. `test_naive_next_boundary_would_be_wrong_regression` in the
test file guards this regression directly.

## Scheduler wiring (`app/scheduler.py` diff)

```diff
--- a/ai-signal-generator/app/scheduler.py
+++ b/ai-signal-generator/app/scheduler.py
@@ -6,6 +6,7 @@ import httpx
 
 from app.config import settings
 from app.database import resolve_exchange_id
+from app.scheduling import seconds_until_aligned_wake
 
 logger = logging.getLogger(__name__)
 
@@ -75,13 +76,13 @@ class AdaptiveScheduler:
         await self._trigger_cycle('startup')
 
         while self._running:
-            interval = await self._get_interval()
-            self._last_interval = interval
+            sleep_seconds = await self._get_interval()
+            self._last_interval = sleep_seconds
             logger.info(
-                "Scheduler strategy=%s interval=%ds (%.1fh)",
-                self.strategy_id, interval, interval / 3600,
+                "Scheduler strategy=%s sleeping %.0fs until candle-close+buffer wake (%.1fmin)",
+                self.strategy_id, sleep_seconds, sleep_seconds / 60,
             )
-            interrupted = await self._sleep(interval)
+            interrupted = await self._sleep(sleep_seconds)
             if not self._running:
                 break
             if interrupted:
@@ -93,26 +94,26 @@ class AdaptiveScheduler:
             else:
                 await self._trigger_cycle('scheduled')
 
-    async def _get_interval(self) -> int:
+    async def _get_interval(self) -> float:
+        """Seconds to sleep until buffer_seconds past the next close of whichever
+        candle timeframe applies to the current state (no-position / position-open
+        / at-risk), per seconds_until_aligned_wake()."""
         config = await self._load_config()
         if not config:
             return 4 * 60 * 60
 
         position = await self._get_open_position()
         if not position:
-            return self._parse_interval(config['interval_no_position'])
-
-        unrealized_pct = abs(float(position.get('pnl_unrealized_pct') or 0))
-        if unrealized_pct >= float(config['at_risk_threshold_pct']):
-            return self._parse_interval(config['interval_at_risk'])
-
-        return self._parse_interval(config['interval_position_open'])
+            label = config['interval_no_position']
+        else:
+            unrealized_pct = abs(float(position.get('pnl_unrealized_pct') or 0))
+            if unrealized_pct >= float(config['at_risk_threshold_pct']):
+                label = config['interval_at_risk']
+            else:
+                label = config['interval_position_open']
 
-    def _parse_interval(self, interval_str: str) -> int:
-        """Converts '4h', '15m', '1d', '5m' etc. to seconds."""
-        unit  = interval_str[-1]
-        value = int(interval_str[:-1])
-        return value * {'m': 60, 'h': 3600, 'd': 86400}.get(unit, 3600)
+        buffer_seconds = int(config.get('candle_close_buffer_seconds', 150))
+        return seconds_until_aligned_wake(label, datetime.now(timezone.utc), buffer_seconds)
```

- The `interrupt()` / `_wakeup` event mechanism is untouched — `_sleep()` still races the computed
  duration against `_wakeup`, so a mid-sleep config change still triggers an immediate reload exactly
  as before.
- Startup behavior unchanged: `_loop()` still fires the unconditional `'startup'` cycle before ever
  calling `_get_interval()`.
- `_get_interval_label()` (used to set `cycle_interval` / OHLCV timeframe in `_build_initial_state`)
  is untouched, per the existing scheduler-cadence/candle-timeframe conflation noted in the task —
  this alignment work only changes *when* the loop wakes, not what timeframe is fetched.

---

## Unit tests

New file `ai-signal-generator/tests/test_scheduling.py` — 12 tests covering: unit parsing (including
non-ccxt `'10m'`), just-after/just-before/exactly-on boundary for multiple timeframes (1h, 4h, 15m,
5m, 1m), the exactly-on-buffer-point and zero-buffer floor cases, the naive-implementation regression
guard, and a sweep asserting sleep is never negative across a full period.

### Full pasted test run (inside container: Python 3.12.13, pytest 9.1.1)

```
$ docker compose exec -T ai-signal-generator python -m pytest /tmp/tests/ -v
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0 -- /usr/local/bin/python
cachedir: .pytest_cache
rootdir: /tmp/tests
plugins: anyio-4.14.0, langsmith-0.9.1
collecting ... collected 26 items

../tmp/tests/test_geometry.py::test_horizontal_channel PASSED            [  3%]
../tmp/tests/test_geometry.py::test_ascending_channel PASSED             [  7%]
../tmp/tests/test_geometry.py::test_descending_channel PASSED            [ 11%]
../tmp/tests/test_geometry.py::test_ascending_triangle PASSED            [ 15%]
../tmp/tests/test_geometry.py::test_descending_triangle PASSED           [ 19%]
../tmp/tests/test_geometry.py::test_rising_wedge PASSED                  [ 23%]
../tmp/tests/test_geometry.py::test_falling_wedge PASSED                 [ 26%]
../tmp/tests/test_geometry.py::test_no_pattern_diverging PASSED          [ 30%]
../tmp/tests/test_geometry.py::test_position_in_range PASSED             [ 34%]
../tmp/tests/test_geometry.py::test_too_few_candles PASSED               [ 38%]
../tmp/tests/test_geometry.py::test_insufficient_swings PASSED           [ 42%]
../tmp/tests/test_geometry.py::test_empty_candles PASSED                 [ 46%]
../tmp/tests/test_geometry.py::test_output_keys_present PASSED           [ 50%]
../tmp/tests/test_geometry.py::test_fit_quality_values PASSED            [ 53%]
../tmp/tests/test_scheduling.py::test_parse_interval_seconds_units PASSED [ 57%]
../tmp/tests/test_scheduling.py::test_parse_interval_seconds_invalid_unit PASSED [ 61%]
../tmp/tests/test_scheduling.py::test_1h_just_after_boundary PASSED      [ 65%]
../tmp/tests/test_scheduling.py::test_4h_just_after_boundary PASSED      [ 69%]
../tmp/tests/test_scheduling.py::test_1h_just_before_boundary PASSED     [ 73%]
../tmp/tests/test_scheduling.py::test_15m_just_before_boundary PASSED    [ 76%]
../tmp/tests/test_scheduling.py::test_1h_exactly_on_boundary PASSED      [ 80%]
../tmp/tests/test_scheduling.py::test_5m_exactly_on_boundary PASSED      [ 84%]
../tmp/tests/test_scheduling.py::test_wake_exactly_on_buffer_point_floors_to_min_sleep PASSED [ 88%]
../tmp/tests/test_scheduling.py::test_zero_buffer_exactly_on_boundary_floors_to_min_sleep PASSED [ 92%]
../tmp/tests/test_scheduling.py::test_naive_next_boundary_would_be_wrong_regression PASSED [ 96%]
../tmp/tests/test_scheduling.py::test_sleep_never_negative_across_a_full_period PASSED [100%]

============================== 26 passed in 1.59s ==============================
```

(Full geometry suite included to confirm no regression from the `scheduler.py` import change.)

---

## Worked example against a real wall-clock timestamp

Deployed via `./scripts/redeploy.sh ai-signal-generator` (output below), then observed the live
scheduler's first post-startup sleep computation for both configured strategies.

### `hype-breakout-da2e` — `interval_no_position = '2h'`, buffer = 150s, no open position

Live log line:
```
ai-signal-generator-1  | 2026-07-02 05:01:23,349 [INFO] app.scheduler: Scheduler strategy=hype-breakout-da2e sleeping 3667s until candle-close+buffer wake (61.1min)
```

By hand: container clock is UTC (confirmed: `docker compose exec ai-signal-generator date -u` matches
host UTC, no `TZ` override). `now = 2026-07-02T05:01:23.349Z`. 2h boundaries fall on even UTC hours:
00:00, 02:00, 04:00, 06:00, .... `last_boundary = 04:00:00`. `target = 04:00:00 + 150s = 04:02:30`,
which is **before** `now` → push to next boundary: `06:00:00 + 150s = 06:02:30`.
`sleep = 06:02:30.000 − 05:01:23.349 = 3666.651s`, which rounds (`%.0f`) to **3667s** — matches the
logged value exactly.

### `ai-btc-6f8c` — `interval_no_position = '4h'`, buffer = 150s, no open position

Live log line:
```
ai-signal-generator-1  | 2026-07-02 05:02:21,182 [INFO] app.scheduler: Scheduler strategy=ai-btc-6f8c sleeping 10809s until candle-close+buffer wake (180.1min)
```

By hand: `now = 2026-07-02T05:02:21.182Z`. 4h boundaries: 00:00, 04:00, 08:00, .... `last_boundary =
04:00:00`. `target = 04:00:00 + 150s = 04:02:30`, before `now` → push to next: `08:00:00 + 150s =
08:02:30`. `sleep = 08:02:30.000 − 05:02:21.182 = 10808.818s`, rounds to **10809s** — matches.

---

## Deploy verification

```
$ ./scripts/redeploy.sh ai-signal-generator
...
 Image matp-ai-signal-generator Built
▶ Recreating ai-signal-generator …
 Container matp-ai-signal-generator-1 Recreated
 Container matp-ai-signal-generator-1 Starting
 Container matp-ai-signal-generator-1 Started
▶ Verifying …
NAME                         IMAGE                      COMMAND                  SERVICE               STATUS
matp-ai-signal-generator-1   matp-ai-signal-generator   "uvicorn app.main:ap…"   ai-signal-generator   Up (health: starting)
✓ ai-signal-generator redeployed.

$ docker compose exec -T ai-signal-generator find /app -name "scheduling.py"
/app/app/scheduling.py

$ docker compose exec -T ai-signal-generator python -c "from app.scheduling import seconds_until_aligned_wake; print('import OK')"
import OK

$ docker compose exec -T ai-signal-generator curl -sf http://localhost:8005/health
{"status":"ok","service":"ai-signal-generator"}
```

Container state after deploy: `Up ... (health: starting)` → confirmed healthy shortly after via the
`/health` curl above (the FastAPI health check itself is not gated on the model probe; `docker compose
ps` health status lags a few seconds behind actual readiness because of the container's healthcheck
interval — the direct `curl` confirms the app is serving).

---

## Files changed

- `db/migrations/037_candle_close_buffer.sql` — new
- `ai-signal-generator/app/scheduling.py` — new (pure alignment function)
- `ai-signal-generator/tests/test_scheduling.py` — new (12 tests)
- `ai-signal-generator/app/scheduler.py` — wired `_get_interval()` to `seconds_until_aligned_wake()`,
  removed the now-redundant `_parse_interval()` (dead code once `_get_interval()` no longer needs a
  flat parse)

---

## Status

Phase 1 complete and deployed to `main`'s live stack. Awaiting confirmation before starting Phase 2
(closed-candle-only OHLCV filtering).
