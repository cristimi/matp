# Signal-Engine Port to Main — Verification Report
**Date:** 2026-06-23

## Step 4 — Safety Gate (`git status --short`)

Only the expected files were staged. None of the protected files appeared.

```
A  .gemini/reports/2026-06-21_branch_split_fix.md
A  db/migrations/024_shadow_signals.sql
 M docker-compose.yml
A  docs/process/reports/PROMPT_02_signal_engine.md
A  signal-engine/Dockerfile
A  signal-engine/app/__init__.py
A  signal-engine/app/config.py
A  signal-engine/app/database.py
A  signal-engine/app/diff.py
A  signal-engine/app/engine.py
A  signal-engine/app/indicators.py
A  signal-engine/app/main.py
A  signal-engine/app/redis_reader.py
A  signal-engine/app/shadow_store.py
A  signal-engine/app/strategies/__init__.py
A  signal-engine/app/strategies/base.py
A  signal-engine/app/strategies/test_harness.py
A  signal-engine/requirements.txt
```

Protected files checked — none present: `order-listener/app/webhook_handler.py`,
`order-listener/app/reconciler.py`, `order-listener/app/main.py`, `db/init.sql`,
`dashboard-api/src/routes/strategies.ts`, `dashboard-ui/src/pages/Strategies.tsx`, `CLAUDE.md`.

## Step 5 — DB State (read-only)

```sql
SELECT to_regclass('public.shadow_signals') AS shadow_signals_table;
SELECT is_nullable FROM information_schema.columns
  WHERE table_schema='public' AND table_name='strategy_positions' AND column_name='pnl_realized';
```

Output:
```
 shadow_signals_table
----------------------
 shadow_signals
(1 row)

 is_nullable
-------------
 YES
(1 row)
```

Migration 024 already applied (`shadow_signals` table present). Migration 026 already applied
(`pnl_realized` nullable). No migrations run. Next migration number: **027**.

## Step 7 — Deploy and Verify

### `docker compose ps signal-engine`
```
NAME                   IMAGE                COMMAND                SERVICE         CREATED          STATUS          PORTS
matp-signal-engine-1   matp-signal-engine   "python -m app.main"   signal-engine   25 seconds ago   Up 10 seconds
```

### `docker compose logs --tail=40 signal-engine`
```
signal-engine-1  | 2026-06-23 05:12:36,887 [INFO] __main__: signal-engine starting
signal-engine-1  | 2026-06-23 05:12:37,901 [INFO] app.database: Database pool initialized.
```

Engine started cleanly, connected to DB pool. No exchange calls, no host port exposed.

### `docker compose exec signal-engine ls app/strategies/test_harness.py`
```
app/strategies/test_harness.py
```

Engine code is inside the container.

### `docker compose exec order-listener grep -c "_flatten_strategy_positions" app/webhook_handler.py`
```
2
```

Count is non-zero — main's `_flatten_strategy_positions` lifecycle code is still running in the
listener. No reversion occurred.
