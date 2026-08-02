# db/init.sql regenerated

The fresh-deploy baseline had drifted badly: it claimed to be current through migration
`037` and was in fact missing ten tables added since, plus everything from migration `075`.
A fresh deploy from it would have booted a database the services could not use.

## What it now contains

Full schema dump (public + tester schemas) with the four seed tables appended in dependency
order — `assets`, `trading_pairs`, `ai_prompt_templates`, `config`. Everything else is
schema-only, so a fresh deploy still boots with zero exchange accounts, zero strategies and
zero keys.

Tables that were missing from the old baseline and are now present:

```
funding_harvest_plans   notification_log      order_price_history   push_subscriptions
social_extraction_cache social_pending_trims  spread_plans          spread_positions
llm_keys                ai_prompt_template_versions                 strategy_change_log
```

Plus the change-tracking functions and triggers from migration 075
(`log_config_change`, `bump_prompt_template_version`, `snapshot_prompt_template`).

`ai_prompt_templates_pre_wave4` is deliberately excluded — a one-off backup left by an old
migration, referenced by no code. `db/migrations/README.md` now documents the regeneration
command including that exclusion, so it does not creep back in.

## Verification

Applied to a throwaway database, not just diffed:

```
$ docker compose exec -T postgres psql -U matp -d postgres -c "CREATE DATABASE init_test2;"
$ docker compose exec -T postgres psql -U matp -d init_test2 -v ON_ERROR_STOP=1 -q < db/init.sql
APPLIED CLEAN

 tables | templates | versions | changelog | strategies | accounts
--------+-----------+----------+-----------+------------+----------
     43 |         9 |        9 |         9 |          0 |          0
```

`strategies = 0` and `accounts = 0` is the point: no secrets and no live state in the
baseline. `versions = 9` shows the prompt-versioning trigger firing correctly during the
seed load — a fresh install records v1 of every prompt automatically.

Structure compared against the live database, column by column:

```
$ diff <(live: table_schema.table_name for public+tester) <(fresh: same)
TABLES IDENTICAL

$ diff <(live: every column with its data type) <(fresh: same)
ALL COLUMNS IDENTICAL
```

(That pair was run against the first regeneration, which still included
`ai_prompt_templates_pre_wave4`; the final file differs from live by exactly that one
excluded backup table — 43 tables instead of 44.)

Both scratch databases were dropped afterwards.

## Diff size

```
 db/init.sql | 1234 insertions(+), 32 deletions(-)
```

Most of that is the accumulated drift from migrations 038–074, not migration 075.
