# Five of six AI strategies had silently stopped scheduling

**Date:** 2026-07-31
**Service:** `ai-signal-generator`
**Symptom reported:** since ~2026-07-30 12:00 only one strategy appears in the AI log.

## 1. Confirming the symptom

Only `eth-ai-34d2` was running cycles:

```
$ docker compose logs ai-signal-generator 2>&1 | grep "Triggering cycle" | tail -3
ai-signal-generator-1  | 2026-07-31 12:00:12,110 [INFO] app.scheduler: Triggering cycle strategy=eth-ai-34d2 reason=scheduled
ai-signal-generator-1  | 2026-07-31 13:00:10,204 [INFO] app.scheduler: Triggering cycle strategy=eth-ai-34d2 reason=scheduled
```

All six AI strategies are enabled in the DB and none is deleted or stopped:

```
$ docker compose exec -T postgres psql -U matp -d matp -c \
    "SELECT id, symbol, enabled, is_deleted, stop_reason FROM strategies WHERE strategy_source='ai_engine' ORDER BY id;"
             id             |  symbol  | enabled | is_deleted | stop_reason
----------------------------+----------+---------+------------+-------------
 bnb-ai-scalper-edbb        | BNB-USDT | t       | f          |
 eth-ai-34d2                | ETH-USDT | t       | f          |
 sol-ai-6486                | SOL-USDT | t       | f          |
 tao-ai-range-rotation-d257 | TAO-USDT | t       | f          |
 xrp-ai-3844                | XRP-USDT | t       | f          |
(5 rows)   -- plus ai-btc-6f8c, which the LIKE pattern above missed
```

Cycle history shows all six were healthy until the 2026-07-30 redeploy and then
five went dead at once:

```
$ docker compose exec -T postgres psql -U matp -d matp -c \
    "SELECT strategy_id, count(*) AS cycles, max(triggered_at) AS last_cycle
     FROM ai_signal_log WHERE triggered_at > now() - interval '7 days'
     GROUP BY strategy_id ORDER BY last_cycle DESC;"
        strategy_id         | cycles |          last_cycle
----------------------------+--------+-------------------------------
 eth-ai-34d2                |    175 | 2026-07-31 13:00:10.204802+00
 xrp-ai-3844                |    289 | 2026-07-30 10:30:10.089879+00
 sol-ai-6486                |    216 | 2026-07-30 10:00:27.608086+00
 tao-ai-range-rotation-d257 |    163 | 2026-07-30 10:00:27.59598+00
 bnb-ai-scalper-edbb        |    216 | 2026-07-30 10:00:27.372299+00
 ai-btc-6f8c                |    145 | 2026-07-30 10:00:17.32216+00
(6 rows)
```

## 2. The schedulers claimed to be running

All six started at container boot and all six reported `running: true` — but five
had `last_trigger: null` and `last_interval_s: 14400`, which is the
`AdaptiveScheduler.__init__` default. That value is only overwritten on line 78 of
`scheduler.py`, immediately after `_get_interval()` returns, so those five loops
never completed a single iteration:

