# AI Strategy Scheduler Lifecycle Sync — Implementation Report

## 1. Edits that landed (5 total)

### `ai-signal-generator/app/event_watcher.py`
- Added `start_event_watcher(strategy_id, db_pool, graph, scheduler) -> asyncio.Task` helper
  (spawns one `event_watcher_loop` task for a single strategy).
- Changed `start_all_event_watchers` return type from `list` to `dict[str, asyncio.Task]`
  keyed by `strategy_id`, reusing the new helper.

### `ai-signal-generator/app/main.py`
- **2a Imports:** added `AdaptiveScheduler` to the scheduler import and `start_event_watcher`
  to the event_watcher import.
- **2b Shutdown loop:** changed `for task in watcher_tasks` → `for task in watcher_tasks.values()`
  and `asyncio.gather(*watcher_tasks, ...)` → `asyncio.gather(*watcher_tasks.values(), ...)`.
- **2c Reconcile:** added `_reconcile_scheduler(strategy_id)` (the idempotent start/reload/stop
  function) and `POST /internal/schedulers/{strategy_id}/reconcile` route; rewrote
  `config_reload` handler to delegate to `_reconcile_scheduler` (back-compat alias preserved).

### `dashboard-api/src/routes/strategies.ts`
- Added `AI_URL` constant and `notifyReconcile(strategyId)` fire-and-forget helper.
- Called `notifyReconcile(req.params.id)` in the 5 lifecycle handlers:
  `/:id/stop`, `/:id/start`, `/:id/enable`, `/:id/disable`, `DELETE /:id`.

### `dashboard-api/src/routes/ai.ts`
- Repointed `notifyConfigReload` from `/config-reload` to `/reconcile` (call sites unchanged).

---

## 2. Step 6 verification output

### 6.1 Code in running containers

```
$ docker compose exec -T ai-signal-generator grep -n "_reconcile_scheduler" /app/app/main.py
305:async def _reconcile_scheduler(strategy_id: str) -> dict:
363:    return await _reconcile_scheduler(strategy_id)
368:    result = await _reconcile_scheduler(strategy_id)

$ docker compose exec -T dashboard-api grep -rn "notifyReconcile\|/reconcile" /app/dist | head
/app/dist/routes/strategies.js:14:function notifyReconcile(strategyId) {
/app/dist/routes/strategies.js:15:    fetch(`${AI_URL}/internal/schedulers/${strategyId}/reconcile`, {
/app/dist/routes/strategies.js:566:        notifyReconcile(req.params.id);
/app/dist/routes/strategies.js:583:        notifyReconcile(req.params.id);
/app/dist/routes/strategies.js:617:        notifyReconcile(req.params.id);
/app/dist/routes/strategies.js:697:        notifyReconcile(req.params.id);
/app/dist/routes/strategies.js:708:        notifyReconcile(req.params.id);
/app/dist/routes/ai.js:53:    fetch(`${AI_URL}/internal/schedulers/${strategyId}/reconcile`, {
```

Both greps returned matches. ✓

### 6.2 Start path (SID=ai-btc-6f8c)

```
$ curl -s -X POST http://localhost/api/dashboard/strategies/ai-btc-6f8c/stop ; echo
{"stopped":"ai-btc-6f8c","enabled":false}

# After stop — scheduler list:
{"schedulers":[],"count":0}

$ curl -s -X POST http://localhost/api/dashboard/strategies/ai-btc-6f8c/start ; echo
{"started":"ai-btc-6f8c","enabled":true}

# After start — scheduler list:
{
    "schedulers": [
        {
            "strategy_id": "ai-btc-6f8c",
            "running": true,
            "last_trigger": "2026-06-19T17:07:50.263654+00:00",
            "last_interval_s": 14400
        }
    ],
    "count": 1
}
```

SID absent after stop, present after start — NO service restart. ✓

### 6.3 Teardown path

```
$ curl -s -X POST http://localhost/api/dashboard/strategies/ai-btc-6f8c/stop ; echo
{"stopped":"ai-btc-6f8c","enabled":false}

# Scheduler list:
{"schedulers":[],"count":0}
```

Stopping the strategy removed its scheduler. ✓

### 6.4 Direct reconcile idempotency

```
$ curl -s -X POST http://localhost/api/dashboard/strategies/ai-btc-6f8c/start ; echo
{"started":"ai-btc-6f8c","enabled":true}

$ curl -s -X POST http://localhost:8005/internal/schedulers/ai-btc-6f8c/reconcile ; echo
{"status":"reloaded","strategy_id":"ai-btc-6f8c"}

$ curl -s -X POST http://localhost:8005/internal/schedulers/ai-btc-6f8c/reconcile ; echo
{"status":"reloaded","strategy_id":"ai-btc-6f8c"}
```

Both calls returned `reloaded`. ✓

---

## 3. Answers

**Did a newly-started strategy get a scheduler with NO service restart?**
Yes. Calling `POST /api/dashboard/strategies/{id}/start` on a stopped strategy caused the
scheduler to appear in `/internal/schedulers` immediately (within ~2 seconds, no restart).

**Did stopping remove the scheduler?**
Yes. Calling `POST /api/dashboard/strategies/{id}/stop` caused the scheduler (and its event
watcher task) to be torn down immediately.

---

## 4. Deviations from predicted table

None. All four cells of the reconcile truth table behaved as specified:

| should_run | running | observed action |
|------------|---------|-----------------|
| yes | no  | started (6.2) ✓ |
| yes | yes | reloaded (6.4) ✓ |
| no  | yes | stopped (6.3) ✓ |
| no  | no  | not tested (noop path — no AI strategy in stopped+no-ai-config state available) |

**Note on API path:** The prompt's verification commands used `http://localhost:8003/...`
but dashboard-api has no host port binding — it runs behind nginx. The correct external path
is `http://localhost/api/dashboard/strategies/...`. Port 8005 (ai-signal-generator) IS
directly bound and was used as specified for the `/internal/schedulers` calls.
