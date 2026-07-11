# Add Cerebras + Zhipu (GLM) LLM Providers

Date: 2026-07-11
Executor: Claude Code (Sonnet, high effort)

Both providers are OpenAI-compatible and implemented via `langchain_openai.ChatOpenAI` with a
`base_url` override — no new pip packages (`langchain-cerebras`, `zhipuai`, `langchain-community`)
were added.

## Commits

| Phase | SHA | Description |
|---|---|---|
| 1 | `eb9c543` | ai-signal-generator: add cerebras + zhipu (GLM) LLM providers |
| 2 | `0ca575c` | dashboard-api: allow cerebras + zhipu llm keys |
| 3 | `35d02e2` | dashboard-ui: add cerebras + zhipu to provider dropdowns |

## Files changed

**Phase 1 — `ai-signal-generator/`**
- `app/config.py` — added `cerebras_api_key`, `zhipu_api_key`, `zhipu_base_url` settings
- `app/config_secrets.py` — added `llm_key_cerebras`/`llm_key_zhipu` → settings-attr mapping
- `app/graph/nodes/node_analyze.py` — `_get_llm` branches for `cerebras`/`zhipu` using `ChatOpenAI` + `base_url`
- `app/models_registry.py` — `_raw_cerebras`/`_raw_zhipu` (OpenAI-compatible `/models` list, Zhipu falls back to `_ZHIPU_FALLBACK` on live-list error), `_probe_cerebras`/`_probe_zhipu` (probe the structured-output path, mirroring `_probe_groq`), registered in `_RAW_FNS`/`_PROBE_FNS`
- `.env.example` (repo root — the path in the executor prompt, `ai-signal-generator/.env.example`, does not exist in this repo) — added `CEREBRAS_API_KEY`, `ZHIPU_API_KEY`, commented `ZHIPU_BASE_URL` override
- `requirements.txt` — **unchanged**, confirmed below

**Phase 2 — `dashboard-api/`**
- `src/routes/config.ts` — extended `LLM_PROVIDERS` allowlist to include `cerebras`, `zhipu`

**Phase 3 — `dashboard-ui/`**
- `src/pages/Strategies.tsx` — added `cerebras`/`zhipu` to the `PROVIDERS` array (~line 681)
- `src/pages/Settings.tsx` — added `cerebras`/`zhipu` to the `LLM_PROVIDERS` array (~line 101)

## Verification

### Phase 1

**1. Grep the running container to prove the branches shipped:**
```
$ docker compose exec ai-signal-generator grep -n "cerebras\|zhipu" /app/app/graph/nodes/node_analyze.py /app/app/models_registry.py /app/app/config_secrets.py /app/app/config.py
/app/app/graph/nodes/node_analyze.py:63:    elif provider == 'cerebras':
/app/app/graph/nodes/node_analyze.py:68:            api_key=settings.cerebras_api_key or None,
/app/app/graph/nodes/node_analyze.py:69:            base_url="https://api.cerebras.ai/v1",
/app/app/graph/nodes/node_analyze.py:72:    elif provider == 'zhipu':
/app/app/graph/nodes/node_analyze.py:77:            api_key=settings.zhipu_api_key or None,
/app/app/graph/nodes/node_analyze.py:78:            base_url=settings.zhipu_base_url,
/app/app/models_registry.py:131:    {"id": "glm-4.5-flash", "display_name": "GLM-4.5-Flash", "provider": "zhipu"},
/app/app/models_registry.py:132:    {"id": "glm-4-flash",   "display_name": "GLM-4-Flash",   "provider": "zhipu"},
/app/app/models_registry.py:136:async def _raw_cerebras() -> list[dict]:
/app/app/models_registry.py:137:    if not settings.cerebras_api_key:
/app/app/models_registry.py:141:        client = AsyncOpenAI(api_key=settings.cerebras_api_key,
/app/app/models_registry.py:142:                             base_url="https://api.cerebras.ai/v1")
/app/app/models_registry.py:144:        return [{"id": m.id, "display_name": m.id, "provider": "cerebras"} for m in page.data]
/app/app/models_registry.py:150:async def _raw_zhipu() -> list[dict]:
/app/app/models_registry.py:151:    if not settings.zhipu_api_key:
/app/app/models_registry.py:155:        client = AsyncOpenAI(api_key=settings.zhipu_api_key,
/app/app/models_registry.py:156:                             base_url=settings.zhipu_base_url)
/app/app/models_registry.py:158:        models = [{"id": m.id, "display_name": m.id, "provider": "zhipu"} for m in page.data]
/app/app/models_registry.py:170:    "cerebras":  _raw_cerebras,
/app/app/models_registry.py:171:    "zhipu":     _raw_zhipu,
/app/app/models_registry.py:284:async def _probe_cerebras(model_id: str) -> bool:
/app/app/models_registry.py:292:                         api_key=settings.cerebras_api_key,
/app/app/models_registry.py:293:                         base_url="https://api.cerebras.ai/v1", max_retries=0)
/app/app/models_registry.py:309:async def _probe_zhipu(model_id: str) -> bool:
/app/app/models_registry.py:313:                         api_key=settings.zhipu_api_key,
/app/app/models_registry.py:314:                         base_url=settings.zhipu_base_url, max_retries=0)
/app/app/models_registry.py:335:    "cerebras":  _probe_cerebras,
/app/app/models_registry.py:336:    "zhipu":     _probe_zhipu,
/app/app/config_secrets.py:26:    "llm_key_cerebras":  "cerebras_api_key",
/app/app/config_secrets.py:27:    "llm_key_zhipu":     "zhipu_api_key",
/app/app/config.py:15:    cerebras_api_key:       str = ""
/app/app/config.py:16:    zhipu_api_key:          str = ""
/app/app/config.py:17:    zhipu_base_url:         str = "https://open.bigmodel.cn/api/paas/v4/"
```

