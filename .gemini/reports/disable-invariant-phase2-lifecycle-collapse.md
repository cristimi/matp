# Phase 2 — Lifecycle Endpoints + `webhook_enabled` Collapse

**Date:** 2026-06-22  
**Files changed:** `order-listener/app/main.py`, `order-listener/app/webhook_handler.py`, `dashboard-api/src/routes/strategies.ts`, `dashboard-ui/src/pages/Strategies.tsx`  
**Status:** Complete — all containers running, all checks passed

---

## Changes

### 1. `order-listener/app/webhook_handler.py` — Remove `webhook_enabled` gate

Removed the 5-line block (lines 576–580) that rejected webhooks with 403 when `webhook_enabled=false`:

```diff
-    if not strategy['webhook_enabled']:
-        logger.warning(f"Rejected webhook: webhooks disabled strategy={strategy_id}")
-        await _log_webhook_call(pool, strategy_id, 403, "Webhooks disabled")
-        await _finalize_signal_log(pool, signal_log_id, 403, "auth_failed", "Webhooks disabled", start_ms)
-        raise HTTPException(status_code=403, detail="Webhooks disabled")
-
     # ── Symbol resolution ─────────────────────────────────────────────
```

`webhook_enabled` is now a no-op DB column. Webhooks route through `enabled` only.

### 2. `order-listener/app/main.py` — `POST /strategies/{strategy_id}/stop`

New endpoint:

```python
@app.post("/strategies/{strategy_id}/stop")
async def stop_strategy(strategy_id: str):
    """Flatten all open legs then disable the strategy. Disables only if all closes succeed."""
    from fastapi import HTTPException
    from app.webhook_handler import _flatten_strategy_positions
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, account_id, enabled FROM strategies WHERE id = $1",
            strategy_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")
    strategy = dict(row)
    results = await _flatten_strategy_positions(pool, strategy)
    errors = [r for r in results if not r.get("success")]
    if not errors:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE strategies SET enabled = false, updated_at = NOW() WHERE id = $1",
                strategy_id,
            )
    return {
        "stopped":     strategy_id,
        "enabled":     False if not errors else bool(row["enabled"]),
        "legs_closed": len(results) - len(errors),
        "errors":      errors,
    }
```

Disables only if all close legs succeed. Returns `{stopped, enabled, legs_closed, errors}`.

### 3. `dashboard-api/src/routes/strategies.ts`

**Added `LISTENER_URL` constant:**
```ts
const LISTENER_URL  = process.env.ORDER_LISTENER_URL || 'http://order-listener:8001';
```

**`POST /:id/stop` — now proxies to listener:**
```ts
router.post('/:id/stop', async (req, res) => {
  const listenerRes = await fetch(`${LISTENER_URL}/strategies/${req.params.id}/stop`, {
    method: 'POST',
  });
  const data = await listenerRes.json();
  if (!listenerRes.ok) return res.status(listenerRes.status).json(data);
  notifyReconcile(req.params.id);
  res.json(data);
});
```

**Removed:**
- `POST /:id/disable` — direct `enabled=false` DB update
- `POST /:id/enable` — direct `enabled=true` DB update
- `POST /:id/webhook-enabled` — direct `webhook_enabled` DB update

**`PUT /:id` — removed `enabled` and `webhook_enabled`:**
- Removed from body destructuring
- Removed `enabled = COALESCE($4, ...)` and `webhook_enabled = COALESCE($5, ...)` from SQL SET
- Renumbered all params ($6→$4, $7→$5, ... $16→$14; WHERE id=$12→$10)
- Removed `enabled, webhook_enabled` from RETURNING clause

### 4. `dashboard-ui/src/pages/Strategies.tsx`

