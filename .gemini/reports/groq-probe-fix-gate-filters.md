# Groq model-probe fix + AI Signal Log gate filters

## Investigation: why Groq models are failing

Two distinct causes, found via `docker compose logs ai-signal-generator` and
`ai_signal_log` grouped by `(strategy_id, llm_provider, llm_model)` where
`gate_rejection_reason = 'llm_failed'`:

1. **Rate limiting (majority of failures)** — `groq/llama-3.3-70b-versatile` has a 100,000
   tokens/day cap on this account, shared across every strategy using it
   (`tao-ai-range-rotation-d257` + `xrp-ai-3844`). Repeated `429 rate_limit_exceeded` once the
   shared daily budget is exhausted; recovers on its own once the window rolls forward. Not a
   code bug — an account/quota constraint. No code change made for this (would require either
   upgrading the Groq tier or spacing strategies across different models/providers, which is a
   config decision, not something to silently change).

2. **`groq/compound` is fundamentally incompatible with this app (real bug, 100% failure rate,
   not transient)** — `hype-breakout-da2e` was configured to use it; every single call failed
   with `400 'tool calling' is not supported with this model`. Root cause:
   `ai-signal-generator/app/models_registry.py`'s `_probe_groq()` (the model-picker's
   availability check) only tested a plain chat completion, not the structured-output/
   tool-calling path `node_analyze.py` actually uses for every real signal. So `compound` passed
   the probe and appeared as a normal, trustworthy option in the model picker, despite being
   unusable in production.

## Fix: probe the real capability, not a proxy for it

`models_registry.py`: `_probe_groq()` now calls `llm.with_structured_output(_ProbeSchema,
include_raw=True)` (matching `node_analyze.py`'s real usage) instead of a plain `ainvoke`, and
treats `"tool calling"` / `"tool_use_failed"` / `"does not support tool"` / `"function calling"`
in the exception message as a definitive "unavailable" (not just transient/uncertain).

**Live verification** (real Groq API calls, not mocked):
```
$ redeploy ai-signal-generator, then force a fresh probe:
POST /internal/models/verify?provider=groq&force=true

Log: "Model probe groq/groq/compound → fail"   (was "ok" under the old plain-chat probe)
Log: "Model probe groq/openai/gpt-oss-20b → ok" (a genuinely tool-calling-capable model, unaffected)
```
`GET /internal/models?provider=groq` now returns `{"id":"groq/compound","verified":false}`.
No dashboard-ui change was needed — `Strategies.tsx` already renders `verified === false` models
as "⚠ {name} (unverified)" and excludes them from auto-selected defaults (`firstVerified`
lookup); it just never had a reason to apply to `compound` before because the probe was wrong.

## AI Signal Log: split the Gate filter into Passed / Blocked / LLM Failed

Matches the gate-badge color split done in the previous session (BLOCKED=amber, LLM
FAILED=red).

- `dashboard-api/src/routes/ai.ts` `/signals`: added a `gate` query param (`passed` / `blocked`
  / `llm_failed`), building the appropriate `gate_passed` + `gate_rejection_reason` SQL
  condition. Old `gate_passed=true/false` param still works unchanged for any other caller.
- `dashboard-ui/src/pages/AiSignalLog.tsx`: the Gate `<FilterSelect>` now has three real options
  (`passed` / `blocked` / `llm_failed`) instead of the boolean true/false pair; `fetchRows` sends
  `gate=...` instead of `gate_passed=...`.

## Verification

Typecheck: dashboard-api and dashboard-ui `npx tsc --noEmit` → both clean.

Redeploys: `dashboard-api` (healthy), `dashboard-ui` (asset `index-DYBsMVQC.js`, confirmed live
via `curl localhost/`).

Live filter check against real data:
```
GET /ai/signals?gate=llm_failed&limit=3 → total: 157, rows all gate_passed=false,
  gate_rejection_reason='llm_failed' (groq rows shown)
GET /ai/signals?gate=blocked&limit=3    → total: 669, rows all gate_passed=false,
  gate_rejection_reason='hold_or_adjust' etc. — llm_failed correctly excluded
```
