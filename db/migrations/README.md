# db/migrations

## How schema changes work

**Fresh deploys** get the full schema automatically from `db/init.sql`, which is mounted at
`/docker-entrypoint-initdb.d/init.sql` and applied once by Postgres on first start. No manual
migration step is needed. `db/init.sql` is a `pg_dump`-derived snapshot and already contains
every change through migration `075`.

`db/init.sql` ships schema only for most tables — it carries seed/reference data for just
`ai_prompt_templates`, `assets`, `trading_pairs`, and `config` (scrubbed of any secret-bearing
rows). Secret-bearing tables (`exchange_accounts`, `strategies`, etc.) and all runtime/log
tables are intentionally schema-only, so a fresh deploy boots with zero exchange accounts and
zero strategies — the operator re-adds them via the app.

**New migrations** (post-baseline) are numbered from `022_`, zero-padded, one logical change
per file, placed directly here in `db/migrations/` (not in `_archive/`). Apply them manually
to existing instances:

```bash
docker compose exec -T postgres psql -U matp -d matp < db/migrations/022_your_change.sql
```

After applying a new migration to the live DB, regenerate `db/init.sql` so the baseline stays
current for future fresh deploys. It is a schema dump with the four seed tables appended, in
this order (`trading_pairs` references `assets`, so the order matters):

```bash
docker compose exec -T postgres pg_dump -U matp -d matp \
  --schema-only --no-owner --no-privileges \
  --exclude-table=public.ai_prompt_templates_pre_wave4 > /tmp/schema.sql

for t in assets trading_pairs ai_prompt_templates config; do
  docker compose exec -T postgres pg_dump -U matp -d matp \
    --data-only --no-owner --table=public.$t > /tmp/data_$t.sql
done
# then splice each data file's "-- Data for Name:" block into /tmp/schema.sql,
# just before its trailing "PostgreSQL database dump complete" footer
```

`ai_prompt_templates_pre_wave4` is excluded on purpose: it is a one-off backup left behind by
an old migration, referenced by no code, and a fresh deploy has no reason to create it.

Verify the result before committing by applying it to a throwaway database — a baseline that
does not build is worse than a stale one:

```bash
docker compose exec -T postgres psql -U matp -d postgres -c "CREATE DATABASE init_test;"
docker compose exec -T postgres psql -U matp -d init_test -v ON_ERROR_STOP=1 -q < db/init.sql
docker compose exec -T postgres psql -U matp -d init_test \
  -c "SELECT count(*) FROM strategies;"   # must be 0 — no secrets in the baseline
docker compose exec -T postgres psql -U matp -d postgres -c "DROP DATABASE init_test;"
```

## `_archive/`

Contains all pre-baseline migrations (`001`–`021`, including duplicate-numbered files and
`fix_strategies_insert.sql`). Kept for historical reference. **Do not re-run these against a
fresh instance** — `db/init.sql` already includes their effects.

See `_archive/MANIFEST.md` for the full list and notes on sequence-number collisions.
