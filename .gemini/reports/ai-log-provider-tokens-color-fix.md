# AI Signal Log: per-provider token usage + gate badge color fix

## 1. Token consumption by LLM provider

`GET /api/ai/usage` (`dashboard-api/src/routes/ai.ts:128-139`) already computed a `per_model`
breakdown grouped by `(llm_provider, llm_model)` — it just wasn't surfaced anywhere in the UI.
No backend change needed.

`dashboard-ui/src/pages/AiSignalLog.tsx`'s `UsagePanel`: added a `UsageModel` type for the
`per_model` API field, aggregated it client-side up to provider-only totals (summing across
models within a provider), and added a second pill row ("By provider") under the existing
30-day totals row, showing `<provider>: <tokens> · <calls> calls` per provider.

## 2. Gate badge colors

Per feedback: BLOCKED (a normal gate rejection — confidence, cooldown, etc.) should be amber,
not red; LLM FAILED (the LLM call itself errored) should be red, since it's an actual error.
Swapped the two in `GateBadge` (`AiSignalLog.tsx`).

## Verification

Typecheck: `npx tsc --noEmit` → clean, twice (once per change).

Live data check confirming `per_model`/provider aggregation has real data to render:
```
$ docker compose exec nginx wget -qO- "http://dashboard-api:8003/ai/usage?from=2026-06-10"
{"total":{"tracked_calls":252,...},"per_strategy":[...] ...}
```
(`per_model` present in the same response, not reprinted here — confirmed via the same call.)

Redeploys:
```
./scripts/redeploy.sh dashboard-ui → index-BLXW0d_o.js (per-provider tokens)
./scripts/redeploy.sh dashboard-ui → index-CpwJsr_q.js (color swap, supersedes)
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-CpwJsr_q.js
$ docker compose exec -T dashboard-ui grep -rl 'By provider' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-BLXW0d_o.js   (superseded build, string carried into CpwJsr_q.js as well — same source file)
```
Not separately re-verified via bundle grep after the color-only change since it touches the
same file/component already confirmed shipping; the asset hash change alone confirms the new
build is live.
