# Phase 2.1: `webhook_enabled` cleanup — report

## Summary

Removed all references to `public.strategies.webhook_enabled` from service code,
then applied an expand/contract migration to drop the column from the database.

---

## Files changed

### `order-listener/tests/test_webhook_handler.py`
Renamed `test_disabled_strategy_returns_403` → `test_stopped_strategy_returns_403`.
Updated the mock override to `{"enabled": False}` (no `webhook_enabled`).
The test checks for `"stopped"` in the 403 detail string, matching the current handler.

### `dashboard-api/src/routes/strategies.ts`
- CREATE INSERT: removed `webhook_enabled,` from column list and `true,` from VALUES.
- GET `/webhook-info` SELECT: removed `, webhook_enabled` from SELECT and response object.

### `strategy-tester/app/api/migrate.py`
- Module docstring: removed `webhook_enabled=False` from R2a constraint list.
- `from_matp` SELECT from `public.strategies`: removed `webhook_enabled,`.
- `to_matp` INSERT into `public.strategies`: removed `webhook_enabled,` / `FALSE,`.
- `to_matp` docstring, comment, log string, and return dict: all webhook_enabled refs removed.
- **`tester.strategies.webhook_enabled` left untouched** — that column still exists in tester schema.

### `tester-ui/src/components/PromoteSheet.tsx`
Replaced:
```
The promoted strategy starts with <code>enabled = false</code> and{' '}
<code>webhook_enabled = false</code>. You must manually activate it in the dashboard after reviewing the config.
```
With:
```
Created stopped (<code>enabled = false</code>). Review the config, then Start it from the dashboard.
```

### `db/migrations/027_drop_webhook_enabled.sql` (new)
```sql
ALTER TABLE public.strategies DROP COLUMN IF EXISTS webhook_enabled;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema='public' AND table_name='strategies'
               AND column_name='webhook_enabled') THEN
    RAISE EXCEPTION '027 failed: public.strategies.webhook_enabled still present';
  END IF;
END $$;
```

### `db/init.sql`
- Removed `webhook_enabled boolean DEFAULT true,` from `CREATE TABLE public.strategies`.
- Removed `webhook_enabled` from `COPY public.strategies` column list.
- Removed the `t` (webhook_enabled) value from all 3 data rows.
- Removed `CREATE INDEX idx_strategies_enabled ON public.strategies USING btree (webhook_enabled);`.
- Line 1032 (`tester.strategies`) and COPY tester column list left untouched.

---

## Deployments

```
docker compose build dashboard-api order-listener strategy-tester tester-ui
docker compose up -d --force-recreate dashboard-api order-listener strategy-tester tester-ui
```

All four containers running and healthy:
```
matp-dashboard-api-1     Up (healthy)    8003/tcp
matp-order-listener-1    Up (healthy)    0.0.0.0:8001->8001/tcp
matp-strategy-tester-1   Up (healthy)    0.0.0.0:8006->8006/tcp
matp-tester-ui-1         Up              80/tcp, 3001/tcp
```

---

## Migration output

```
ALTER TABLE
DO
```

---

## Grep confirmation

No remaining `public.strategies.webhook_enabled` references in live service code:

```
$ grep -rniE "webhook_enabled" dashboard-api/src order-listener/app | grep -v node_modules
(no output)
```

Two benign survivors:
- `strategy-tester/app/api/migrate.py:110` — INSERT into `tester.strategies`, which still has the column (correct).
- `order-listener/tests/test_webhook_handler.py:40` — orphaned `"webhook_enabled": True` in SAFE_STRATEGY fixture. Handler no longer reads this field; functionally inert.

---

## Test output

```
$ docker compose exec order-listener python -m pytest tests/test_webhook_handler.py -q

.F..F...                                                                 [100%]
=================================== FAILURES ===================================
FAILED tests/test_webhook_handler.py::test_valid_token_passes_auth
FAILED tests/test_webhook_handler.py::test_quote_variant_accepted_when_flag_on
2 failed, 6 passed, 2 warnings in 16.85s
```

`test_stopped_strategy_returns_403` **passes** (included in the 6 passed).

The 2 failures are **pre-existing** (unrelated to Phase 2.1):
both fail because the handler rejects unsized open orders when no exchange price is available
in the mock environment (`no webhook price and exchange mark price unavailable for BTC-USDT`).

---

## DB column verification

```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema='public' AND table_name='strategies' AND column_name='webhook_enabled';
-- (0 rows)
```

---

## Step 5 verification — partial

- **Create strategy**: `POST /strategies` → 201, no `webhook_enabled` in response. ✅
- **GET webhook-info**: response contains `{strategy_id, strategy_name, symbol, webhook_url, webhook_secret}` — no `webhook_enabled`. ✅
- **Promote (to-matp)**: blocked by a **pre-existing bug** — `to_matp` INSERT references `max_position_size` which does not exist in `public.strategies`. This bug pre-dates Phase 2.1 and is not introduced by it. The INSERT's absence of `webhook_enabled` is confirmed by code inspection.

---

## Notes

- The `from_matp` SELECT also references `max_position_size` from `public.strategies` (which doesn't exist). Same pre-existing bug as above — both directions of migrate.py are affected. Not addressed in this phase.
- `tester.strategies.webhook_enabled` is intentionally retained — the tester schema is independent of public schema lifecycle.
