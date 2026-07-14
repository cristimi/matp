# OpenRouter as an LLM provider

Date: 2026-07-14

## What changed

- **ai-signal-generator**
  - `config.py`: `openrouter_api_key` / `openrouter_base_url` (default `https://openrouter.ai/api/v1`).
  - `key_pool.py`: `openrouter` slug registered (multi-key + rotation work like any provider).
  - `llm_chain.py`: `_get_llm` branch (OpenAI-compatible via `ChatOpenAI` + base_url);
    `function_calling` structured-output method like the other OpenAI-compat gateways.
  - `models_registry.py`: `_raw_openrouter` uses OpenRouter's native `/models` metadata and
    keeps only tool-calling-capable models (`supported_parameters` contains `tools`), since
    every cycle needs structured output. **Deliberately no probe fn**: the catalog is 300+
    models and probing each with a live generation daily would be slow and spend real money
    on paid ones. Consequence (documented in code): openrouter models always show unverified
    (⚠) and never enter auto-derived fallback chains — usable as a primary or in a manual
    `llm_fallback_chain`.
- **dashboard-api**: `openrouter` added to `config.ts` LLM_PROVIDERS (key CRUD) and
  `ai.ts` VALID_PROVIDERS (strategy config validation).
- **dashboard-ui**: OpenRouter in Settings key list and Strategies provider dropdown.
- strategy-tester/social-listener: not added (same as cerebras/zhipu — their vendored
  LLM paths don't support it).

## Bug found & fixed while verifying: deleted key leaked back as phantom env key

`key_pool.load()` used to sync the top-priority key into `settings.<provider>_api_key`,
and the env-fallback read settings live. Deleting a provider's **last** DB key then
resurrected the deleted key as a phantom `env` entry (observed live: pool still showed
`openrouter: 1` after DELETE). Fix: env-var defaults are snapshotted once at first
`load()`, the fallback reads only the snapshot, and the settings sync (which nothing in
this service reads anymore) was removed. Regression test added
(`test_deleted_db_key_does_not_leak_back_via_env`).

## Verification (live outputs)

No key configured:

```
GET /internal/models?provider=openrouter → {"provider":"openrouter","models":[],"key_configured":false}
GET /api/dashboard/config/llm-keys → openrouter present: True []
```

With a key (catalog listing is public, no LLM calls, zero cost — fake key used):

```
key_configured: True | models: 267
sample: ['kwaipilot/kat-coder-air-v2.5', 'kwaipilot/kat-coder-pro-v2.5', 'openai/gpt-5.6-luna-pro', ...]
all unverified: True
```

Delete round-trip after the leak fix (pool drops openrouter entirely):

```
18:13:54 key_pool: loaded {... 'openrouter': 1, 'zhipu': 1}   (fake key added)
18:14:00 key_pool: loaded {'anthropic': 1, 'cerebras': 1, 'gemini': 1, 'groq': 1, 'openai': 1, 'zhipu': 1}
GET /internal/models?provider=openrouter → {"provider":"openrouter","models":[],"key_configured":false}
```

Tests in the built image: `26 passed` (test_llm_chain + test_tiering, incl. new regression test).
Served UI asset `index-E_Ir1Uir.js` contains the OpenRouter entries. ai-signal-generator,
dashboard-api, dashboard-ui redeployed and healthy.

## Usage

Add an OpenRouter key in Settings → LLM Provider Keys → OpenRouter, then pick
provider "OpenRouter" in a strategy's LLM config; the model dropdown lists all
tool-capable OpenRouter models (unverified ⚠ by design).

## Addendum (2026-07-14, later): real-key live test + mobile Save button fix

User added their OpenRouter key via the Settings UI (id 10, label "Matp"); pool
hot-reloaded it. Live structured-output test through the production
`call_llm_chain` path with three free-tier candidates exercised the full machinery
in one run:

```
attempt 1 [openrouter/tencent/hy3:free]              → structured-output parse failed → chain fallback
attempt 2 [openrouter/google/gemma-4-31b-it:free]    → real 429 (upstream) → classified rate_limit,
                                                       key_pool: openrouter/Matp cooldown 60s
attempt 3 [openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free] → SERVED
served_by: {'provider': 'openrouter', 'model': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free', 'key_label': 'Matp'}
signal:   {'action': 'hold', 'confidence': 0.5, 'reasoning': 'Market is in a consolidation phase...'}
usage:    {'input_tokens': 853, 'output_tokens': 2230, 'total_tokens': 3083}
```

Cooldown expired on schedule; key back to `active` in /internal/llm-keys/status.

Mobile bug (reported by user): the add-key form put label + key + Save/Cancel in one
flex row, overflowing on phones — Save was off-screen. Fixed: form stacks vertically
below the `sm:` breakpoint (`flex-col sm:flex-row`, inputs `w-full` on mobile).
Deployed as asset `index-B3qceSM0.js` (verified live via curl + grep in the served
container).
