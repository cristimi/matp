# 2026-07-12 — LLM Failure Fallback Chain + Scout/Premium Model Tiering

Prompt: `docs/process/prompts/2026-07-12-llm-fallback-tiering.prompt`

Two features in `ai-signal-generator`, plus dashboard config surface:

- **Feature A — fallback chain**: an LLM failure (exception, timeout, or
  structured-output parse failure) no longer kills the cycle; an ordered
  chain of candidates is walked until one answers or the chain is exhausted.
- **Feature B — scout/premium tiering**: optional cheap scout model runs the
  cycle first; `hold` is final, any proposed action (or scout failure)
  escalates to the configured premium model, whose output is the only one
  that ever proceeds to guard/dispatch.

Commits (all on `main`):

| Phase | Commit | Content |
|---|---|---|
| 1 | `96f96ba` | migration 053 + config plumbing |
| 2 | `69b4335` | fallback chain (`llm_chain.py`) + 10 unit tests |
| 3 | `41dab56` | scout tiering in `node_analyze` + 11 unit tests |
| 4 | `6c85a56` | dashboard-api validation + UI form + tier badge |
| 5 | (this commit) | prompt + report |

---

## Phase 1 — Migration 053 + config plumbing

`ls db/migrations` before writing (highest was 052):

```
... 050_reduce_candle_close_buffer.sql
051_geometric_range_moderate_fit.sql
052_ai_signal_log_missing_inputs.sql
_archive
README.md
```

Created `db/migrations/053_llm_fallback_and_scout_tiering.sql`. Apply output:

```
BEGIN
ALTER TABLE
DO
COMMENT
COMMENT
COMMENT
ALTER TABLE
COMMENT
COMMENT
COMMENT
COMMIT
psql:<stdin>:108: NOTICE:  Migration 053 verified OK: scout tiering + fallback chain columns exist
DO
```

`\d` verification (new columns only):

```
 llm_scout_provider          | character varying(20)    |           |          |
 llm_scout_model             | character varying(50)    |           |          |
 premium_force_interval      | integer                  |           | not null | 12
 llm_fallback_chain          | jsonb                    |           |          |
    "ai_strategy_config_premium_force_interval_chk" CHECK (premium_force_interval >= 1 AND premium_force_interval <= 1000)
---
 llm_tier              | character varying(16)    |           |          |
 scout_input_tokens    | integer                  |           |          |
 scout_output_tokens   | integer                  |           |          |
 scout_total_tokens    | integer                  |           |          |
 fallback_attempts     | jsonb                    |           |          |
```

Plumbing: the scheduler's `_build_initial_state` uses `SELECT s.*, a.*` so the
new columns flow into `strategy_config` automatically; the manual-trigger
endpoint (`main.py /internal/trigger`) uses an explicit column list, so the
four new columns were added there. `AgentState` gained `llm_tier`,
`llm_served_by`, `scout_usage`, `fallback_attempts`.

## Phase 2 — Feature A: fallback chain

New module `app/graph/llm_chain.py`:

- `call_llm_chain(prompt, candidates)` — tries each `(provider, model)` in
  order. Failure = exception, timeout, or `parsed is None`; all three advance
  the chain. `_STRUCTURED_OUTPUT_METHOD` is applied per attempted provider.
  Every failed attempt is logged at WARNING with the failed model, and the
  serving model logs which models it recovered from.
