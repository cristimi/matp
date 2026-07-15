# Zhipu key knocked out by auth-misclassified server 500 — recovery + classifier fix

Date: 2026-07-15

## What the user saw

"The multi-key implementation broke the zhipu API key" — every zhipu cycle since
~04:01 failed with `OpenAIError: Missing credentials`.

## Root cause

The key was never broken or lost. At 04:01 Zhipu returned a **transient server
500**, and its error-id (a timestamp) happened to contain the digits `401`:

```
dead_reason: "InternalServerError: Error code: 500 - {'error': {'code': '1234',
'message': '网络错误，错误id：20260715040130d17f55d03efd418e，请稍后重试'}}"
                          ^^^^-- '401' substring inside the error-id timestamp
```

(网络错误…请稍后重试 = "network error… please retry later".)

`classify_llm_error()` did bare substring matching (`'401' in text`), classified
the 500 as an **auth failure**, and `report_auth_failed()` marked the key dead
in memory. From then on `acquire('zhipu')` returned `None` — the only zhipu key
was dead — so every attempt ran with `api_key=None`:

```
04:00:44 LLM attempt [zhipu/glm-4.5] key=none failed (other): OpenAIError: Missing credentials...
04:00:44 node_analyze LLM chain exhausted (4 attempt(s)): [zhipu/glm-4.5] ... [zhipu/glm-4.7] ...
```

By design the dead flag is **process-memory only** (a transient 403 must not
permanently kill a key) — the encrypted key row in `llm_keys` (id 3) was
untouched the whole time.

## Recovery (immediate)

One reload cleared the dead flag:

```
POST /internal/llm-keys/reload
→ {"status":"ok","keys_per_provider":{... "zhipu":1}}
GET /internal/llm-keys/status → zhipu:
[{'id': 3, 'label': 'migrated', 'state': 'active', 'cooldown_remaining_s': None, 'dead_reason': None}]
```

## Fix (so it can't recur)

`ai-signal-generator/app/graph/llm_chain.py` — `classify_llm_error()` now:

1. Checks the SDK exception **class name** first (`RateLimitError` →
   rate_limit; `AuthenticationError`/`PermissionDeniedError` → auth).
2. Checks the `status_code` attribute (openai/anthropic `APIStatusError`):
   429 → rate_limit, 401/403 → auth, **≥500 → other** (server fault is never
   the key's fault, whatever the body says).
3. Only then falls back to text matching, where bare `401|403|429` must be
   **standalone** (`(?<![0-9A-Za-z])(401|403|429)(?![0-9A-Za-z])`) — digits
   embedded in request ids/timestamps no longer match. Phrase markers
   ('rate limit', 'invalid api key', …) unchanged.

## Verification

New regression tests (the first uses the exact live Zhipu error string):

- `test_server_error_with_401_in_request_id_is_not_auth`
- `test_standalone_status_codes_still_classified`
- `test_sdk_exception_class_names_classified`
- `test_status_code_attribute_beats_message_text`

```
docker compose run ... python -m pytest tests/test_llm_chain.py tests/test_tiering.py -q
30 passed, 1 warning in 2.32s
```

Redeployed (`./scripts/redeploy.sh ai-signal-generator`), container healthy,
zhipu key active after restart. Live structured-output call through the
production rotation path (`_attempt_with_keys('zhipu', 'glm-4.5-air', ...)`)
inside the running container:

```
key_label: migrated
signal: {'action': 'hold', 'confidence': 0.6, ..., 'reasoning': 'Market conditions are neutral and indecisive...'}
usage: {'input_tokens': 572, 'output_tokens': 397, 'total_tokens': 969}
error: None
```

The zhipu key is fully functional. ROADMAP "Known Issues Fixed" updated.

## Note

`gemini` (id 6) is currently in a genuine rate-limit cooldown (~52 min,
escalated) — unrelated to this bug; it recovers automatically.
