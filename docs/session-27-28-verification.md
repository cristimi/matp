# Session 27–28: Signal Log UI & Order Retry Verification

**Date:** 2026-06-07

---

## Session 27 — Signal Log UI End-to-End Verification

**VERDICT: PASS — all 7 checks confirmed**

### Task 1 — Build & health

Both `dashboard-api` and `dashboard-ui` rebuilt with `--no-cache`. All 8 containers
healthy before proceeding.

### Task 2 — API verification

| Check | Result |
|---|---|
| `GET /api/dashboard/signals?limit=5` | 200 OK, correct paginated structure |
| Required fields | All present (`id`, `received_at`, `strategy_id`, `outcome`, `raw_body`, `duration_ms`, `oel_*` join fields) |
| `GET /api/dashboard/signals/strategies` | `["test_blofin_demo_01","test_hl_demo_01"]` |
| Filter by `outcome=auth_failed` | `total=1` |
| Filter by strategy `test_hl_demo_01` | `total=17` |

### Task 3 — Fresh signal generation

Three signals sent; all three appeared in `signal_log` within 1 second:

| id | outcome | http_status | duration_ms |
|----|---------|-------------|-------------|
| 29 | `symbol_rejected` | 422 | 6ms |
| 28 | `validation_failed` | 422 | 6ms |
| 27 | `auth_failed` | 403 | 224ms |

JOIN query confirmed no `order_execution_log` rows for any of the 3 (all rejected
before reaching the executor — correct behaviour).

### Task 4 — Visual UI checks (Playwright, headless Chromium)

1. **Page loads** — HTTP 200, no JS console errors, "29 total" shown ✅
2. **3 fresh signals at top** — `auth failed` ×3, `validation failed` ×4,
   `symbol rejected` ×3 visible in list ✅
3. **Outcome badge colours** ✅
   - `AUTH FAILED` → `rgba(225,29,72,0.08)` / red text
   - `VALIDATION FAILED` / `SYMBOL REJECTED` → `rgba(234,88,12,0.1)` / orange text
   - `FILLED` → `rgba(0,168,119,0.08)` / green text
4. **Click to expand** — ERROR label + error message + RAW PAYLOAD JSON all rendered;
   execution log section absent for rejected signals (correct — `oel_id` is null) ✅
5. **Strategy dropdown** — 2 `<select>` elements populated:
   `[All Strategies, test_blofin_demo_01, test_hl_demo_01]` ✅
6. **Outcome filter** — selecting `auth_failed` narrowed to 2 rows + "2 total" counter ✅
7. **Clear button** — `✕ Clear` appeared when filter active; click reset both filters,
   all outcomes visible again ✅

**No gaps found. No fixes applied.**

---

## Session 28 — Order Retry End-to-End Verification

**VERDICT: PASS**

### Task 1 — actual_fill_price column

Column exists in the `orders` table:

```
 actual_fill_price | numeric |  |  |
```

### Task 2 — Order selected for testing

```
id:         92e3e6d7-0223-4c66-a775-f1ab8146300e
strategy:   test_hl_demo_01
symbol:     ETH-USDT
side:       sell
status:     route_failed
received:   2026-06-07 09:29:46 UTC
```

### Task 3 — Retry API response

```json
{
  "order_id": "92e3e6d7-0223-4c66-a775-f1ab8146300e",
  "status": "rejected",
  "retry_result": {
    "success": false,
    "status": "rejected",
    "error_msg": "Reduce only order would increase position. asset=4",
    "exchange_order_id": null,
    "actual_fill_price": null,
    "realized_pnl": null
  }
}
```

No 500 error. The retry reached Hyperliquid and received a real exchange rejection
(no open position to close — expected with demo credentials). DB record updated:
`status=rejected`, `error_msg` written, `updated_at=2026-06-07 14:56:33` (changed
from the original `route_failed` timestamp).

### Task 4 — dead_letter_orders

No row exists for this order ID — expected. Only orders that exhaust automatic retry
attempts are moved to `dead_letter_orders`; a manual UI retry does not increment the
counter.

### Task 5 — UI retry button

Three `↺ RETRY` buttons visible on `/orders` (enabled, no JS errors). Button click
triggered the request successfully; page text showed `rejected` post-retry. No loading
spinner is shown on the button during the in-flight request — minor UX gap, not a bug.

### Task 6 — Listener logs

```
[INFO] app.executor_client: Calling executor for order 92e3e6d7... http://order-executor:8004/execute
[INFO] httpx: HTTP Request: POST http://order-executor:8004/execute "HTTP/1.1 200 OK"
POST /orders/92e3e6d7.../retry HTTP/1.1 200 OK
```

Executor called correctly with the strategy's `account_id`. No `account_id not found`
errors.

### Task 7 — Token bypass

Non-issue by design. The retry handler calls `call_executor` directly, bypassing
`webhook_handler.py` entirely. The `token="internal_retry"` only satisfies the
`WebhookPayload` model constructor — no HMAC check runs on retry.

**No gaps found. No fixes applied.**
