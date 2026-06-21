# Social Listener — Phase 1: Read + Parse (Dry Run)
**Date:** 2026-06-21  
**Branch:** feat/signal-engine  
**Status:** Infrastructure complete. Awaiting human one-time Telegram credential setup.

---

## Migration Sequence Confirmation

```
$ ls -1 db/migrations/*.sql
db/migrations/022_reconcile_divergence.sql
db/migrations/023_dynamic_allocation.sql
db/migrations/024_shadow_signals.sql
db/migrations/025_social_signal_log.sql
```

**IMPORTANT CORRECTION FROM PROMPT:** The prompt specified migration `024`, but `024_shadow_signals.sql`
already existed. The correct next free number is **025**. The migration was created as
`db/migrations/025_social_signal_log.sql` accordingly.

---

## Step 1 — Migration Applied

```
$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/025_social_signal_log.sql
CREATE TABLE
CREATE INDEX
CREATE INDEX
NOTICE:  Migration 025 verified OK
DO
```

Self-verifying DO block passed with no errors.

---

## Step 2 — Table Shape

```
$ docker compose exec -T postgres psql -U matp -d matp -c '\d public.social_signal_log'

                                          Table "public.social_signal_log"
      Column       |           Type           | Collation | Nullable |                    Default
-------------------+--------------------------+-----------+----------+-----------------------------------------------
 id                | bigint                   |           | not null | nextval('social_signal_log_id_seq'::regclass)
 source            | text                     |           | not null |
 channel_msg_id    | bigint                   |           | not null |
 posted_at         | timestamp with time zone |           | not null |
 ingested_at       | timestamp with time zone |           | not null | now()
 raw_text          | text                     |           |          |
 preview_text      | text                     |           |          |
 x_url             | text                     |           |          |
 is_actionable     | boolean                  |           | not null |
 action_type       | text                     |           | not null |
 asset             | text                     |           |          |
 direction         | text                     |           |          |
 reference_price   | numeric                  |           |          |
 confidence        | numeric                  |           |          |
 in_whitelist      | boolean                  |           | not null | false
 model             | text                     |           |          |
 extractor_version | text                     |           |          |
 raw_llm_json      | jsonb                    |           |          |
Indexes:
    "social_signal_log_pkey" PRIMARY KEY, btree (id)
    "ix_social_signal_actionable" btree (is_actionable, posted_at DESC)
    "uq_social_signal_source_msg" UNIQUE, btree (source, channel_msg_id)
Check constraints:
    "social_signal_action_type_chk" CHECK (action_type = ANY (ARRAY['OPEN'::text, 'FLIP'::text, 'CLOSE'::text, 'ADD'::text, 'TRIM'::text, 'NONE'::text]))
    "social_signal_direction_chk" CHECK (direction IS NULL OR (direction = ANY (ARRAY['LONG'::text, 'SHORT'::text])))
```

All 18 columns present. UNIQUE dedup index `uq_social_signal_source_msg` confirmed. Both
CHECK constraints on `action_type` and `direction` confirmed.

---

## Step 3 — Image Build

```
$ docker compose build --no-cache social-listener
...
#9 140.8 Successfully installed annotated-types-0.7.0 anthropic-0.111.0 anyio-4.14.0
  asyncpg-0.31.0 certifi-2026.6.17 cffi-2.0.0 charset_normalizer-3.4.7
  cryptography-49.0.0 distro-1.9.0 docstring-parser-0.18.0 filetype-1.2.0
  google-auth-2.55.0 google-genai-2.9.0 h11-0.16.0 httpcore-1.0.9 httpx-0.28.1
  idna-3.18 jiter-0.15.0 jsonpatch-1.33 jsonpointer-3.1.1 langchain-anthropic-1.4.6
  langchain-core-1.4.8 langchain-google-genai-4.2.5 langchain-openai-1.3.2
  langchain-protocol-0.0.18 langsmith-0.8.18 openai-2.43.0 orjson-3.11.9
  packaging-26.2 pyaes-1.6.1 pyasn1-0.6.3 pyasn1-modules-0.4.2 pycparser-3.0
  pydantic-2.13.4 pydantic-core-2.46.4 pydantic-settings-2.14.2 python-dotenv-1.2.2
  pyyaml-6.0.3 regex-2026.5.9 requests-2.34.2 requests-toolbelt-1.0.0 rsa-4.9.1
  sniffio-1.3.1 telethon-1.44.0 tenacity-9.1.4 tiktoken-0.13.0 tqdm-4.68.3
  typing-extensions-4.15.0 typing-inspection-0.4.2 urllib3-2.7.0 uuid-utils-0.16.2
  websockets-16.0 xxhash-3.7.0 zstandard-0.25.0
...
 Image matp-social-listener Built
```

All packages installed. Image `matp-social-listener:latest` built successfully.

---

## Step 4 — Container State (no credentials set)

