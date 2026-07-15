# Zhipu error 1210: forced tool_choice rejected — fixed

**Date:** 2026-07-15
**Service:** ai-signal-generator
**File:** `ai-signal-generator/app/graph/llm_chain.py`

## Symptom

Every LLM call to zhipu `glm-4.5`, `glm-4.6`, `glm-4.7` failed with:

```
BadRequestError: Error code: 400 - {'error': {'code': '1210', 'message': 'API 调用参数有误，请检查文档。'}}
```

52 such failures in the last 24h of logs (glm-4.5 ×17, glm-4.5-air-as-scout ×15,
glm-4.7 ×10, glm-4.6 ×10). Only the `glm-4.5-air` fallback kept the hype/tao
strategies producing decisions. This was one of several contributors to the
AI strategies placing almost no trades since ~2026-07-13 (the others being
exhausted OpenRouter/Gemini credits and free-tier daily caps — not code bugs).

## Root cause

Zhipu stopped accepting a **forced** `tool_choice` on glm-4.5/4.6/4.7 (glm-4.5-air
still accepts it). Isolated with a live probe from inside the container:

```
glm-4.5 | tools only | OK
glm-4.5 | tools + tool_choice=auto | OK
glm-4.5 | tools + tool_choice=forced | FAIL Error code: 400 - {'error': {'code': '1210', 'message': 'API 调用参数有误，请检查文档。'}}
glm-4.5 | tools + parallel_tool_calls=False | OK
```

langchain's `with_structured_output(method='function_calling')` always sends the
forced form (`tool_choice={"type":"function","function":{"name":...}}`), so every
structured-output call to those models 400'd.

## Fix

In `_attempt()` (`llm_chain.py`), zhipu no longer goes through
`with_structured_output`. It binds the schema as a tool with `tool_choice='auto'`
and parses the tool call manually into the same `{'raw','parsed','parsing_error'}`
shape, so the rest of the chain (usage accounting, parse-failure fallback) is
untouched. `'zhipu'` removed from `_STRUCTURED_OUTPUT_METHOD`. If the model answers
conversationally instead of calling the tool, it's recorded as a parse failure and
the chain falls back exactly as before.

## Verification (live container, after `./scripts/redeploy.sh ai-signal-generator`)

Real `_attempt()` path against all four zhipu models:

```
glm-4.5 | signal: hold | usage: {'input_tokens': 551, 'output_tokens': 413, 'total_tokens': 964} | error: None
glm-4.6 | signal: hold | usage: {'input_tokens': 547, 'output_tokens': 347, 'total_tokens': 894} | error: None
glm-4.7 | signal: hold | usage: {'input_tokens': 551, 'output_tokens': 407, 'total_tokens': 958} | error: None
glm-4.5-air | signal: hold | usage: {'input_tokens': 551, 'output_tokens': 498, 'total_tokens': 1049} | error: None
```

Before the fix the same call for glm-4.5 reproduced the failure:

```
glm-4.5 FAIL BadRequestError Error code: 400 - {'error': {'code': '1210', 'message': 'API 调用参数有误，请检查文档。'}}
glm-4.5-air OK parsed: True parse_err: None
```

Container healthy after redeploy, no errors in logs:

```
NAME                         IMAGE                      COMMAND                  SERVICE               CREATED         STATUS                   PORTS
matp-ai-signal-generator-1   matp-ai-signal-generator   "uvicorn app.main:ap…"   ai-signal-generator   2 minutes ago   Up 2 minutes (healthy)   8005/tcp
```

## Not fixed here (intentionally out of scope, per user)

- OpenRouter credits exhausted (402) + free-model daily cap — needs a top-up or key change.
- Gemini prepaid credits depleted (429 RESOURCE_EXHAUSTED).
- Groq llama-3.3-70b free-tier 100k tokens/day cap.
- `bnb-ai-scalper-edbb` is in `dry_run=true`, so its open signals are suppressed by design.
