# Strategy Tree Phase 3 — Actions + stop_reason

## Phase 3A — stop_reason column + writes

### Migration 032 applied

```
BEGIN
ALTER TABLE
COMMIT
psql:/dev/stdin:26: NOTICE:  Migration 032 verified OK
DO
```

Column confirmed in schema:
```
 stop_reason          | character varying        |           |          |
```

### Error path investigation (order-generator/app/scheduler.py)

The `disable()` method (line 220) only sets `s.enabled = False` in-memory and removes
the APScheduler job. The `_run_strategy` error handler (line 130) only logs the exception.
Neither path writes to the database. The error path **never reaches the DB** — no write
was invented. Chip will fall back to generic gray "stopped" for scheduler-disabled strategies.

### Code writes confirmed

```
/order-listener/app/main.py:119        → stop_reason = 'user'
/order-listener/app/webhook_handler.py:116 → stop_reason = 'drawdown'
/dashboard-api/src/routes/strategies.ts:757 → stop_reason = NULL  (on /start)
/dashboard-api/src/routes/strategies.ts:380,442 → s.stop_reason selected + returned in tree
```

### Live sequence: user-stop sets 'user', start clears to NULL

**Start strategy** (strategy was already disabled):
```json
{"started": "tv-btc-test-hl-94e1", "enabled": true}
```

DB after start:
```
         id          | enabled | stop_reason
---------------------+---------+-------------
 tv-btc-test-hl-94e1 | t       |
(1 row)
```
`stop_reason` is NULL ✓

**Stop strategy** (0 open positions, no exchange calls):
```json
{"stopped": "tv-btc-test-hl-94e1", "enabled": false, "legs_closed": 0, "errors": []}
```

DB after stop:
```
         id          | enabled | stop_reason
---------------------+---------+-------------
 tv-btc-test-hl-94e1 | f       | user
(1 row)
```
`stop_reason = 'user'` ✓

### Tree endpoint returns real stop_reason

```
hype-breakout-da2e True None
tv_test_harness True None
tv-btc-test-hl-94e1 False user
hype-test-7db4 True None
ai-btc-6f8c True None
```

`stop_reason: "user"` live in L1 tree ✓

### Services redeployed

- `order-listener`: rebuilt + recreated (healthy)
- `dashboard-api`: rebuilt + recreated (healthy)
- `order-generator`: no DB write in error path — not redeployed

## Phase 3B — wire pause/resume + close

(To be completed)

## Phase 3C — edit + stop-reason chip variants + deploy

(To be completed)
