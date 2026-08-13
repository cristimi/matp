# Zhipu balance exhausted — switch to the free GLM Flash models

**Date:** 2026-08-13
**Service:** `ai-signal-generator`
**Trigger:** every zhipu cycle failing since at least 2026-08-12 21:00.

## 1. What was actually wrong

Not a rate limit that resets. The account has **no balance and no resource package**.

```
2026-08-13 07:01:01,272 [WARNING] app.graph.llm_chain: LLM attempt [zhipu/glm-4.5] key=migrated failed (rate_limit): RateLimitError: Error code: 429 - {'error': {'code': '1113', 'message': '余额不足或无可用资源包,请充值。'}}
2026-08-13 07:01:18,771 [ERROR] app.graph.nodes.node_analyze: node_analyze LLM chain exhausted (4 attempt(s)): [zhipu/glm-4.5] ...1113...; [zhipu/glm-4.5-air] ...1113...; [zhipu/glm-4.6] ...1113...; [zhipu/glm-4.7] ...1113...
```

Error **1113** = *"insufficient balance or no available resource package, please top up"*. It is
delivered as HTTP 429, which is why it looked like throttling in the logs and in
`llm_health_monitor`. It will not clear with time.

Blast radius before the fix — 100 % failure on both live zhipu strategies:

```
$ curl -s ai-signal-generator:8005/internal/llm-health/status
 "bnb-ai-scalper-edbb":        {"cycles": 25, "failed": 25, "failed_pct": 100.0, "state": "degraded", "model": "glm-4.5"}
 "tao-ai-range-rotation-d257": {"cycles": 24, "failed": 24, "failed_pct": 100.0, "state": "degraded", "model": "glm-4.5"}
```

`hype-breakout-da2e` was also pinned to `glm-4.5`, but is currently `enabled = f`.

## 2. The free models still work on the same key

Probed with the account's stored key, inside the container:

```
glm-4.7-flash    OK   -> '{"action":"hold","confidence":0.5}'
glm-4.5-flash    OK   -> '{"action":"hold","confidence":0.5}'
glm-4-flash      OK   -> 'OK'
glm-4.6          FAIL -> RateLimitError: 429 - {'code': '1113', 'message': '余额不足或无可用资源包,请充值。'}
glm-5            FAIL -> RateLimitError: 429 - {'code': '1113', 'message': '余额不足或无可用资源包,请充值。'}
```

`glm-4.5-flash` was retired 2026-01-30 and is server-side aliased to `glm-4.7-flash`, so only
`glm-4.7-flash` and `glm-4-flash` are worth listing.

### Why they never showed up in the model picker

Zhipu's `/models` endpoint returns **only the paid tier** — it never enumerates the free Flash
models, even though they answer normally:

```
models.list -> ['glm-4.5', 'glm-4.5-air', 'glm-4.6', 'glm-4.7', 'glm-5', 'glm-5-turbo', 'glm-5.1', 'glm-5.2']
```

`_raw_zhipu()` used that list verbatim, with `_ZHIPU_FALLBACK` reachable only if the *listing
call itself* failed. A listing that succeeds and returns eight unpayable models is exactly the
case that was not covered.

### Thinking mode breaks the tool call

`glm-4.7-flash` defaults to thinking mode. Same prompt, through `_probe_zhipu`'s structured path:

```
glm-4.7-flash [plain]    parsed=None                    | 129 output tokens
glm-4.7-flash [no-think] parsed=_ProbeSchema(ok=True)    |  16 output tokens
glm-4-flash   [plain]    parsed=_ProbeSchema(ok=True)
glm-4-flash   [no-think] parsed=_ProbeSchema(ok=True)
```

On the live `bind_tools(tool_choice='auto')` path thinking-on did produce tool calls (6/6), but
at **10.6 s and 602 output tokens per call** versus ~16 tokens with thinking off — uncomfortably
close to `_PROBE_TIMEOUT` (15 s), and slower on an endpoint that already throttles. Thinking is
therefore disabled for all zhipu calls, in the probe and the live chain alike, so a model cannot
pass one path and fail the other. It is one constant (`_ZHIPU_NO_THINKING`) to flip back.

### Free tier is capacity-throttled

Roughly half of all free-model calls come back as error **1305** —
`该模型当前访问量过大，请您稍后再试` ("this model is busy, try again later"). That one *is* transient.
`key_pool.acquire()` falls back to the least-cooled key when every key is cooling, so the single
zhipu key is never locked out by it.

## 3. Changes

**`ai-signal-generator/app/models_registry.py`**