**`confirmStop` simplified** — backend handles flattening; UI no longer closes positions individually:
```diff
-  const confirmStop = async (closePositions: boolean) => {
+  const confirmStop = async () => {
-    if (closePositions && ...) {
-      for (const pos of openPositions) {
-        await fetch(`.../positions/${pos.id}/close`, { method: 'POST' });
-      }
-    }
     const res = await fetch(`.../strategies/${stopTarget.id}/stop`, { method: 'POST' });
   ...
```

**Stop modal** — single-path with updated warning:
- Removed "Stop Without Closing" button
- Removed two-branch conditional (open positions vs none)
- Shows "⚠ N open position(s). Stopping will close them at market price." when applicable
- Single "Stop Strategy" button regardless of open-position count

---

## Deployment

```
docker compose build --no-cache order-listener
docker compose build dashboard-api dashboard-ui
docker compose up -d --force-recreate order-listener dashboard-api dashboard-ui
```

All three containers healthy:
```
matp-dashboard-api-1    Up (healthy)
matp-dashboard-ui-1     Up
matp-order-listener-1   Up (healthy)
```

---

## Verification

### V1a — Stop with no open positions
```
$ curl -sf -X POST http://localhost:8001/strategies/tv_test_harness/stop
{"stopped":"tv_test_harness","enabled":false,"legs_closed":0,"errors":[]}

$ SELECT enabled FROM strategies WHERE id='tv_test_harness';
 enabled
---------
 f
```
`enabled=false`, `legs_closed=0`. ✓

### V1b — Stop with 1 open position (flatten + disable)
Inserted BTC-USDT long position. Then:
```
$ curl -sf -X POST http://localhost:8001/strategies/tv_test_harness/stop
{"stopped":"tv_test_harness","enabled":false,"legs_closed":1,"errors":[]}

$ SELECT s.enabled, p.status, p.close_reason, p.pnl_realized
  FROM strategies s JOIN strategy_positions p ON p.strategy_id=s.id
  WHERE s.id='tv_test_harness';
 enabled | status |    close_reason    |   pnl_realized
---------+--------+--------------------+-----------------
 f       | closed | flatten_on_disable | -0.9864...
```
Position closed with `flatten_on_disable`, PnL booked, strategy disabled. ✓

### V2 — 404 for nonexistent strategy
```
$ curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8001/strategies/does-not-exist/stop
404
```
✓

### V3 — dashboard-api proxies to listener (end-to-end via nginx)
```
$ curl -sf -X POST http://localhost/api/dashboard/strategies/tv_test_harness/stop
{"stopped":"tv_test_harness","enabled":false,"legs_closed":0,"errors":[]}
```
Listener response shape flows through dashboard-api → nginx to caller. ✓

### V4 — `PUT /:id` still works after param renumbering
```
$ curl -sf -X PUT http://localhost/api/dashboard/strategies/tv_test_harness \
    -H "Content-Type: application/json" -d '{"max_daily_signals": 123}'

$ SELECT max_daily_signals FROM strategies WHERE id='tv_test_harness';
 max_daily_signals
-------------------
               123
```
Update persists correctly. ✓

### V5 — Removed endpoints return 404
```
$ curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost/api/dashboard/strategies/tv_test_harness/disable
404
$ curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost/api/dashboard/strategies/tv_test_harness/enable
404
$ curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost/api/dashboard/strategies/tv_test_harness/webhook-enabled
404
```
All removed. ✓

### V6 — Structure
```
$ docker compose exec order-listener grep -c "capital_allocation = capital_allocation +" /app/app/webhook_handler.py
1
$ docker compose exec order-listener grep -c "Webhooks disabled" /app/app/webhook_handler.py
0
$ docker compose exec -T dashboard-ui grep -rl "Stop Without Closing" /usr/share/nginx/html
(no output — text gone)
$ docker compose exec -T dashboard-ui grep -rl "Stopping will close them at market" /usr/share/nginx/html
/usr/share/nginx/html/assets/index-B-Z8KyPW.js
```
One allocation site, gate removed, new UI text confirmed. ✓