```
$ docker compose ps social-listener
NAME                     IMAGE                  COMMAND                SERVICE           CREATED         STATUS                        PORTS
matp-social-listener-1   matp-social-listener   "python -m app.main"   social-listener   41 seconds ago  Restarting (1) 1 second ago
```

Container starts, successfully initializes the DB pool, then exits with Telethon
`ValueError: Your API ID or Hash cannot be empty or None` — this is **expected** behavior
with no credentials set. `restart: unless-stopped` causes it to keep retrying. Once real
TG credentials are set in `.env`, it will connect and stay up.

---

## Step 5 — Startup Log (no credentials set)

```
$ docker compose logs social-listener
social-listener-1  | 2026-06-21 07:07:31,489 INFO app.db DB pool initialized
social-listener-1  | Traceback (most recent call last):
...
social-listener-1  | ValueError: Your API ID or Hash cannot be empty or None.
  Refer to telethon.rtfd.io for more information.
```

Confirms: DB pool initializes successfully (asyncpg connects to postgres). The Telethon
client build is the only failure, and only because credentials are missing.

---

## Step 6 — Table Query (empty, expected without ingestion)

```
$ docker compose exec -T postgres psql -U matp -d matp -c \
    "SELECT is_actionable, count(*) FROM public.social_signal_log GROUP BY 1 ORDER BY 1;"
 is_actionable | count
---------------+-------
(0 rows)
```

Table is queryable. Zero rows: correct — no ingestion runs have been completed yet
(awaiting TG credentials).

---

## Step 7 — Human One-Time Prerequisites (CANNOT be automated)

Before the live run can succeed, a human must:

1. **Register a Telegram app** at `https://my.telegram.org` → Apps → get `TG_API_ID` (integer)
   and `TG_API_HASH` (string). Use a **dedicated throwaway account**, never a personal account.

2. **Mint the StringSession locally** (interactive phone/code auth cannot run in a container):
   ```bash
   cd social-listener
   pip install telethon
   python -m app.generate_session
   # Outputs: TG_SESSION=1BQANOTECJHz...
   ```

3. **Add to `.env`** (never commit this file):
   ```
   TG_API_ID=<integer from my.telegram.org>
   TG_API_HASH=<string from my.telegram.org>
   TG_SESSION=<StringSession output from generate_session.py>
   ANTHROPIC_API_KEY=<your key>   # or OPENAI_API_KEY / GEMINI_API_KEY
   ```

4. **Redeploy** (env is injected at container start, no image rebuild needed):
   ```bash
   docker compose up -d --force-recreate social-listener
   docker compose logs -f social-listener
   ```

   Expected startup sequence:
   ```
   INFO app.db DB pool initialized
   INFO social-listener Telegram connected as <username>
   INFO social-listener Backfilling last 50 messages from AstronomerZero
   INFO social-listener msg 12345 [ACTIONABLE] OPEN BTC LONG ref=67500.0 conf=0.92
   INFO social-listener msg 12346 [·] NONE - ref=None conf=0.05
   ...
   INFO social-listener Backfill complete (50 messages)
   INFO social-listener Listening for new messages...
   ```

---

## Files Created

| Path | Purpose |
|------|---------|
| `db/migrations/025_social_signal_log.sql` | Audit table + indexes + self-verify DO block |
| `social-listener/Dockerfile` | `python:3.12-slim`, CMD `python -m app.main` |
| `social-listener/requirements.txt` | telethon, asyncpg, pydantic-settings, langchain-anthropic/openai/google-genai |
| `social-listener/app/__init__.py` | Empty package marker |
| `social-listener/app/config.py` | pydantic-settings Settings (env vars for TG, DB, LLM) |
| `social-listener/app/db.py` | asyncpg pool + `already_seen()` + `insert_signal()` |
| `social-listener/app/extractor.py` | LLM extractor (`SocialExtraction` Pydantic model, multi-provider) |
| `social-listener/app/telegram.py` | Telethon client builder + `to_record()` + X URL extraction |
| `social-listener/app/main.py` | Backfill + live event loop |
| `social-listener/app/generate_session.py` | Interactive session minter (run locally once) |
| `docker-compose.yml` (appended) | `social-listener:` service block |

---

## Scope Boundary Compliance

- No imports from or calls to: `order-listener`, `order-executor`, `ai-signal-generator`,
  `market-ingestion`, `dashboard-api`, or any CCXT/exchange code.
- No order placement, webhook calls, or `/webhook` endpoint hits.
- No reads or writes to `strategies`, `strategy_positions`, or any execution table.
- No modifications to existing migrations (`022`, `023`, `024`) or `db/init.sql`.
- `social_signal_log` is a standalone audit table; nothing else in the stack touches it.

---

## Extractor Model Note

Default is `claude-sonnet-4-6` via `EXTRACTOR_MODEL` env var. As specified in the prompt:
**do not downgrade to Flash Lite** for this service. The model is configurable via
`EXTRACTOR_PROVIDER` + `EXTRACTOR_MODEL` in `.env`.