- `_ZHIPU_FALLBACK` → `_ZHIPU_FREE`, now **merged into** the listing result instead of replacing
  it only on listing failure.
- New `zhipu_chat_kwargs()` — shared thinking-disabled kwargs for probe + live chain.
- `_probe_zhipu()` passes those kwargs, and now treats `'1113'` as **definitively unavailable**.
  Previously a 1113 landed in the "transient/uncertain" branch and returned `True`, so unpayable
  models stayed cached as `ok` and ate one of the three fallback slots every cycle. The cache TTL
  re-probes, so topping the account up still self-heals.

**`ai-signal-generator/app/graph/llm_chain.py`**

- `_get_llm()` for zhipu passes `models_registry.zhipu_chat_kwargs()`.

**DB** — all three zhipu strategies repointed:

```sql
UPDATE ai_strategy_config
   SET llm_model = 'glm-4.7-flash',
       llm_scout_model = CASE WHEN llm_scout_provider = 'zhipu' THEN 'glm-4-flash' ELSE llm_scout_model END
 WHERE llm_provider = 'zhipu';
UPDATE 3
```

```
        strategy_id         | llm_provider |    llm_model    | llm_scout_provider | llm_scout_model
----------------------------+--------------+-----------------+--------------------+-----------------
 bnb-ai-scalper-edbb        | zhipu        | glm-4.7-flash   | zhipu              | glm-4-flash
 hype-breakout-da2e         | zhipu        | glm-4.7-flash   |                    |
 tao-ai-range-rotation-d257 | zhipu        | glm-4.7-flash   | zhipu              | glm-4-flash
```

## 4. Verification against the running container

Free models are now offered by the registry, verified-first, and the eight unpayable ones are
correctly demoted (they used to cache as `ok`):

```
$ docker compose exec -T nginx wget -qO- 'http://ai-signal-generator:8005/internal/models?provider=zhipu'
glm-4.7-flash    GLM-4.7-Flash (free)   verified=True
glm-4-flash      GLM-4-Flash (free)     verified=True
glm-4.5          glm-4.5                verified=False
glm-4.5-air      glm-4.5-air            verified=False
glm-4.6          glm-4.6                verified=False
glm-4.7          glm-4.7                verified=False
glm-5            glm-5                  verified=False
glm-5-turbo      glm-5-turbo            verified=False
glm-5.1          glm-5.1                verified=False
glm-5.2          glm-5.2                verified=False
```

Startup probe agrees:

```
2026-08-13 07:50:45,266 [INFO] app.models_registry: Model probe zhipu/glm-4.7-flash → ok
2026-08-13 07:50:46,784 [INFO] app.models_registry: Model probe zhipu/glm-4-flash → ok
2026-08-13 07:50:46,786 [INFO] app.main: Model probe complete: {... 'zhipu': {'passed': 2, 'failed': 8, 'skipped': 0}}
```

Both previously-dead strategies were force-triggered and produced real signals:

```
2026-08-13 07:48:59,846 [WARNING] node_analyze: Scout [zhipu/glm-4-flash] failed strategy=bnb-ai-scalper-edbb — escalating to premium: structured-output parse failed: 3 validation errors for LLMSignalOutput
2026-08-13 07:49:01,807 [INFO]    node_analyze: Scout [zhipu/glm-4-flash] proposed action=open_short strategy=tao-ai-range-rotation-d257 — escalating to premium (scout output is never executed)
2026-08-13 07:49:04,836 [INFO]    node_analyze: LLM [zhipu/glm-4.7-flash] tier=scout_escalated → action=hold confidence=0.550 tokens=3152
2026-08-13 07:49:09,641 [INFO]    node_analyze: LLM [zhipu/glm-4.7-flash] tier=scout_escalated → action=hold confidence=0.550 tokens=3089
```

Unit tests:

```
$ docker compose exec -T ai-signal-generator python -m pytest /app/tests -q
129 passed, 2 warnings in 26.33s
```

## 5. Known rough edges (not fixed here)

- `glm-4-flash` as scout sometimes returns a tool call that fails `LLMSignalOutput` validation
  ("3 validation errors" above). The designed escalation to premium absorbs it, so the cycle
  still decides — but the scout call is wasted when it happens.
- `_probe_zhipu()` probes with a **forced** `tool_choice` (`with_structured_output(method=
  "function_calling")`) while the live chain binds with `tool_choice='auto'`. The two paths can
  disagree about the same model. Worth aligning, separately.
- Cost of these strategies is now zero, but so is the priority: ~50 % of free-tier calls return
  1305 and get retried or escalated.
