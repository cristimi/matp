# Phase 3 Report — Reconciler Divergence Flag + Migration 022 (Bug 2)

**Date:** 2026-06-19  
**Status:** COMPLETE — all new tests pass; migration applied; init.sql regenerated

---

## Root cause (Bug 2)

The reconciler's "exchange larger" branch silently reset `reconcile_miss_count` but stored
no record of the discrepancy. There was no way for the dashboard or operator to know that
exchange size exceeded DB size. The HYPE incident (exchange 5.8 vs DB 1.45) was invisible
at the DB level — the reconciler kept resetting every pass with no trace.

---

## Changes

### 1. `db/migrations/022_reconcile_divergence.sql`

```sql
ALTER TABLE strategy_positions
    ADD COLUMN IF NOT EXISTS reconcile_divergent     BOOLEAN   NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS reconcile_exchange_size NUMERIC,
    ADD COLUMN IF NOT EXISTS reconcile_divergence_at TIMESTAMP WITH TIME ZONE;
```

Applied to live DB:
```
ALTER TABLE
```

### 2. `db/init.sql`

Regenerated from live DB via `pg_dump --schema-only` — now includes the three new columns
for fresh-deploy baseline.

### 3. `order-listener/app/reconciler.py` — three sub-changes

**SELECT**: added `sp.reconcile_divergent` so the loop knows whether a flag is already set.

**"Exchange larger" branch** — now flags divergence unconditionally:
```sql
UPDATE strategy_positions
SET reconcile_miss_count    = 0,
    reconcile_divergent     = TRUE,
    reconcile_exchange_size = $1,
    reconcile_divergence_at = COALESCE(reconcile_divergence_at, NOW()),
    updated_at              = NOW()
WHERE id = $2
```
`COALESCE` preserves the first-seen timestamp across subsequent passes.

**"Sizes match" branch** — clears the flag when present:
```sql
UPDATE strategy_positions
SET reconcile_miss_count    = 0,
    reconcile_divergent     = FALSE,
    reconcile_exchange_size = NULL,
    reconcile_divergence_at = NULL,
    updated_at              = NOW()
WHERE id = $1
```
No-op (no UPDATE) when `miss_count == 0 AND reconcile_divergent == FALSE` — avoids
gratuitous writes on every clean pass.

### 4. New tests — `order-listener/tests/test_reconcile_divergence.py`

| Test | Assertion |
|------|-----------|
| `test_exchange_larger_sets_divergent_flag` | SQL has `reconcile_divergent=TRUE` and `reconcile_exchange_size=$1` with correct value |
| `test_exchange_larger_preserves_first_seen_timestamp` | SQL uses `COALESCE(reconcile_divergence_at, NOW())` |
| `test_exchange_larger_also_resets_miss_count` | SQL has `reconcile_miss_count=0` (position IS confirmed present) |
| `test_sizes_match_clears_divergence_flag` | When previously flagged, match-reset clears all three columns |
| `test_sizes_match_no_write_when_already_clean` | No UPDATE when already `miss_count=0, divergent=FALSE` |

---

## Test output

### order-listener (48/50)

```
tests/test_reconcile_divergence.py::test_exchange_larger_sets_divergent_flag PASSED
tests/test_reconcile_divergence.py::test_exchange_larger_preserves_first_seen_timestamp PASSED
tests/test_reconcile_divergence.py::test_exchange_larger_also_resets_miss_count PASSED
tests/test_reconcile_divergence.py::test_sizes_match_clears_divergence_flag PASSED
tests/test_reconcile_divergence.py::test_sizes_match_no_write_when_already_clean PASSED
... (43 pre-existing tests: all passed)
```

Pre-existing failures (unchanged since Phase 1):
- `test_valid_token_passes_auth` — open-signal sizing requires live mark price
- `test_quote_variant_accepted_when_flag_on` — same

---

## What Phase 3 enables

After the next reconciler pass for the HYPE position (if it still exists on exchange):

```sql
-- Example state after Phase 3:
reconcile_divergent     = true
reconcile_exchange_size = 5.800000
reconcile_divergence_at = '2026-06-19 ...'  -- first detection
```

Phase 4 (dashboard-api) reads these columns and exposes them in the positions API
response so the operator can see the discrepancy without querying psql.
