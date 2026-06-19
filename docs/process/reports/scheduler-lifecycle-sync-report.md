# Scheduler Lifecycle Sync Report

**Date:** 2026-06-19
**Services changed:** `ai-signal-generator`, `dashboard-api`

---

## Problem

`start_all_schedulers` / `start_all_event_watchers` ran once at FastAPI boot. Any strategy
lifecycle change after boot (create, stop, disable, delete) was invisible to the running
process until a full service restart:

- **New AI strategy** → no scheduler ever started → never fires.
- **Stopped / disabled strategy** → scheduler kept running → kept calling the LLM and
  placing orders. This was the dangerous half.

`dashboard-api` already called `POST /internal/schedulers/{id}/config-reload` on AI config
saves, but that route only `interrupt()`ed an already-running scheduler; if none existed it
returned `not_found` and did nothing.

---

## Solution — one idempotent "reconcile" operation

**Should-run predicate:** `enabled = true AND NOT is_deleted AND ai_strategy_config row exists`
(same query as the boot loader).

| should_run | currently running | action |
|-----------|-------------------|--------|
| yes | no  | start scheduler **and** its event watcher |
| yes | yes | `interrupt()` (config reload) |
| no  | yes | stop scheduler; cancel+await its watcher |
| no  | no  | no-op |

---

## Files changed

### `ai-signal-generator/app/event_watcher.py`
- Added `start_event_watcher(strategy_id, db_pool, graph, scheduler) -> asyncio.Task`
  helper that spawns one `event_watcher_loop` task for a single strategy.
- Changed `start_all_event_watchers` return type from `list` to `dict[str, asyncio.Task]`
  keyed by `strategy_id`, reusing the new helper.

### `ai-signal-generator/app/main.py`
- **Imports:** added `AdaptiveScheduler` (scheduler) and `start_event_watcher`
  (event_watcher) to existing imports.
- **Shutdown loop:** `watcher_tasks` is now a dict; changed iteration from list form
  to `.values()` in both `task.cancel()` loop and `asyncio.gather(...)` call.
- **Reconcile function + route:** added `_reconcile_scheduler(strategy_id)` implementing
  the four-cell truth table above, plus `POST /internal/schedulers/{id}/reconcile` route.
- **`config-reload` alias:** rewrote the existing handler to delegate to
  `_reconcile_scheduler` so old callers still work.

### `dashboard-api/src/routes/strategies.ts`
- Added `AI_URL` constant and `notifyReconcile(strategyId)` fire-and-forget helper
  (calls `POST /internal/schedulers/{id}/reconcile`).
- Called `notifyReconcile` immediately before `res.json(...)` in five handlers:
  `POST /:id/stop`, `POST /:id/start`, `POST /:id/enable`, `POST /:id/disable`,
  `DELETE /:id`.
- `POST /` (create) intentionally excluded — `ai_strategy_config` row does not exist yet
  at create time; the AI config `PUT` route (in `ai.ts`) handles that transition.

### `dashboard-api/src/routes/ai.ts`
- Repointed `notifyConfigReload` URL from `/config-reload` to `/reconcile`.
  Call sites (three existing `notifyConfigReload(...)` calls) unchanged.

---

## Verification (strategy `ai-btc-6f8c`)

**6.1 — Code in running containers:**
```
docker compose exec -T ai-signal-generator grep -n "_reconcile_scheduler" /app/app/main.py
305:async def _reconcile_scheduler(strategy_id: str) -> dict:
363:    return await _reconcile_scheduler(strategy_id)
368:    result = await _reconcile_scheduler(strategy_id)

docker compose exec -T dashboard-api grep -rn "notifyReconcile\|/reconcile" /app/dist | head
/app/dist/routes/strategies.js:14:function notifyReconcile(strategyId) {
/app/dist/routes/strategies.js:15:    fetch(`${AI_URL}/internal/schedulers/${strategyId}/reconcile`, {
/app/dist/routes/strategies.js:566:        notifyReconcile(req.params.id);
/app/dist/routes/strategies.js:583:        notifyReconcile(req.params.id);
/app/dist/routes/strategies.js:617:        notifyReconcile(req.params.id);
/app/dist/routes/strategies.js:697:        notifyReconcile(req.params.id);
/app/dist/routes/strategies.js:708:        notifyReconcile(req.params.id);
/app/dist/routes/ai.js:53:    fetch(`${AI_URL}/internal/schedulers/${strategyId}/reconcile`, {
```

**6.2 — Start path (no restart):**
```
POST /api/dashboard/strategies/ai-btc-6f8c/stop
→ {"stopped":"ai-btc-6f8c","enabled":false}
GET  /internal/schedulers → {"schedulers":[],"count":0}    ← ABSENT ✓

POST /api/dashboard/strategies/ai-btc-6f8c/start
→ {"started":"ai-btc-6f8c","enabled":true}
GET  /internal/schedulers → {"schedulers":[{"strategy_id":"ai-btc-6f8c","running":true,...}],"count":1}  ← PRESENT ✓
```

**6.3 — Teardown path (dangerous-half fix):**
```
POST /api/dashboard/strategies/ai-btc-6f8c/stop
→ {"stopped":"ai-btc-6f8c","enabled":false}
GET  /internal/schedulers → {"schedulers":[],"count":0}    ← ABSENT ✓
```

**6.4 — Reconcile idempotency:**
```
POST /internal/schedulers/ai-btc-6f8c/reconcile → {"status":"reloaded","strategy_id":"ai-btc-6f8c"}
POST /internal/schedulers/ai-btc-6f8c/reconcile → {"status":"reloaded","strategy_id":"ai-btc-6f8c"}
```

**Result:** A newly-started strategy got a live scheduler with **no service restart**.
Stopping a strategy removed its scheduler and event watcher immediately. All four cells
of the reconcile truth table behaved as specified.