- `build_fallback_chain(...)` — manual `llm_fallback_chain` override used
  verbatim when set (handles asyncpg's jsonb-as-string); otherwise
  auto-derived from the registry probe cache: same-provider verified models
  first (primary excluded), then verified models of other providers that
  have an API key configured. Cold cache → the primary provider's raw model
  list. Cap: 3 fallbacks beyond the primary.
- `models_registry` gained cache-only accessors (`cached_ok_models`,
  `known_providers`, `raw_models`) so live cycles never re-probe inline.
- `node_dispatch` now persists `llm_tier`, `fallback_attempts`, and the
  scout token columns; `llm_provider`/`llm_model` record the model that
  actually served the decision (a fallback can differ from configured).

## Phase 3 — Feature B: scout/premium tiering

Implemented inside `node_analyze` (no graph-shape change, so the
`skip_geometry` path is untouched):

1. `llm_scout_provider` NULL → exactly the Phase-2 single-call behavior.
2. Deterministic premium triggers (scout skipped): see next section.
3. Scout runs the full normal prompt with the full `LLMSignalOutput` schema.
   `hold` → final, `llm_tier='scout'`, its usage in the MAIN token columns,
   scout columns NULL (one call happened).
4. Non-hold proposal → premium (with feature-A chain) decides;
   `llm_tier='scout_escalated'`, scout usage in scout columns, premium usage
   in main columns. If the premium chain is exhausted, the cycle fails with
   `llm_error` — the scout output is never promoted.
5. Scout failure → single attempt only (no chain walk for the cheap tier),
   WARNING logged, escalate straight to premium.

### Deterministic triggers: implemented vs. skipped

Implemented (evaluated from AgentState + one indexed `ai_signal_log` lookup):

- **`first_cycle`** — no `ai_signal_log` history for the strategy → premium.
  (Not in the spec's candidate list; added because with zero history there is
  no baseline for any comparison trigger and no tier history for the Nth-cycle
  counter — the safe default is the premium model.)
- **`fit_quality_changed`** — current `geometry_data.fit_quality` vs. the
  previous cycle's logged `geometry_data->>'fit_quality'` (persisted since
  migration 044). Fires only when both sides are non-null.
- **`premium_force_interval`** — count of consecutive `llm_tier='scout'` rows
  since the last premium-deciding row (`llm_tier IS DISTINCT FROM 'scout'`;
  NULL/historical rows count as premium-deciding). Forces when
  `count + 1 >= premium_force_interval`, so interval=1 forces every cycle.

Not implementable without new data fetches (skipped, documented in code):

- **SL/TP proximity** — the open position's SL/TP prices exist only on the
  exchange/listener side. `strategy_positions` has no SL/TP columns,
  `AgentState` doesn't carry them, and `ai_signal_log` doesn't record the
  resolved SL/TP of the opening signal. Implementing this would require a new
  data fetch (listener call) or a new persisted column — both out of scope
  per the spec's "do not add new data fetches".
- **`volatility_regime` changed** — the regime
  (`{atr_percentile, bb_width_percentile, squeeze_flag}`) is computed fresh
  each cycle and never persisted anywhere, so there is no previous value to
  compare against. Would need a new `ai_signal_log` column (not in the
  migration spec) to become implementable.

### Test gates (Phases 2+3)

Run per repo convention in a disposable `python:3.11-slim` container
(`pip install pytest numpy pandas asyncpg pydantic pydantic-settings httpx ccxt`),
no live LLM/DB calls (everything mocked):

```
tests/test_tiering.py::test_scout_null_single_premium_call PASSED        [  4%]
tests/test_tiering.py::test_scout_hold_is_final_no_premium_call PASSED   [  9%]
tests/test_tiering.py::test_scout_action_escalates_premium_decides PASSED [ 14%]
tests/test_tiering.py::test_scout_failure_escalates_to_premium PASSED    [ 19%]
tests/test_tiering.py::test_scout_parse_failure_escalates_and_keeps_scout_spend PASSED [ 23%]
tests/test_tiering.py::test_premium_exhausted_scout_not_promoted PASSED  [ 28%]
tests/test_tiering.py::test_first_cycle_forces_premium PASSED            [ 33%]
tests/test_tiering.py::test_fit_quality_change_forces_premium PASSED     [ 38%]
tests/test_tiering.py::test_nth_cycle_force PASSED                       [ 42%]
tests/test_tiering.py::test_nth_cycle_not_yet_reached_scout_runs PASSED  [ 47%]
tests/test_tiering.py::test_force_interval_one_always_premium PASSED     [ 52%]
tests/test_llm_chain.py::test_primary_success_no_fallback PASSED         [ 57%]
tests/test_llm_chain.py::test_primary_exception_fallback_serves PASSED   [ 61%]
tests/test_llm_chain.py::test_primary_timeout_fallback_serves PASSED     [ 66%]
tests/test_llm_chain.py::test_parse_failure_fallback_serves PASSED       [ 71%]
tests/test_llm_chain.py::test_chain_exhausted PASSED                     [ 76%]
tests/test_llm_chain.py::test_manual_override_respected PASSED           [ 80%]
tests/test_llm_chain.py::test_manual_override_capped_at_three PASSED     [ 85%]
tests/test_llm_chain.py::test_auto_derivation_same_provider_first PASSED [ 90%]
tests/test_llm_chain.py::test_cold_cache_raw_list_fallback PASSED        [ 95%]
tests/test_llm_chain.py::test_invalid_override_falls_back_to_auto PASSED [100%]
======================== 21 passed, 1 warning in 1.24s =========================
```