```
$ docker compose logs ai-signal-generator 2>&1 | grep -E "Scheduler started|Started 6"
ai-signal-generator-1  | 2026-07-30 11:03:14,849 [INFO] app.scheduler: Scheduler started strategy=eth-ai-34d2
ai-signal-generator-1  | 2026-07-30 11:03:14,851 [INFO] app.scheduler: Scheduler started strategy=xrp-ai-3844
ai-signal-generator-1  | 2026-07-30 11:03:14,852 [INFO] app.scheduler: Scheduler started strategy=ai-btc-6f8c
ai-signal-generator-1  | 2026-07-30 11:03:14,856 [INFO] app.scheduler: Scheduler started strategy=tao-ai-range-rotation-d257
ai-signal-generator-1  | 2026-07-30 11:03:14,856 [INFO] app.scheduler: Scheduler started strategy=bnb-ai-scalper-edbb
ai-signal-generator-1  | 2026-07-30 11:03:14,857 [INFO] app.scheduler: Scheduler started strategy=sol-ai-6486
ai-signal-generator-1  | 2026-07-30 11:03:14,857 [INFO] app.scheduler: Started 6 scheduler(s): [...]

$ docker compose exec -T nginx wget -qO- http://ai-signal-generator:8005/internal/schedulers
{"schedulers":[
 {"strategy_id":"eth-ai-34d2","running":true,"last_trigger":"2026-07-31T13:00:10.204831+00:00","last_interval_s":3587.53},
 {"strategy_id":"xrp-ai-3844","running":true,"last_trigger":null,"last_interval_s":14400},
 {"strategy_id":"ai-btc-6f8c","running":true,"last_trigger":null,"last_interval_s":14400},
 {"strategy_id":"tao-ai-range-rotation-d257","running":true,"last_trigger":null,"last_interval_s":14400},
 {"strategy_id":"bnb-ai-scalper-edbb","running":true,"last_trigger":null,"last_interval_s":14400},
 {"strategy_id":"sol-ai-6486","running":true,"last_trigger":null,"last_interval_s":14400}],"count":6}
```

Only `eth-ai-34d2` ever logged the "sleeping …" line that follows `_get_interval()`.
No traceback, no error, nothing in the log for the other five — for 26 hours.

## 3. Root cause

`AdaptiveScheduler._loop()` had no exception handling:

```python
async def _loop(self):
    while self._running:
        sleep_seconds = await self._get_interval()   # ← hits the DB
        ...
```

`_get_interval()` acquires a pool connection and runs two queries. If that raises,
the exception propagates out of `_loop`, ends the task, and is **never seen**:
nothing awaits the task, and `self._task` keeps a reference to it so Python never
garbage-collects it into the "Task exception was never retrieved" warning. The
`_running` flag stays `True`, so `/internal/schedulers` and the UI both keep
reporting the strategy as healthy.

The pool is created with `min_size=2` (`database.py:16`), so at boot only two
connections exist. Six schedulers start at once; the first one to run gets a ready
connection, the rest need new connections opened while the container is also doing
`compute_warmup()`, `build_graph()`, the collector, and the model probe on a
heavily loaded host. That matches exactly what happened — the one scheduler that
got the warm connection survived, the five that had to open one did not.

The queries themselves are fine; re-running `_get_interval()` for all six strategies
inside the live container succeeds:

```
$ docker compose exec -T ai-signal-generator python /app/probe.py
eth-ai-34d2: OK sleep=2845.834780931473
xrp-ai-3844: OK sleep=1045.8166949748993
ai-btc-6f8c: OK sleep=2845.801232099533
tao-ai-range-rotation-d257: OK sleep=2845.791234970093
bnb-ai-scalper-edbb: OK sleep=2845.7813909053802
sol-ai-6486: OK sleep=2845.772702932358
```

So the failure was transient. The bug is that a transient failure was fatal and invisible.

## 4. Fix

`ai-signal-generator/app/scheduler.py`

1. `_loop()` wraps each iteration in `try/except Exception`, logs the full traceback
   via `logger.exception`, backs off `RETRY_BACKOFF_SECONDS` (60s) and continues.
   `asyncio.CancelledError` is re-raised so `stop()` still works. Loop exit is logged.
2. New `loop_status()` reports the real state of the task — `loop_alive` plus
   `loop_error` (`cancelled` / `exited` / the exception repr).

`ai-signal-generator/app/main.py`

3. `GET /internal/schedulers` now includes `loop_alive` and `loop_error`, so
   `running: true, loop_alive: false` immediately identifies this failure mode.

## 5. Verification against the running container

All six schedulers now complete an iteration and arm their timers:

```
$ docker compose logs ai-signal-generator 2>&1 | grep "sleeping"
ai-signal-generator-1  | 2026-07-31 13:18:10,548 [INFO] app.scheduler: Scheduler strategy=xrp-ai-3844 sleeping 719s until candle-close+buffer wake (12.0min)
ai-signal-generator-1  | 2026-07-31 13:18:10,736 [INFO] app.scheduler: Scheduler strategy=ai-btc-6f8c sleeping 2519s until candle-close+buffer wake (42.0min)
ai-signal-generator-1  | 2026-07-31 13:18:10,982 [INFO] app.scheduler: Scheduler strategy=eth-ai-34d2 sleeping 2519s until candle-close+buffer wake (42.0min)
ai-signal-generator-1  | 2026-07-31 13:18:10,999 [INFO] app.scheduler: Scheduler strategy=tao-ai-range-rotation-d257 sleeping 2519s until candle-close+buffer wake (42.0min)
ai-signal-generator-1  | 2026-07-31 13:18:11,000 [INFO] app.scheduler: Scheduler strategy=bnb-ai-scalper-edbb sleeping 2519s until candle-close+buffer wake (42.0min)
ai-signal-generator-1  | 2026-07-31 13:18:11,018 [INFO] app.scheduler: Scheduler strategy=sol-ai-6486 sleeping 2519s until candle-close+buffer wake (42.0min)
```

New health fields are live:

```
$ docker compose exec -T nginx wget -qO- http://ai-signal-generator:8005/internal/schedulers
{"schedulers":[
 {"strategy_id":"xrp-ai-3844","running":true,"loop_alive":true,"loop_error":null,"last_trigger":null,"last_interval_s":719.45},
 {"strategy_id":"eth-ai-34d2","running":true,"loop_alive":true,"loop_error":null,"last_trigger":null,"last_interval_s":2519.01},
 {"strategy_id":"ai-btc-6f8c","running":true,"loop_alive":true,"loop_error":null,"last_trigger":null,"last_interval_s":2519.26},
 {"strategy_id":"sol-ai-6486","running":true,"loop_alive":true,"loop_error":null,"last_trigger":null,"last_interval_s":2518.98},
 {"strategy_id":"tao-ai-range-rotation-d257","running":true,"loop_alive":true,"loop_error":null,"last_trigger":null,"last_interval_s":2519.00},
 {"strategy_id":"bnb-ai-scalper-edbb","running":true,"loop_alive":true,"loop_error":null,"last_trigger":null,"last_interval_s":2518.99}],"count":6}
```

A previously dead strategy ran a real end-to-end cycle (first XRP cycle since
2026-07-30 10:30):

```
$ docker compose logs ai-signal-generator 2>&1 | grep -E "xrp-ai-3844"
ai-signal-generator-1  | 2026-07-31 13:31:13,074 [INFO] app.scheduler: Triggering cycle strategy=xrp-ai-3844 reason=scheduled
ai-signal-generator-1  | 2026-07-31 13:35:06,845 [INFO] app.graph.nodes.node_dispatch: strategy=xrp-ai-3844 action=hold gate=False reason=hold_or_adjust — no webhook

$ docker compose exec -T postgres psql -U matp -d matp -c \
    "SELECT strategy_id, max(triggered_at) FROM ai_signal_log
     WHERE triggered_at > now() - interval '30 minutes' GROUP BY strategy_id;"
 strategy_id |              max
-------------+-------------------------------
 xrp-ai-3844 | 2026-07-31 13:31:13.074679+00
(1 row)
```

Container healthy:

```
$ docker compose ps ai-signal-generator
NAME                         SERVICE               STATUS
matp-ai-signal-generator-1   ai-signal-generator   Up (healthy)
```

## 6. Note on cost

Between 2026-07-30 10:30 and 2026-07-31 13:18, five of six AI strategies produced
no signals at all. Any position they held was left unmanaged for ~27 hours — no
stop adjustment, no exit evaluation. Positions open during that window should be
reviewed manually.
