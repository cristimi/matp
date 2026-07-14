# Multiple API keys per LLM provider

Date: 2026-07-14

## What changed

Moved from one key per LLM provider (single `config.llm_key_<provider>` row) to a
multi-key architecture with runtime rotation.

- **`db/migrations/056_llm_keys.sql`** — new `llm_keys` table (`provider`, `label`,
  `encrypted_key`, `enabled`, `priority`, timestamps). Existing config rows migrated in,
  old rows deleted. Same AES-256-GCM + `CONFIG_SECRET_KEY` encryption.
- **`ai-signal-generator/app/key_pool.py`** (new) — per-provider key pool: decrypts keys at
  load, hands out the highest-priority usable key, cools down rate-limited keys
  (escalating 60 s → 1 h), kills auth-failed keys in memory until reload, falls back to
  env-var keys for providers with no DB rows. Hot-reload via
  `POST /internal/llm-keys/reload`; runtime state via `GET /internal/llm-keys/status`.
- **`llm_chain.py`** — `_get_llm()` takes the key explicitly; new `classify_llm_error()`
  (rate_limit / auth / other); `_attempt_with_keys()` rotates keys *within* one
  (provider, model) candidate (max 3 keys) before the chain falls through to the next
  model. `served_by` and `attempts` now carry `key_label`.
- **`node_analyze.py`** — the scout keeps its no-model-fallback rule but now rotates keys
  on rate-limit/auth failures too.
- **`models_registry.py`** — probes and raw model lists take keys from the pool.
- **`config_secrets.py`** (strategy-tester, social-listener) — now read the top-priority
  enabled key per provider from `llm_keys` at startup (no rotation there).
  `ai-signal-generator/app/config_secrets.py` deleted (replaced by the pool).
  Note: strategy-tester's map excludes cerebras/zhipu — its Settings has no such fields
  (found live: `ValueError: "Settings" object has no field "cerebras_api_key"`, fixed).
- **dashboard-api `routes/config.ts`** — CRUD replaces the single-key GET/PUT:
  `GET /config/llm-keys` (grouped, no key material), `POST` (add), `PATCH /:id`
  (label/enabled/priority/replace key), `DELETE /:id`, `GET /llm-keys/status` (proxy to
  ai-signal-generator, 8 s timeout — host load regularly makes cross-container calls
  slow). Every mutation fire-and-forgets the signal generator's reload endpoint.
- **dashboard-ui Settings** — per-provider key list with label, ACTIVE/COOLDOWN/AUTH
  FAILED badge, enable/disable, delete, add-key form. `api.patch` helper added.

## Verification (live outputs)

Migration applied — 4 keys moved, old rows gone:

```
CREATE TABLE / CREATE INDEX / INSERT 0 4 / DELETE 4
 id | provider |  label   | enabled | priority
----+----------+----------+---------+----------
  4 | cerebras | migrated | t       |        0
  1 | groq     | migrated | t       |        0
  2 | openai   | migrated | t       |        0
  3 | zhipu    | migrated | t       |        0
 old_rows: 0
```

Key pool loads on startup (DB keys + gemini/anthropic env fallbacks):

```
app.key_pool: key_pool: loaded {'cerebras': 1, 'groq': 1, 'openai': 1, 'zhipu': 1, 'gemini': 1, 'anthropic': 1}
```

Internal endpoints:

```
GET  /internal/llm-keys/status → {"providers":{"cerebras":[{"id":4,"label":"migrated","state":"active",...}], ...}}
POST /internal/llm-keys/reload → {"status":"ok","keys_per_provider":{"cerebras":1,"groq":1,"openai":1,"zhipu":1,"gemini":1,"anthropic":1}}
```

CRUD round-trip through nginx (add → hot reload picked it up → disable → delete):

```
POST  /api/dashboard/config/llm-keys        → {"id":5,"provider":"zhipu","label":"e2e-test","enabled":true,"priority":1,...}
log:  key_pool: loaded {... 'zhipu': 2 ...}   (reload triggered by dashboard-api)
PATCH /api/dashboard/config/llm-keys/5      → {"id":5,...,"enabled":false,...}
DELETE /api/dashboard/config/llm-keys/5     → {"deleted":5}
DB after: 4 rows (ids 1–4), e2e-test gone
```

strategy-tester reads from the new table at startup:

```
app.config_secrets: config: applied DB key for groq_api_key
app.config_secrets: config: applied DB key for openai_api_key
```

Tests (run inside the built image, tests dir mounted):

```
1 failed, 82 passed
```

The one failure is `tests/test_ohlcv.py::test_fetch_ohlcv_separates_closed_candles_from_live_price`,
which also fails on unmodified HEAD (verified via `git stash`) — pre-existing, unrelated.
New coverage: rate-limit rotates key within a candidate, auth failure kills the key,
all-keys-limited falls through to the next chain candidate, non-key errors don't burn keys.

Deploy state: `ai-signal-generator`, `dashboard-api`, `dashboard-ui`, `strategy-tester`
all rebuilt via `./scripts/redeploy.sh`, all healthy. Live UI asset `index-DXoFIzWw.js`
contains the new Settings section (grep in the served container confirmed).

## Behavior notes

- Rotation policy is failover-by-priority; a 429 cools the key down, so sustained load
  drifts to the next key and returns automatically (implicit quota stacking). Round-robin
  was considered and deferred.
- Auth-failure disablement is in-memory only (a transient 403 must not permanently kill a
  key); the DB `enabled` flag is only ever changed by the user.
- strategy-tester / social-listener stay startup-only consumers (top-priority key, no
  rotation) — acceptable since backtests are manual and restartable.