Full suite: `1 failed, 64 passed` — the one failure is
`tests/test_ohlcv.py::test_fetch_ohlcv_separates_closed_candles_from_live_price`,
which is **pre-existing and environmental**: verified by `git stash -u` (all
feature changes removed) and re-running — it fails identically on clean
`main` (`1 failed, 4 passed` in that file). Cause: the disposable container
installs an unpinned current `ccxt` whose fake-exchange surface changed
(`'_FakeExchange' object has no attribute 'fetch_markets'`). Unrelated to
this work; not fixed here (scope guard).

### Worst-case cycle latency

Per-attempt timeouts: primary keeps the existing 90 s; **fallback attempts
were reduced to 45 s** (`_FALLBACK_TIMEOUT`). Reasoning: a healthy verified
model answers this prompt well under 45 s; by the time a fallback runs the
cycle has already burned up to 90 s, and doubling down with 90 s per fallback
would allow 360 s of pure LLM wait.

- No scout, worst case: 90 + 3×45 = **225 s**.
- Scout configured, worst case (scout times out at 90 s, then full premium
  chain): 90 + 90 + 3×45 = **315 s** (~5.3 min).

Assessment: the worst case requires a simultaneous multi-provider outage
(primary + 3 fallbacks all failing at full timeout) — the realistic bad case
is one provider down, adding ≤45 s. Schedulers wake at candle-close+buffer,
so a pathological cycle on a 5m at-risk interval could complete after the
next candle closes; the scheduler serializes per strategy (no overlap), and a
late decision on fresh-enough data beats today's behavior (dead cycle, no
decision at all). No further timeout reduction applied.

## Phase 4 — Dashboard API + UI

- `ai.ts`: new fields in `ALLOWED_CONFIG_FIELDS`; validation —
  `llm_scout_provider` null or VALID_PROVIDERS, `premium_force_interval`
  integer 1–1000, `llm_fallback_chain` null or array of `{provider, model}`
  with valid provider and non-empty model. The chain is `JSON.stringify`-ed
  before insert (pg would otherwise serialize a JS array as a Postgres array
  literal, which breaks jsonb).
- `Strategies.tsx` (edit form, LLM Configuration section): scout
  provider+model dropdown pair reusing the `/api/ai/models` endpoint pattern
  (with the verified/⚠-unverified marking), premium-force-interval number
  input (shown only when a scout is selected), fallback-chain JSON textarea
  with client-side validation and a "leave empty for automatic" placeholder.
- `AiSignalLog.tsx`: `llm_tier` badge on each row (scout green /
  scout escalated yellow / fallback orange / premium neutral) + Tier, Scout
  tokens, and Failed attempts cells in the expanded detail grid.

Gate evidence — `docker compose build --no-cache dashboard-api dashboard-ui`
then `./scripts/redeploy.sh` for both:

```
 Image matp-dashboard-ui Built
 Image matp-dashboard-api Built
✓ dashboard-api redeployed.
   live dashboard-ui asset: index-BnLeWbXi.js
✓ dashboard-ui redeployed.
```

PUT (scout fields set) and GET (persisted):

