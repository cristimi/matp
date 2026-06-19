# Phase 3 Remediation Report

**Date:** 2026-06-19  
**Scope:** `db/init.sql` restore + `db/migrations/022_reconcile_divergence.sql` self-verify block  
**Reconciler code:** NOT TOUCHED (correct as of `ef1de84`)

---

## Problem

Commit `ef1de84` (Phase 3) regenerated `db/init.sql` from `pg_dump --schema-only` of the live DB.
That stripped all 14 seed `INSERT` statements and added 46 `OWNER TO` statements, meaning a
fresh-volume deploy would produce empty tables (no assets, no trading pairs, no AI templates).
Migration 022 also lacked the self-verifying `DO $$ … RAISE NOTICE` block required by house style.

---

## Step 1a — Restore `db/init.sql` from Phase 2 commit (739d080)

```
git checkout 739d080 -- db/init.sql
```

Before/after `grep` counts:

| Metric | Bad (ef1de84) | Restored (739d080) |
|--------|---------------|--------------------|
| `INSERT INTO` lines | 0 | **14** |
| `OWNER TO` lines | 46 | **0** |

---

## Step 1b — Surgically add 3 divergence columns to `public.strategy_positions`

Anchor: `reconcile_miss_count integer DEFAULT 0 NOT NULL` appears exactly once (line 630,
`public.strategy_positions`). Tester table has no `reconcile_*` columns — not touched.

`grep -n "reconcile_" db/init.sql` after edit:
```
630:    reconcile_miss_count integer DEFAULT 0 NOT NULL,
631:    reconcile_divergent boolean DEFAULT false NOT NULL,
632:    reconcile_exchange_size numeric,
633:    reconcile_divergence_at timestamp with time zone
```

All four columns appear together, exclusively in the public table.

---

## Step 2 — Self-verify block added to migration 022

Appended after the `ALTER TABLE` statements:

```sql
DO $$
DECLARE
  n INT;
BEGIN
  SELECT COUNT(*) INTO n
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name   = 'strategy_positions'
    AND column_name IN ('reconcile_divergent', 'reconcile_exchange_size', 'reconcile_divergence_at');

  IF n <> 3 THEN
    RAISE EXCEPTION 'Migration 022: expected 3 reconcile divergence columns on public.strategy_positions, found %', n;
  END IF;
  RAISE NOTICE 'Migration 022 verified OK — reconcile_divergent / reconcile_exchange_size / reconcile_divergence_at present';
END $$;
```

---

## Step 3 — Fresh-volume verification

```
docker compose down
docker volume rm matp_postgres_data
docker compose up -d postgres
sleep 8
```

### Seed counts

```sql
SELECT count(*) FROM public.assets;
-- count: 4  ✓

SELECT count(*) FROM public.trading_pairs;
-- count: 1  ✓

SELECT count(*) FROM public.ai_prompt_templates;
-- count: 6  ✓
```

### Divergence columns on fresh DB

```
\d public.strategy_positions | grep reconcile

 reconcile_miss_count    | integer                  | not null | 0
 reconcile_divergent     | boolean                  | not null | false
 reconcile_exchange_size | numeric                  |          |
 reconcile_divergence_at | timestamp with time zone |          |
```

All four `reconcile_*` columns present. ✓

### Migration 022 applied to fresh DB (idempotency test)

```
docker compose exec -T postgres psql -U matp -d matp < db/migrations/022_reconcile_divergence.sql

NOTICE:  column "reconcile_divergent" of relation "strategy_positions" already exists, skipping
NOTICE:  column "reconcile_exchange_size" of relation "strategy_positions" already exists, skipping
NOTICE:  column "reconcile_divergence_at" of relation "strategy_positions" already exists, skipping
ALTER TABLE
DO
NOTICE:  Migration 022 verified OK — reconcile_divergent / reconcile_exchange_size / reconcile_divergence_at present
```

`ADD COLUMN IF NOT EXISTS` is idempotent — skips gracefully when columns already exist via
`init.sql`. Self-verify block confirms all three columns are present. ✓

---

## Files changed (only 2 + this report)

```
db/init.sql                              — restored seed INSERTs; added 3 divergence columns
db/migrations/022_reconcile_divergence.sql — added self-verify DO block
.gemini/reports/PHASE3_REMEDIATION.md   — this report
```
