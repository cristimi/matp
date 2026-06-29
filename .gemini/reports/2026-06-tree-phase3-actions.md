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

### Changes

- `StrategyTreePage`: extracted `loadStrategies` callback, passed as `onL1Refresh` to `StrategyCard`
- `StrategyCard`: added `handlePauseResume` (⏸ gated behind confirm, ▶ resumes directly)
- Pause confirm: "Pausing closes all open positions for this strategy. Continue?"
- In-flight: button shows `…`, disabled
- Error: shown inline in Row 2
- On pause: position cache reset + card collapsed + L1 refetched
- `PositionCard`: added `onClose` prop, `handleClosePosition` (confirm + POST /positions/:id/close)
- Both ✕ (header) and "Close position" (orders track) wired; disabled + error inline while in-flight

### API verification

Start → enabled=true, stop_reason=NULL:
```
{"started":"tv-btc-test-hl-94e1","enabled":true}
id: tv-btc-test-hl-94e1 | enabled: t | stop_reason: (NULL)
```

Stop → enabled=false, stop_reason='user', legs_closed=0:
```
{"stopped":"tv-btc-test-hl-94e1","enabled":false,"legs_closed":0,"errors":[]}
id: tv-btc-test-hl-94e1 | enabled: f | stop_reason: user
```

Position close (closed position → expected error):
```json
{"error": "Position is closed, not open"}
```

### Build

Asset: `index-C21O9u6x.js` (Phase 3B, superseded by 3C below)

---

## Phase 3C — edit + stop-reason chip variants + deploy

### Changes

**StopChip color variants** (repo CSS tokens only):
- `null/unknown` → gray: `var(--muted)` / `var(--bg3)` / `var(--border-hi)`
- `'user'` → gray (same, shows "user")
- `'drawdown'` → amber: `var(--failed-color)` / `var(--failed-color-a)` / `var(--failed-color-b)`
- `'error'` → red: `var(--red)` / `var(--red-a)` / `var(--red-b)`

**ⓘ detail panel**:
- Toggles on ⓘ click
- Lazily fetches `/strategies/:id` on first open
- Shows: interval, default leverage, margin/trade, max drawdown%, committed capital, last signal
- ✎ Edit strategy button navigates to `/strategies` with `state.editId`

**Edit auto-trigger in Strategies.tsx**:
- Reads `location.state.editId` on mount (via `useState` initializer)
- After strategies load, finds matching strategy and auto-calls `handleEdit(target)`
- Clears location state via `window.history.replaceState` to prevent re-trigger

### Full end-to-end verification (Phase 3C)

All services healthy:
```json
{"status":"ok","service":"dashboard-api"}
{"status":"ok","service":"order-listener"}
```

Strategy start+stop cycle:
```
Start:  {"started":"tv-btc-test-hl-94e1","enabled":true}
DB:     enabled=t | stop_reason=(NULL)
Stop:   {"stopped":"tv-btc-test-hl-94e1","enabled":false,"legs_closed":0,"errors":[]}
DB:     enabled=f | stop_reason=user
```

L1 tree (stop_reason live):
```
hype-breakout-da2e   enabled=True  stop_reason=None
tv_test_harness      enabled=True  stop_reason=None
tv-btc-test-hl-94e1  enabled=False stop_reason=user
hype-test-7db4       enabled=True  stop_reason=None
ai-btc-6f8c          enabled=True  stop_reason=None
```

Position close (closed position → graceful error):
```json
{"error": "Position is closed, not open"}
```

### Deploy

- Rebuilt `dashboard-ui` --no-cache (clean build)
- Rebuilt `dashboard-api` (Phase 3A)
- Rebuilt `order-listener` (Phase 3A)
- `order-generator` not deployed (no DB write in error path)

Live asset hash: **`index-BpB_3IYM.js`**

Amber chip tokens confirmed in live bundle:
```
11x failed-color-a  (amber bg)
11x failed-color-b  (amber border)
20x failed-color    (amber text)
```
