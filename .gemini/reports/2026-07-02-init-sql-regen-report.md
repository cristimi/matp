# db/init.sql regeneration — 2026-07-02

## Summary

`db/init.sql` was regenerated from the live `postgres` container (PostgreSQL 16.14, matching
`pg_dump` major version) as:

- **Full schema** (`pg_dump --schema-only --no-owner --no-privileges`) — captures every DDL
  change through migration `037` (`use_geometry`, `geometric_range` template,
  `candle_close_buffer_seconds` + its CHECK constraint), since all of 022–037 were already
  applied to the live DB.
- **Data only for four seed/reference tables**: `public.assets`, `public.trading_pairs`,
  `public.ai_prompt_templates`, `public.config` — each dumped separately with
  `pg_dump --data-only --no-owner --no-privileges --table=public.<t>` and assembled in
  FK-safe order (`assets` → `trading_pairs` → `ai_prompt_templates` → `config`).
- **No data for every other table**, including all secret-bearing tables
  (`exchange_accounts`, `strategies`) and all runtime/log tables (`orders`, `signal_log`,
  `strategy_positions`, `ai_signal_log`, `shadow_signals`, `social_*`, all `tester.*` data,
  etc.) — schema-only, empty COPY-free.

`db/init.sql`: 2543 → 2461 lines (net -82; secrets/live rows removed, new DDL for 035–037 added).

## `config` table — inspected before including

```
$ docker compose exec -T postgres psql -U matp -d matp -c "SELECT key, value FROM public.config ORDER BY key;"
        key         |    value    
--------------------+-------------
 active_platform    | hyperliquid
 max_order_size_btc | 1.0
 max_order_size_eth | 10.0
(3 rows)
```

All three rows are non-sensitive operational settings (no keys, tokens, or credentials) —
included as-is, unscrubbed.

## Verification 1 — schema drift closed (grep on new db/init.sql)

```
$ grep -c "candle_close_buffer_seconds" db/init.sql
2
$ grep -c "use_geometry" db/init.sql
1
$ grep -c "geometric_range" db/init.sql
1
```

Line-level confirmation:

```
206:    use_geometry boolean DEFAULT false NOT NULL,
207:    candle_close_buffer_seconds integer DEFAULT 150 NOT NULL,
208:    CONSTRAINT ai_strategy_config_candle_close_buffer_chk CHECK (((candle_close_buffer_seconds >= 0) AND (candle_close_buffer_seconds <= 600)))
...
2441: geometric_range   Geometric Range & Breakout   ... (full seed row in ai_prompt_templates COPY block)
```

## Verification 2 — no secrets

```
$ grep -A2 "COPY public.exchange_accounts" db/init.sql
(no output — no COPY block at all; schema-only)

$ grep -A2 "COPY public.strategies " db/init.sql
(no output — no COPY block at all; schema-only)

$ grep -n '\\x[0-9a-f]\{40,\}' db/init.sql
(no output — no long hex ciphertext blobs)

$ grep -in "blofin_token\|webhook_secret" db/init.sql
649:    webhook_secret character varying(255) NOT NULL,
1176:    webhook_secret character varying(255) DEFAULT encode(public.gen_random_bytes(16), 'hex'::text) NOT NULL,
1197:    blofin_token text,
1542:-- Name: strategies strategies_webhook_secret_key; Type: CONSTRAINT; Schema: public; Owner: -
1546:    ADD CONSTRAINT strategies_webhook_secret_key UNIQUE (webhook_secret);
1816:-- Name: idx_strategies_webhook_secret; Type: INDEX; Schema: public; Owner: -
1819:CREATE INDEX idx_strategies_webhook_secret ON public.strategies USING btree (webhook_secret);
```

All matches are column/constraint/index **definitions** only — no literal secret values
anywhere in the file (confirmed above: zero COPY data for `exchange_accounts`/`strategies`).

## Verification 3 — fresh-bootstrap smoke test (throwaway container, init.sql alone, no migrations applied)

```
$ docker run -d --name matp-init-smoketest \
    -e POSTGRES_USER=matp -e POSTGRES_PASSWORD=smoketest -e POSTGRES_DB=matp \
    -v /home/cristi/matp/db/init.sql:/docker-entrypoint-initdb.d/init.sql:ro \
    postgres:16-alpine
55b2412e1ce50a897254b29abd619f635effc058eb61c29de9eac98cfd505936
ready after 7s

$ docker logs matp-init-smoketest 2>&1 | grep -iE "error|fatal|exception"
no errors in init log
```

Schema checks:

```
$ docker exec matp-init-smoketest psql -U matp -d matp -c "\d public.ai_strategy_config" \
    | grep -E "use_geometry|candle_close_buffer|Check"
 use_geometry                | boolean                  |           | not null | false
 candle_close_buffer_seconds | integer                  |           | not null | 150
Check constraints:
    "ai_strategy_config_candle_close_buffer_chk" CHECK (candle_close_buffer_seconds >= 0 AND candle_close_buffer_seconds <= 600)
```

Seed-data checks:

```
$ docker exec matp-init-smoketest psql -U matp -d matp -c "SELECT count(*) FROM public.ai_prompt_templates;"
 count 
-------
     7
(1 row)

$ docker exec matp-init-smoketest psql -U matp -d matp -c "SELECT id FROM public.ai_prompt_templates ORDER BY id;"
       id        
-----------------
 breakout
 conservative
 geometric_range
 mean_reversion
 range_rotation
 scalper
 trend_following
(7 rows)
```

Note: the task brief said "→ 6 (incl. `geometric_range`)" as the expected count; the live DB
actually carries **7** templates (the `geometric_range` migration added a 7th to an existing
set of 6, not 5). Verified this is correct against live DB state before trusting it, per the
"trust what you observe now" rule — 7 is the accurate figure and what the regenerated file
ships.

```
$ docker exec matp-init-smoketest psql -U matp -d matp -c "SELECT count(*) FROM public.exchange_accounts;"
 count 
-------
     0
(1 row)

$ docker exec matp-init-smoketest psql -U matp -d matp -c "SELECT count(*) FROM public.strategies;"
 count 
-------
     0
(1 row)

$ docker exec matp-init-smoketest psql -U matp -d matp -c "SELECT count(*) FROM public.assets;"
 count 
-------
     4
(1 row)

$ docker exec matp-init-smoketest psql -U matp -d matp -c "SELECT count(*) FROM public.trading_pairs;"
 count 
-------
     1
(1 row)

$ docker exec matp-init-smoketest psql -U matp -d matp -c "SELECT * FROM public.config ORDER BY key;"
        key         |    value    |          updated_at           
--------------------+-------------+-------------------------------
 active_platform    | hyperliquid | 2026-06-11 18:20:58.536961+00
 max_order_size_btc | 1.0         | 2026-05-18 15:41:14.011312+00
 max_order_size_eth | 10.0        | 2026-05-18 15:41:14.011312+00
(3 rows)
```

CHECK constraint enforcement (out-of-range insert rejected):

```
$ docker exec matp-init-smoketest psql -U matp -d matp -c "
INSERT INTO public.ai_strategy_config (strategy_id, candle_close_buffer_seconds) VALUES ('smoketest', 700);"
ERROR:  new row for relation "ai_strategy_config" violates check constraint "ai_strategy_config_candle_close_buffer_chk"
DETAIL:  Failing row contains (smoketest, 4h, 15m, 5m, 1.50, t, t, t, t, t, f, f, f, {RSI,MACD,EMA50,EMA200,BB,VWAP}, 90, 0.720, 240, 60, 30, trend_following, null, t, t, t, t, f, 300.0, 0.0500, t, ..., f, 700).
```

700 > 600 upper bound → correctly rejected by the migration-037 CHECK constraint on the fresh
throwaway instance (no manual migrations applied — the constraint came from `init.sql` alone).

Container torn down after the test:

```
$ docker rm -f matp-init-smoketest
matp-init-smoketest
```

## Docs

`db/migrations/README.md` updated: the "already contains every change through migration `021`"
line now reads "through migration `037`", plus a new paragraph documenting which tables carry
seed data vs. schema-only in `init.sql`. `_archive/` guidance left untouched.

## Diff stat

```
$ git diff --stat db/init.sql db/migrations/README.md
 db/init.sql             | 726 +++++++++++++++++++++---------------------------
 db/migrations/README.md |   8 +-
 2 files changed, 329 insertions(+), 405 deletions(-)
```

## Operator action required (not part of this task)

The previously committed `db/init.sql` (prior to this regeneration) embedded **real encrypted
exchange credentials** for two demo accounts (Blofin-demo, Hyperliquid-demo) in its
`exchange_accounts` COPY block, plus `webhook_secret`/`blofin_token` values in `strategies`.
Those secrets remain in git history even after this fix. Treat both demo accounts' credentials
as **exposed and rotate them** on the exchange side. Purging them from git history (e.g. via
`git filter-repo` / BFG) is a separate, deliberately-not-automated step — flag before running
any history rewrite, since it force-rewrites shared history.
