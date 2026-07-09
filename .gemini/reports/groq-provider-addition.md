# Add Groq as a fourth LLM provider

## What changed

Groq is now a first-class LLM provider alongside Anthropic, OpenAI, and Google
Gemini, wired through every place the other three already existed (per the
user's rationale: low latency, good fit for AI strategies).

1. **ai-signal-generator** — `app/config.py` (`groq_api_key`), `app/config_secrets.py`
   (`llm_key_groq` → `groq_api_key` DB override), `app/graph/nodes/node_analyze.py`
   (`_get_llm` groq branch via `langchain_groq.ChatGroq`), `app/models_registry.py`
   (`_raw_groq`/`_probe_groq`, using `groq.AsyncGroq` for model listing),
   `requirements.txt` (`langchain-groq`).
2. **strategy-tester** — same `config.py`/`config_secrets.py` additions;
   `app/pricing.py` gained a Groq pricing table (llama-3.1-8b-instant,
   llama-3.3-70b-versatile, gemma2-9b-it, deepseek-r1-distill-llama-70b) plus a
   per-provider fallback so an unrecognized Groq model doesn't silently price
   as Gemini. The `_vendored/node_analyze.py` groq branch was synced from
   ai-signal-generator via `make sync-vendored` — but only that file; an
   unrelated pre-existing drift in `_vendored/prompt_builder.py` (looks like
   the limit-orders/open-orders prompt work never got synced into
   strategy-tester) was deliberately left untouched since it's out of scope
   for this change. `CHECKSUMS` verified with `sha256sum -c`.
3. **social-listener** — `app/config.py`/`config_secrets.py` additions,
   `app/extractor.py` groq branch in `_build_llm`.
4. **dashboard-api** — `VALID_PROVIDERS` (`routes/ai.ts`) and `LLM_PROVIDERS`
   (`routes/config.ts`) both gained `'groq'`.
5. **dashboard-ui** — `Settings.tsx` (LLM key entry) and `Strategies.tsx`
   (per-strategy provider picker) both gained a Groq option.
6. **Infra** — `docker-compose.yml` adds `GROQ_API_KEY` env passthrough to
   `ai-signal-generator`, `strategy-tester`, `social-listener`; `.env.example`
   documents the new var; the real `.env` got an empty `GROQ_API_KEY=` line
   appended (inserted via `sed`, never printed/read to avoid exposing other
   secrets).

No DB migration needed — `llm_provider` columns are plain `VARCHAR(50)`, no
CHECK constraint.

## Branching note

All work was done from a `feat/desktop-ui` checkout (session default), but
Groq support is unrelated to the desktop UI initiative, so per CLAUDE.md
("routine work goes straight to main") it was moved over before committing:
confirmed every touched file was byte-identical between `main` and
`feat/desktop-ui` (`git diff main feat/desktop-ui -- <files>` empty for all
20), then `git stash` → `git checkout main` → `git stash pop` — a clean,
conflict-free move.

## Verification

Type/syntax checks, all clean:
```
$ cd dashboard-api && npx tsc --noEmit -p .   # exit 0, no output
$ cd dashboard-ui  && npx tsc --noEmit -p .   # exit 0, no output
$ python3 -c "import ast; ast.parse(...)" for all 11 touched .py files  # all OK
$ cd strategy-tester/app/_vendored && sha256sum -c CHECKSUMS
indicators.py: OK
__init__.py: OK
node_analyze.py: OK
prompt_builder.py: OK
prompt_templates.py: OK
```

All five affected services rebuilt and redeployed via `./scripts/redeploy.sh
<service>` (force-recreate), then verified against the *running* container,
not host build output:

```
$ docker compose ps ai-signal-generator dashboard-api strategy-tester dashboard-ui social-listener
NAME                          STATUS
matp-ai-signal-generator-1    Up (healthy)
matp-dashboard-api-1          Up (healthy)
matp-strategy-tester-1        Up (healthy)
matp-dashboard-ui-1           Up
matp-social-listener-1        Up

$ docker compose exec -T ai-signal-generator grep -n groq app/graph/nodes/node_analyze.py app/models_registry.py app/config.py
(groq branch present in all three)

$ docker compose exec -T dashboard-api grep -n groq dist/routes/ai.js dist/routes/config.js
dist/routes/ai.js:9:const VALID_PROVIDERS = ['google', 'openai', 'anthropic', 'groq'];
dist/routes/config.js:10:const LLM_PROVIDERS = ['anthropic', 'openai', 'gemini', 'groq'];

$ docker compose exec -T strategy-tester grep -n groq app/_vendored/node_analyze.py app/pricing.py app/config.py
(groq branch + pricing table present)

$ docker compose exec -T dashboard-ui grep -rl groq /usr/share/nginx/html/assets/
/usr/share/nginx/html/assets/index-B3ebToMQ.js
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-B3ebToMQ.js        # matches — live bundle confirmed

$ docker compose exec -T social-listener grep -n groq app/config.py app/config_secrets.py app/extractor.py
(groq branch present in all three)
```

## Not yet done / follow-ups

- No `GROQ_API_KEY` has been configured yet (env var and `config` table both
  empty) — providers stay dormant until a key is added via the Settings page
  or `.env`. Once added, restart the three consuming services (or use the
  Settings page which triggers a DB-override apply on next restart).
- Groq pricing table entries in `strategy-tester/app/pricing.py` are
  best-effort from public pricing and should be spot-checked against
  groq.com/pricing before relying on cost estimates for Groq-backed backtests.
- The pre-existing `_vendored/prompt_builder.py` drift (limit-orders prompt
  support present in ai-signal-generator but not yet synced into
  strategy-tester) was left as-is — flagging here since it's a separate,
  real gap noticed during this work, not something this change introduced.
