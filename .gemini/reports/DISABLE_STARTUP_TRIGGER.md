# AI scheduler — disable immediate cycle on restart

## Request

Disable the AI strategy schedulers' "run immediately after a system restart" behavior. Each
of the 7 `AdaptiveScheduler` instances (one per enabled AI strategy) fired an immediate cycle
labeled `trigger_reason='startup'` the moment its loop task started — meaning every
`./scripts/redeploy.sh ai-signal-generator` (or any container restart) caused all 7 strategies
to evaluate simultaneously, regardless of where they were in their actual candle-close cycle.
Strategies should instead wait for their next natural (candle-close-aligned) trigger.

This is also the same "startup" burst used as the worst-case concurrency scenario during the
BNB scalper `load_markets()` investigation — removing it also reduces how often that many
strategies hit an exchange's API at the exact same instant.

## Change

`ai-signal-generator/app/scheduler.py::AdaptiveScheduler._loop()` — removed the two lines that
fired `await self._trigger_cycle('startup')` before entering the sleep loop. The loop now
starts directly with `_get_interval()` (candle-close-aligned) and sleeps until that time, same
as every subsequent cycle.

This function is the code path for both real process restarts (`start_all_schedulers`, called
from FastAPI's startup handler) and enabling a strategy via the dashboard
(`_reconcile_scheduler`'s `should_run and not running` branch in `main.py`) — both now behave
identically: no immediate fire, wait for the next aligned wake. An already-running scheduler
picking up a config change still fires immediately via `.interrupt()` → `trigger_reason=
'config_reload'`, which is a separate code path (`AdaptiveScheduler.interrupt()`) untouched by
this change — editing a strategy's config still applies right away.

No `'startup'` string references remain anywhere in `ai-signal-generator`, `dashboard-api`, or
`dashboard-ui` — clean removal, nothing else depended on that trigger_reason value.

## Verification

Redeployed `ai-signal-generator` (`./scripts/redeploy.sh ai-signal-generator`) and read the
live container logs from cold start:

```
2026-07-11 12:05:25 [INFO] app.scheduler: Started 7 scheduler(s): [...]
2026-07-11 12:05:44 [INFO] app.event_watcher: Started 7 event watcher(s)
...
2026-07-11 12:05:46 [INFO] app.scheduler: Scheduler strategy=hype-breakout-da2e sleeping 3263s until candle-close+buffer wake (54.4min)
2026-07-11 12:05:49 [INFO] app.scheduler: Scheduler strategy=ai-btc-6f8c sleeping 3261s until candle-close+buffer wake (54.3min)
2026-07-11 12:05:49 [INFO] app.scheduler: Scheduler strategy=eth-ai-34d2 sleeping 3261s until candle-close+buffer wake (54.3min)
2026-07-11 12:05:49 [INFO] app.scheduler: Scheduler strategy=tao-ai-range-rotation-d257 sleeping 6861s until candle-close+buffer wake (114.3min)
2026-07-11 12:05:49 [INFO] app.scheduler: Scheduler strategy=bnb-ai-scalper-edbb sleeping 3261s until candle-close+buffer wake (54.3min)
2026-07-11 12:05:49 [INFO] app.scheduler: Scheduler strategy=sol-ai-6486 sleeping 6861s until candle-close+buffer wake (114.3min)
2026-07-11 12:05:49 [INFO] app.scheduler: Scheduler strategy=xrp-ai-3844 sleeping 3261s until candle-close+buffer wake (54.3min)
```

No `"startup — triggering immediate cycle"` or `"Triggering cycle ... reason=startup"` lines —
every scheduler went straight from start to its next aligned sleep. Confirmed against
`ai_signal_log` directly:

```sql
SELECT strategy_id, triggered_at, trigger_reason
FROM ai_signal_log
WHERE triggered_at > '2026-07-11 12:04:52'::timestamptz  -- container start time
ORDER BY triggered_at DESC;
-- (0 rows)
```

Zero rows since the restart — no cycle fired. Each strategy will next run at its logged
candle-close-aligned time.