**2. Live model-list endpoint (internal port confirmed as 8005 from `docker-compose.yml`, not 8000):**

No key set on this host for either provider, so both fired the "no key" short-circuit path
(`_raw_cerebras`/`_raw_zhipu` return `[]` before any HTTP call):
```
$ docker compose exec ai-signal-generator curl -s "http://localhost:8005/internal/models?provider=cerebras"
{"provider":"cerebras","models":[],"key_configured":false}

$ docker compose exec ai-signal-generator curl -s "http://localhost:8005/internal/models?provider=zhipu"
{"provider":"zhipu","models":[],"key_configured":false}
```
Matches the expected "no key" behavior exactly. No key was available on this host to exercise
the live-list path, so the real Zhipu `/models` response shape is not yet known — flagging this
per §7 as an open item, not assumed.

**3. Dependency drift check:**
```
$ git diff HEAD~1 -- ai-signal-generator/requirements.txt
(empty output)
```
Confirmed: no new dependency was added.

### Phase 2

```
$ docker compose exec dashboard-api curl -s -X PUT "http://localhost:8003/config/llm-keys/zhipu" \
  -H 'Content-Type: application/json' -d '{"api_key":"test-not-a-real-key"}' -w "\nHTTP_CODE:%{http_code}\n"
{"provider":"zhipu","configured":true,"updated_at":"2026-07-11T16:36:00.850Z"}
HTTP_CODE:200

$ docker compose exec dashboard-api curl -s -X PUT "http://localhost:8003/config/llm-keys/cerebras" \
  -H 'Content-Type: application/json' -d '{"api_key":"test-not-a-real-key"}' -w "\nHTTP_CODE:%{http_code}\n"
{"provider":"cerebras","configured":true,"updated_at":"2026-07-11T16:36:07.605Z"}
HTTP_CODE:200

$ docker compose exec dashboard-api curl -s -X PUT "http://localhost:8003/config/llm-keys/notreal" \
  -H 'Content-Type: application/json' -d '{"api_key":"x"}' -w "\nHTTP_CODE:%{http_code}\n"
{"error":"Provider must be one of: anthropic, openai, gemini, groq, cerebras, zhipu"}
HTTP_CODE:400
```
Both new providers return 200 (previously would have 400'd with "Provider must be one of:
anthropic, openai, gemini, groq"); an unrelated fake provider still correctly 400s and the error
message reflects the updated allowlist.

Test rows deleted immediately after:
```
$ docker compose exec postgres psql -U matp -d matp -c "DELETE FROM config WHERE key IN ('llm_key_zhipu','llm_key_cerebras') RETURNING key;"
       key
------------------
 llm_key_zhipu
 llm_key_cerebras
(2 rows)

DELETE 2
```

### Phase 3

```
$ docker compose exec dashboard-ui sh -c "grep -o '.\{0,20\}[Cc]erebras.\{0,20\}' /usr/share/nginx/html/assets/*.js"
bel:"Groq"},{value:"cerebras",label:"Cerebras"},
,label:"Groq"},{id:"cerebras",label:"Cerebras"},

$ docker compose exec dashboard-ui sh -c "grep -o '.\{0,20\}[Zz]hipu.\{0,20\}' /usr/share/nginx/html/assets/*.js"
"Cerebras"},{value:"zhipu",label:"Zhipu (GLM)
el:"Cerebras"},{id:"zhipu",label:"Zhipu (GLM)
```
Two matches each — one from `Strategies.tsx`'s `PROVIDERS` array (`value`/`label`), one from
`Settings.tsx`'s `LLM_PROVIDERS` array (`id`/`label`) — both present in the built, served bundle.

```
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-DJTT39Z_.js

$ docker compose exec dashboard-ui sh -c "grep -rl 'cerebras' /usr/share/nginx/html/assets/*.js"
/usr/share/nginx/html/assets/index-DJTT39Z_.js
```
The strings live in the same asset hash nginx is currently serving.

## Deploys

```
./scripts/redeploy.sh ai-signal-generator   # OK, health: starting → healthy
./scripts/redeploy.sh dashboard-api         # OK, health: starting → healthy
./scripts/redeploy.sh dashboard-ui          # OK — vite build was slow (~7 min) due to the
                                             # host's tight memory budget (2GB RAM, heavy swap
                                             # use during the build); completed successfully,
                                             # no errors.
```

## Notes / known risks (per executor prompt §7)

- Zhipu's free `glm-*-flash` tier carries a non-commercial clause. Not gated in code — that's
  Cristi's call, not the executor's.
- No Zhipu API key is configured on this host, so `_raw_zhipu`'s live-list path was never
  exercised — only the "no key → `[]`" short-circuit was verified. The real
  `open.bigmodel.cn/api/paas/v4/models` response shape (and whether it's OpenAI-`/models`-style
  at all) is still unconfirmed. If a key is added later, re-run
  `curl http://localhost:8005/internal/models?provider=zhipu` (from inside the
  ai-signal-generator container) and check the logs for whether `_ZHIPU_FALLBACK` fired.
- If a GLM or Cerebras model rejects tool-calling, the structured-output probe will mark it
  unverified (⚠ in UI) rather than hiding it — expected, matches existing Groq behavior.