```
$ curl -s -X PUT http://localhost/api/ai/strategies/bnb-ai-scalper-edbb/config \
    -H 'Content-Type: application/json' \
    -d '{"llm_scout_provider":"google","llm_scout_model":"gemini-2.5-flash-lite",
         "premium_force_interval":6,
         "llm_fallback_chain":[{"provider":"google","model":"gemini-2.5-flash"},
                               {"provider":"groq","model":"llama-3.3-70b-versatile"}]}'
    "llm_scout_provider": "google",
    "llm_scout_model": "gemini-2.5-flash-lite",
    "premium_force_interval": 6,
    "llm_fallback_chain": [ ...

$ curl -s http://localhost/api/ai/strategies/bnb-ai-scalper-edbb/config
{
  "strategy_id": "bnb-ai-scalper-edbb",
  "llm_provider": "anthropic",
  "llm_model": "claude-haiku-4-5-20251001",
  "llm_scout_provider": "google",
  "llm_scout_model": "gemini-2.5-flash-lite",
  "premium_force_interval": 6,
  "llm_fallback_chain": [
    { "model": "gemini-2.5-flash",         "provider": "google" },
    { "model": "llama-3.3-70b-versatile",  "provider": "groq" }
  ]
}
```

Validation rejections (verbatim):

```
{"error":"llm_scout_provider must be null or one of: google, openai, anthropic, groq, cerebras, zhipu"}
{"error":"premium_force_interval must be an integer between 1 and 1000"}
{"error":"llm_fallback_chain must be null or an array of {provider, model} with provider in [google, openai, anthropic, groq, cerebras, zhipu] and non-empty model"}
```

Built code verified inside the containers:

```
$ docker compose exec -T dashboard-ui grep -rl 'Scout model (optional)' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-BnLeWbXi.js
$ docker compose exec -T dashboard-api grep -rl 'llm_scout_provider' /app/dist/
/app/dist/routes/ai.js
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-BnLeWbXi.js
```

The chain override was then reset to NULL on the demo strategy so the live
smoke exercises auto-derivation (the default path).

## Phase 5 — Runtime smoke

`./scripts/redeploy.sh ai-signal-generator`:

```
matp-ai-signal-generator-1   matp-ai-signal-generator   "uvicorn app.main:ap…"   ai-signal-generator   21 seconds ago   Up 4 seconds (health: starting)   8005/tcp
✓ ai-signal-generator redeployed.
$ docker compose exec -T nginx wget -qO- http://ai-signal-generator:8005/health
{"status":"ok","service":"ai-signal-generator","collector":{...}}
```

Demo strategy: `bnb-ai-scalper-edbb` (dry-run) — premium
`anthropic/claude-haiku-4-5-20251001`, scout `google/gemini-2.5-flash-lite`,
`premium_force_interval=6`. Cycles driven via the dashboard manual trigger.

**Scout-final cycle** (live log):

```
2026-07-12 18:16:01,845 [INFO] app.graph.nodes.node_analyze: LLM [google/gemini-2.5-flash-lite] tier=scout → action=hold confidence=0.600 tokens=1950 (premium call saved)
```

**Natural `scout_escalated`** — not fabricated: Google returned a real 503 on
the scout call, which escalated to the premium model per design:

```
2026-07-12 18:17:14,576 [WARNING] app.graph.nodes.node_analyze: Scout [google/gemini-2.5-flash-lite] failed strategy=bnb-ai-scalper-edbb — escalating to premium: ServerError: 503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand. ...', 'status': 'UNAVAILABLE'}}
2026-07-12 18:17:30,419 [INFO] app.graph.nodes.node_analyze: LLM [anthropic/claude-haiku-4-5-20251001] tier=scout_escalated → action=hold confidence=0.450 tokens=3492
```

(No scout-proposed-action escalation occurred in the window — every
successful scout call answered `hold`, which is the expected common case and
exactly the cost-saving behavior. The action-escalation path is covered by
unit tests `test_scout_action_escalates_premium_decides` and
`test_premium_exhausted_scout_not_promoted`.)

