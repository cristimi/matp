# HYPE-USDT Orders Failing — "Webhooks Disabled" Investigation

**Date:** 2026-06-21  
**Strategy:** `hype-test-7db4` ("HYPE Test", symbol `HYPE-USDT`)  
**Symptom:** Two webhook calls rejected with HTTP 403 "Webhooks disabled"

---

## 1. Current DB State

```
SELECT id, name, symbol, enabled, webhook_enabled, capital_allocation, allocation_peak, max_drawdown_pct, updated_at
FROM strategies WHERE id = 'hype-test-7db4';

       id       |   name    |  symbol   | enabled | webhook_enabled | capital_allocation | allocation_peak | max_drawdown_pct |          updated_at
----------------+-----------+-----------+---------+-----------------+--------------------+-----------------+------------------+-------------------------------
 hype-test-7db4 | HYPE Test | HYPE-USDT | t       | f               |                200 |             200 |               75 | 2026-06-21 13:37:04.349615+00
```

`enabled = true` but `webhook_enabled = false`. This is the direct cause of every rejection.

---

## 2. Full Event Timeline

From `strategy_webhook_calls` and `signal_log`:

| Time (UTC)              | Event                            | Detail |
|-------------------------|----------------------------------|--------|
| 2026-06-19 10:05:59     | `guard_rejected`                 | Leverage 20x > max 10x |
| 2026-06-19 10:06:42     | `route_failed` (200)             | Order couldn't match — testnet liquidity |
| 2026-06-19 13:26:22     | `route_failed` (200)             | Same |
| 2026-06-19 13:37:15     | ✅ `filled` (200)                | **Last successful webhook** (`webhook_enabled = true`) |
| 2026-06-20 11:08:05     | `validation_failed` (422)        | Missing `timestamp` field in payload |
| 2026-06-20 11:08:22     | `symbol_rejected` (422)          | BTC-USDT signal sent to HYPE-USDT strategy |
| 2026-06-20 11:08:43     | **`drawdown_stop`** (429)        | Allocation $70 ≤ floor $75 (75% below peak $300). `enabled` set to `false` |
| *some point after above*| **`webhook_enabled` → false**    | Mechanism unknown (see §4) |
| 2026-06-21 13:35:36     | ❌ `auth_failed` (403)           | "Webhooks disabled" |
| 2026-06-21 13:37:04     | **Strategy PUT** (updated_at)    | Someone edited the strategy (re-enabled it, reset allocation) — but did NOT fix `webhook_enabled` |
| 2026-06-21 13:37:14     | ❌ `auth_failed` (403)           | "Webhooks disabled" |

---

## 3. Root Cause — Two-Part Problem

### Part A: The gate code
`order-listener/app/webhook_handler.py:533–537`:

```python
if not strategy['webhook_enabled']:
    logger.warning(f"Rejected webhook: webhooks disabled strategy={strategy_id}")
    await _log_webhook_call(pool, strategy_id, 403, "Webhooks disabled")
    await _finalize_signal_log(pool, signal_log_id, 403, "auth_failed", "Webhooks disabled", start_ms)
    raise HTTPException(status_code=403, detail="Webhooks disabled")
```

This check fires when `strategies.webhook_enabled = false` in the database.

### Part B: No UI to toggle it
- `dashboard-api` exposes `POST /strategies/:id/webhook-enabled` (line 787 of `routes/strategies.ts`) which can flip the flag.
- The dashboard UI (`Strategies.tsx`) **does not call this endpoint anywhere** — confirmed by grep showing zero hits for `webhook-enabled` or `webhookEnabled` in the UI source.
- The edit form (`handleEdit`, lines 968–983) does not include `webhook_enabled` in the form state, and `handleEditSubmit` (lines 1083–1091) spreads `editForm` into the PUT body — so the edit form cannot accidentally set or clear the flag either.
- **Result:** once `webhook_enabled` becomes `false`, there is no way to fix it through the dashboard.

---

## 4. How `webhook_enabled` Became False — Most Likely Cause

**What we know:**
- Strategy was created on 2026-06-19 with `webhook_enabled = true` (hardcoded in the INSERT at `strategies.ts:289`)
- Webhooks worked on 2026-06-19 (3 calls recorded, last success at 13:37:15)
- On 2026-06-20 the drawdown stop fired — but that only sets `enabled = false`, NOT `webhook_enabled`
- The two failures on 2026-06-21 both return "Webhooks disabled", meaning `webhook_enabled` was already `false` by then

**The gap:** between 2026-06-19 13:37 and 2026-06-21 13:35, something set `webhook_enabled = false`.

**Candidate mechanisms:**
1. **Direct API call** — `POST /api/dashboard/strategies/hype-test-7db4/webhook-enabled` with `{enabled: false}`. This is the only code path that sets the field to false. It does NOT update `updated_at`, which is consistent with `updated_at` being 2026-06-21 13:37:04 (from a separate PUT).
2. **Direct SQL** — manual `UPDATE` in psql.
3. **Strategy re-creation** — the strategy was deleted and re-inserted with `webhook_enabled = false`; ruled out because the `id` is the same and `strategy_webhook_calls` history is intact.

Most likely: a direct API call or SQL during post-drawdown recovery attempts.

---

## 5. What Needs to Happen to Fix the Strategy

1. Confirm you want to re-enable webhooks on `hype-test-7db4`.
2. Fix it via direct API call (the endpoint is already wired up, just not exposed in the UI):
   ```bash
   curl -X POST http://localhost:8003/strategies/hype-test-7db4/webhook-enabled \
        -H "Content-Type: application/json" \
        -d '{"enabled": true}'
   ```
   Or via SQL:
   ```sql
   UPDATE strategies SET webhook_enabled = true, updated_at = NOW()
   WHERE id = 'hype-test-7db4';
   ```
3. Verify: `SELECT id, webhook_enabled FROM strategies WHERE id = 'hype-test-7db4';` should show `t`.

---

## 6. Product Gap — Missing UI Control

The `webhook_enabled` flag is a first-class column used as a hard gate in order-listener, but it has no UI toggle. The API endpoint exists (`POST /:id/webhook-enabled`) but was never wired to the dashboard.

**Affected strategies at risk:** any strategy could land in `webhook_enabled = false` state with no in-app recovery path.

**Recommended fix:** add a toggle to the strategy edit panel or strategy card actions in `Strategies.tsx` that calls `POST /:id/webhook-enabled`. This is a UI-only change.

---

## 7. Supporting Data

```
SELECT strategy_id, http_status, error_message, received_at
FROM strategy_webhook_calls WHERE strategy_id = 'hype-test-7db4' ORDER BY received_at;

  strategy_id   | http_status |   error_message   |          received_at
----------------+-------------+-------------------+-------------------------------
 hype-test-7db4 |         200 |                   | 2026-06-19 10:06:43.295173+00
 hype-test-7db4 |         200 |                   | 2026-06-19 13:26:23.711157+00
 hype-test-7db4 |         200 |                   | 2026-06-19 13:37:16.496606+00
 hype-test-7db4 |         403 | Webhooks disabled | 2026-06-21 13:35:36.624541+00
 hype-test-7db4 |         403 | Webhooks disabled | 2026-06-21 13:37:15.015128+00
```
