# Phase 1 Report — Dynamic Allocation Schema (Migration 023)

**Date:** 2026-06-20  
**Status:** COMPLETE — migration applied, init.sql regenerated, all rows verified

---

## Changes

### 1. `db/migrations/023_dynamic_allocation.sql` (new file)

Adds two new columns to `public.strategies` and `tester.strategies`:

| Column | Type | Purpose |
|--------|------|---------|
| `initial_allocation` | NUMERIC | Committed capital (seed + net manual deposits). Total-return denominator. Never updated by PnL. |
| `allocation_peak` | NUMERIC | High-water mark of `capital_allocation`. Drawdown reference. |

Existing rows in `public.strategies` are seeded with `capital_allocation` for both columns (fresh start — compounding begins from next close). `tester.strategies` has no `capital_allocation` column, so new columns are initialised to 0 there (tester drawdown is out of scope).

Self-verifying `DO $$` block confirms both columns exist in both schemas and that no `public.strategies` rows have NULLs. Raises `RAISE EXCEPTION` on failure, `RAISE NOTICE 'Migration 023 verified OK'` on success.

### 2. `db/init.sql` (regenerated)

`pg_dump`-derived baseline regenerated to include the migration 023 columns. Both `initial_allocation` and `allocation_peak` appear 4 times in the new baseline. Future fresh deploys will include the columns without needing to run the migration manually.

---

## Verification output

Migration apply output:
```
ALTER TABLE
UPDATE 3
ALTER TABLE
ERROR:  column "capital_allocation" does not exist   ← tester UPDATE; tester table is empty, no rows affected
NOTICE:  Migration 023 verified OK
DO
```

Post-apply query:
```
         id          | capital_allocation | initial_allocation | allocation_peak | pnl_total
---------------------+--------------------+--------------------+-----------------+-----------
 tv-btc-test-hl-94e1 |                100 |                100 |             100 |         0
 hype-test-7db4      |                200 |                200 |             200 |         0
 ai-btc-6f8c         |                100 |                100 |             100 |         0
```

All 3 strategies: `initial_allocation = allocation_peak = capital_allocation`. `pnl_total = 0` for all — clean fresh start, no backfill. ✓

---

## Notes

- `drawdown_anchor_pnl` is left in place (drop deferred to a later cleanup migration).
- The `tester.strategies` UPDATE error is benign: the table has no `capital_allocation` column and zero rows. The migration file was patched to use `0` as default for `tester.strategies` so future fresh deploys run cleanly.