DB gate query (spec's `created_at` column is actually named `triggered_at`):

```
$ SELECT id, triggered_at, llm_provider, llm_model, llm_tier, total_tokens, scout_total_tokens, fallback_attempts
  FROM ai_signal_log ORDER BY triggered_at DESC LIMIT 10;
  id  |         triggered_at          | llm_provider |         llm_model         |    llm_tier     | total_tokens | scout_total_tokens | fallback_attempts
------+-------------------------------+--------------+---------------------------+-----------------+--------------+--------------------+-------------------
 1362 | 2026-07-12 18:17:49.130784+00 | google       | gemini-2.5-flash-lite     | scout           |         1890 |                    |
 1361 | 2026-07-12 18:17:30.606079+00 | google       | gemini-2.5-flash-lite     | scout           |         1875 |                    |
 1360 | 2026-07-12 18:16:59.120216+00 | anthropic    | claude-haiku-4-5-20251001 | scout_escalated |         3492 |                    |
 1359 | 2026-07-12 18:16:44.877578+00 | google       | gemini-2.5-flash-lite     | scout           |         1914 |                    |
 1357 | 2026-07-12 18:15:15.913355+00 | google       | gemini-2.5-flash-lite     | scout           |         1950 |                    |
 1358 | 2026-07-12 18:14:12.490886+00 | google       | gemini-2.5-flash-lite     | scout           |         1834 |                    |
 1355 | 2026-07-12 18:00:42.093992+00 | anthropic    | claude-haiku-4-5-20251001 |                 |         3837 |                    |
 1354 | 2026-07-12 18:00:42.071944+00 | zhipu        | glm-4.5                   |                 |         3184 |                    |
 1356 | 2026-07-12 18:00:42.01859+00  | zhipu        | glm-4.5                   |                 |         2786 |                    |
 1353 | 2026-07-12 18:00:41.984193+00 | cerebras     | gpt-oss-120b              |                 |         2241 |                    |
(10 rows)
```

Token placement is as specified: scout-final rows carry scout usage in the
main columns with scout columns NULL; row 1360's `scout_total_tokens` is NULL
because the scout's 503 consumed no tokens (no usage returned).

## Deviations from spec

1. **Scout token columns on a failed-but-billed scout**: spec says scout
   columns populate "only when BOTH tiers run". A scout that fails at
   parse-time still consumed tokens; those are recorded in the scout columns
   (tier `scout_escalated`) so spend is never silently dropped. A scout that
   fails with an exception before a response (like the live 503) records
   nothing — no usage exists.
2. **`first_cycle` trigger added** (not in the spec's candidate list) —
   rationale under "Deterministic triggers".
3. **Tier precedence**: if a scout escalated AND a fallback model served the
   premium chain, the row is `scout_escalated` (not `fallback`);
   `fallback_attempts` still records the failed premium attempts.
4. **`llm_provider`/`llm_model` in `ai_signal_log`** now record the model
   that actually produced the decision (relevant when a fallback served);
   previously always the configured model. Per-model usage stats therefore
   attribute spend to the model that spent it.
5. **UI scope**: scout fields were added to the strategy **edit** form only,
   not the create wizard (create → edit to enable tiering). Keeps the change
   minimal per spec's "keep the UI minimal".
6. **Chain-exhausted usage**: when the whole chain fails, the last attempt's
   token usage (if any) is kept in the main columns — preserves the
   pre-existing behavior of accounting tokens on parse failures.
7. Spec's Phase-5 query names a `created_at` column; the table's column is
   `triggered_at` — query adjusted accordingly.

No new env vars; `docker-compose.yml` and all out-of-scope services untouched.

## Open questions

- Persisting `volatility_regime` (one small jsonb/text column on
  `ai_signal_log`) would unlock the regime-change premium trigger.
- SL/TP proximity trigger needs SL/TP in `AgentState` (e.g. from the
  listener's position/order data already fetched when `use_limit_orders` is
  on) — worth revisiting if scout false-negatives near stops become a
  concern; until then `premium_force_interval` bounds the exposure window.
- The dashboard `/usage` endpoint ignores the new scout token columns;
  scout-escalated cycles under-report total spend there by the scout's
  tokens. Small today; worth folding in if scout adoption grows.
