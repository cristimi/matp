# AI Signal Log — "Missing Inputs" feature

## Request

Following the BNB AI scalper investigation (see `BNB_AI_SCALPER_MISSING_INPUTS.md`), the user
asked whether the AI signal log could report — per cycle — which inputs were *expected*
(enabled via `ai_strategy_config.use_*` flags) but ended up *absent* from the actual prompt,
distinct from `data_sources_used` (which only ever reported what was requested, not what
succeeded). Requested to be visible only when a log row is expanded.

## Implementation

1. **`db/migrations/052_ai_signal_log_missing_inputs.sql`** — adds
   `ai_signal_log.missing_inputs text[]`. Applied live via
   `docker compose exec -T postgres psql -U matp -d matp < db/migrations/052_...sql`.

2. **`ai-signal-generator/app/graph/nodes/node_dispatch.py`** — new
   `_MISSING_INPUT_CHECKS` table and `_missing_inputs(sc, state)` function. For each enabled
   `use_*` flag, checks whether the corresponding ingest-state field (`orderbook_data`,
   `cvd_data`, `technical_indicators`, `sentiment_data['fear_greed']`, etc.) ended up empty,
   and returns the list of source labels that came back empty. Deliberately excludes
   `use_limit_orders`/`open_orders` — `node_ingest` sets that to `[]` both on fetch failure
   and on a genuine zero-open-orders result, so it isn't a reliable "missing" signal. Wired
   into the existing `ai_signal_log` INSERT as a 20th bound parameter.

3. **`dashboard-api`** — no code change needed; both signal-listing routes already
   `SELECT * FROM ai_signal_log` and `formatSignal()` spreads `...row`, so the new column
   flows through automatically.

4. **`dashboard-ui/src/pages/AiSignalLog.tsx`** — added `missing_inputs` to the `AiSignalRow`
   type and a new "Missing Inputs" pill block, styled with the existing yellow/warning
   palette (matching `GateBadge`'s "BLOCKED" color), placed after the existing "Data sources"
   block inside the row's `{expanded && (...)}` section — so it only renders when the card is
   expanded, and only when the array is non-empty.

## Verification

Applied the migration, redeployed `ai-signal-generator` and `dashboard-ui`
(`./scripts/redeploy.sh ai-signal-generator` / `dashboard-ui`).

**DB layer** — `ai_signal_log` id 1099 (bnb-ai-scalper-edbb, 2026-07-11T11:37:49Z):
```
missing_inputs:     {technical,fear_greed,funding_rate,economic_calendar,liquidations}
data_sources_used:  {technical,fear_greed,funding_rate,open_interest,news,
                      economic_calendar,orderbook,cvd,funding_history,liquidations}
```
5 of the 10 requested sources came back empty this cycle (genuine transient fetch failures —
CoinGecko news errors, a blofin candle timeout, an okx/binance OI failure — visible in
`docker compose logs ai-signal-generator` around the same timestamp). `orderbook` and `cvd`,
which *did* resolve this cycle, are correctly absent from `missing_inputs` while still present
in `data_sources_used` — confirming the two lists diverge exactly as intended.

**API layer** — confirmed via:
```
docker compose exec nginx wget -qO- "http://dashboard-api:8003/ai/signals?strategy_id=bnb-ai-scalper-edbb&limit=1"
```
Response JSON includes `"missing_inputs": ["technical","fear_greed","funding_rate","economic_calendar","liquidations"]`,
matching the DB row exactly — no dashboard-api code changes were needed.

**UI layer** — confirmed the new bundle is live and contains the feature:
```
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-BVzCngu3.js
$ docker compose exec -T dashboard-ui grep -rl "Missing Inputs" /usr/share/nginx/html
/usr/share/nginx/html/assets/index-BVzCngu3.js
```

## Scope note (unrelated finding, surfaced during verification)

During this verification cycle, several LLM provider calls failed with rate/billing errors
unrelated to this change:
- `google/gemini-2.5-flash`: `RESOURCE_EXHAUSTED` — "Your project has exceeded its monthly
  spending cap."
- `groq/llama-3.3-70b-versatile`: `RateLimitError` — daily token limit (100000 TPD) reached.

These caused `llm_failed` gate rejections on several *other* strategies during this window
(`hype-breakout-da2e`, `sol-ai-6486`, `ai-btc-6f8c`, `eth-ai-34d2`) before falling back to
`anthropic/claude-sonnet-4-5` for `bnb-ai-scalper-edbb`. Not addressed here — flagging since
it will keep causing `llm_failed` rows until the Google spend cap is raised or the Groq daily
quota resets.
