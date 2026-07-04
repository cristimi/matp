# Phase 1 (intrabar) — per-strategy `entry_trigger` flag (plumb-only)

Branch: `main`. Signal-engine is shadow-only — no live trading impact.

**Scope confirmation: no intrabar evaluation logic was added, and no strategy was switched to
`intrabar`.** Every existing strategy's `entry_trigger` is `bar_close` (the default), and
`evaluate()` does not read `entry_trigger` anywhere. The flag is inert plumbing only, to be
implemented in a later phase.

## Migration number confirmation

```
$ ls db/migrations/*.sql | sort | tail -3
db/migrations/036_geometric_range_template.sql
db/migrations/037_candle_close_buffer.sql
db/migrations/038_geometric_range_limit_orders.sql
```

Next free number confirmed as **039**, as expected.

## Migration 039 applied

`db/migrations/039_add_entry_trigger.sql` — adds `entry_trigger varchar(16) DEFAULT 'bar_close'
NOT NULL` to both `public.strategies` and `tester.strategies`, with a CHECK constraint limiting
values to `bar_close` / `intrabar`. Mirrors the `local_signal_mode` (migration 024) dual-schema
pattern and the migration-037 self-verify style.

```
$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/039_add_entry_trigger.sql
BEGIN
ALTER TABLE
ALTER TABLE
ALTER TABLE
ALTER TABLE
COMMIT
NOTICE:  Migration 039 OK: entry_trigger on public+tester strategies, default bar_close
DO
```

## Before/after column state

```
$ docker compose exec -T postgres psql -U matp -d matp -c "\d public.strategies" | grep -i entry_trigger
 entry_trigger        | character varying(16)    |           | not null | 'bar_close'::character varying
    "strategies_entry_trigger_chk" CHECK (entry_trigger::text = ANY (ARRAY['bar_close'::character varying, 'intrabar'::character varying]::text[]))

$ docker compose exec -T postgres psql -U matp -d matp -c "SELECT id, entry_trigger FROM public.strategies;"
           id           | entry_trigger 
------------------------+---------------
 hype-breakout-da2e     | bar_close
 hype-test-7db4         | bar_close
 sui-manual-59d9        | bar_close
 ai-btc-6f8c            | bar_close
 tv_test_harness        | bar_close
 tv-btc-test-hl-94e1    | bar_close
 matp-test-harness-fe19 | bar_close
(7 rows)
```

All existing rows are `bar_close` as expected.

## Constraint-rejection proof

```
$ docker compose exec -T postgres psql -U matp -d matp -c "UPDATE public.strategies SET entry_trigger='bogus' WHERE id='tv_test_harness';"
ERROR:  new row for relation "strategies" violates check constraint "strategies_entry_trigger_chk"
DETAIL:  Failing row contains (tv_test_harness, ... bogus).

$ docker compose exec -T postgres psql -U matp -d matp -c "SELECT id, entry_trigger FROM public.strategies WHERE id='tv_test_harness';"
       id        | entry_trigger 
-----------------+---------------
 tv_test_harness | bar_close
(1 row)
```

The bogus update was rejected and the value remained `bar_close`.

## Code changes

- `signal-engine/app/engine.py` — `load_active_strategies` now selects
  `COALESCE(entry_trigger, 'bar_close') AS entry_trigger`, sets `strat.entry_trigger` on the
  loaded `TestHarnessStrategy` instance, and logs it in the load line. `entry_trigger` is not
  read anywhere in the evaluation path.
- `signal-engine/app/strategies/base.py` — `Strategy` Protocol documents the new
  `entry_trigger: str` attribute (plumbed, not yet consumed).
- `signal-engine/app/strategies/test_harness.py` — `TestHarnessStrategy` gets a default class
  attribute `entry_trigger: str = "bar_close"`.

## Engine reads it — deploy + log proof

```
$ ./scripts/redeploy.sh signal-engine
...
✓ signal-engine redeployed.

$ docker compose logs signal-engine | grep -i entry_trigger
signal-engine-1  | 2026-07-04 04:51:09,596 [INFO] app.engine: engine: loaded strategy=tv_test_harness symbol=BTC-USDT tf=1h mode=shadow entry_trigger=bar_close
```

## No behavior change

```
$ docker compose logs signal-engine | grep -iE "error|traceback|exception"
(no output)

$ docker compose ps signal-engine
NAME                   IMAGE                SERVICE         STATUS
matp-signal-engine-1   matp-signal-engine   signal-engine   Up

shadow_signals count for tv_test_harness:
  before redeploy: 245
  after redeploy (~15s later): 247
```

Count is accumulating normally (no drop, no errors) — behavior is unchanged.

## Summary

`entry_trigger` is now a real, constrained column on both `public.strategies` and
`tester.strategies`, defaulting to `bar_close`, read by the engine and logged at load time.
**No intrabar evaluation logic exists yet, and no strategy runs in `intrabar` mode** — this
phase is plumbing only. A later phase will implement the intrabar entry-evaluation path and
make use of this flag.
