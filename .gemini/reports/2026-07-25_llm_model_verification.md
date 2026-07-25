# `llm_failed` item 2 — model verification at assignment time

**Date:** 2026-07-25
**Branch:** main
**Status:** DONE — deployed and verified live

---

## Item 2 as written was already moot

The analysis said: *repoint or disable `mistralai/mistral-medium-3-5`* (33/33 failures, all 402).
Checking before changing anything:

```
     strategy_id     | cycles |             first             |             last              | failed
---------------------+--------+-------------------------------+-------------------------------+--------
 bnb-ai-scalper-edbb |     33 | 2026-07-14 21:15:10.037213+00 | 2026-07-15 05:15:10.020185+00 |     33
```

A single 8-hour window ten days ago. Current configuration:

```
        strategy_id         | llm_provider |        llm_model
----------------------------+--------------+-------------------------
 ai-btc-6f8c                | groq         | llama-3.3-70b-versatile
 bnb-ai-scalper-edbb        | zhipu        | glm-4.5
 eth-ai-34d2                | cerebras     | zai-glm-4.7
 hype-breakout-da2e         | zhipu        | glm-4.5
 sol-ai-6486                | cerebras     | gpt-oss-120b
 tao-ai-range-rotation-d257 | zhipu        | glm-4.5
 xrp-ai-3844                | groq         | llama-3.3-70b-versatile
```

Nothing points at mistral; someone already moved `bnb-ai-scalper-edbb` to zhipu/glm-4.5. **There
was nothing to repoint.** So the fix implemented here is for the hole that let it happen, which
is still open.

---

## The hole

`PUT /ai/strategies/:id/config` validated the **shape** of `llm_fallback_chain` (provider in the
known list, non-empty model string) but never checked that the **primary** model could serve a
call. And openrouter models are deliberately never probed — the registry comment is explicit that
its 300+ model catalog makes a daily sweep slow and expensive, so openrouter models stay
unverified and are usable "as a primary or in a manual `llm_fallback_chain`, which is the intended
role."

That intended role is exactly how a model that 402s on every request became a strategy's primary
and burned 33 consecutive cycles with nothing to catch it.

A daily sweep of 300+ models and a single probe of the one model a human is assigning are
completely different cost shapes. The second is affordable.

---

## Change

**`ai-signal-generator/app/models_registry.py`**
- `_probe_openrouter()` — mirrors `_probe_cerebras` (openrouter is an OpenAI-compatible gateway).
  Deliberately **not** in `_PROBE_FNS`, so the daily sweep is unchanged.
- `verify_model(provider, model)` — one on-demand structured-output probe, checking in order:
  known provider → chat-capable id → API key present → live probe. Caches the result like any
  other probe. Returns `{ok, provider, model, reason}`.

**`ai-signal-generator/app/main.py`** — `POST /internal/models/verify`.

**`dashboard-api/src/routes/ai.ts`** — on config save, when `llm_provider`/`llm_model` is being
*changed*, call verify:
- `ok: false` → **400, save blocked**, with the reason surfaced.
- probe unreachable or timed out → **save proceeds** with a `warning` field on the response.
  Config management must not depend on LLM provider availability.
- unchanged model → no probe, no cost.

45s timeout: a live probe plus this host's cross-container latency (the homelab runs loaded
enough that internal HTTP can take many seconds).

---

## Verification

Endpoint, all four branches:

```
--- {"provider":"cerebras","model":"gpt-oss-120b"}
{"ok":true,"provider":"cerebras","model":"gpt-oss-120b","reason":""}
--- {"provider":"openrouter","model":"mistralai/mistral-medium-3-5"}
{"ok":false,...,"reason":"probe failed — model did not return a valid structured response"}
--- {"provider":"groq","model":"whisper-large-v3"}
{"ok":false,...,"reason":"not a chat model (speech/image/embedding/classifier)"}
--- {"provider":"bogus","model":"x"}
{"ok":false,...,"reason":"unknown provider (known: anthropic, cerebras, google, groq, openai, openrouter, zhipu)"}
```

Note the third: rejected on the id alone, without spending an API call — the item-1 filter doing
double duty.

Through the real config API, against a throwaway strategy id so no live strategy could be touched:

```
=== REJECT: assign the item-2 model ===
HTTP 400
{"error":"openrouter/mistralai/mistral-medium-3-5 is not usable: probe failed — model did not
 return a valid structured response",
 "hint":"Pick a model that passes verification, or fix the provider account first."}

=== REJECT: a speech model ===
HTTP 400
{"error":"groq/whisper-large-v3 is not usable: not a chat model (speech/image/embedding/classifier)",...}

=== nothing persisted? ===
0
```

Happy path — `cerebras/gpt-oss-120b` was **not** rejected: it passed verification and proceeded to
the DB write, which then failed on a foreign key because `__verify_test__` is not a real strategy:

```
{"error":"insert or update on table \"ai_strategy_config\" violates foreign key constraint
 \"ai_strategy_config_strategy_id_fkey\""}
```

That is the correct outcome for a bogus strategy id and proves the good model cleared validation.
I did not mutate a real strategy's config to see a 200.

All seven live configs unchanged after testing; the throwaway row was never created (`DELETE 0`).

---

## Honest gaps

- **The unreachable-probe path is coded but not exercised.** Taking ai-signal-generator down mid-
  session to test it wasn't worth the disruption. The logic is a `try/catch` around the fetch that
  sets `llmWarning` and falls through to the save.
- **Verification is point-in-time.** A model that verifies today can start 402ing tomorrow when an
  account runs dry — that is what the item-5 monitor is for. The two are complementary: verify
  catches configuration mistakes, the monitor catches account decay.
- **Only the primary is verified.** A manual `llm_fallback_chain` still gets shape validation only.
  Verifying each entry would mean N live probes on save; worth doing if manual chains become common.
