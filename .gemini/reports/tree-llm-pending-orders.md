# Tree page: LLM pill + pending orders display

## What changed

**dashboard-api** (`src/routes/strategies.ts`)
- `GET /strategies/tree`: joined `ai_strategy_config` for `ai_llm_provider`/`ai_llm_model`; added a
  pending-orders aggregation (`status = 'pending'`) per strategy with `price`/`sl_price`/`tp_price`,
  fanned out to order-executor's `GET /accounts/{account_id}/mark-price/{symbol}` per unique
  `(account_id, symbol)` pair for a live mark price.
- `GET /strategies/:id`: joined `ai_strategy_config` so the expanded detail panel can show LLM info.

**dashboard-ui**
- `api.ts`: `StrategyTreeItem` gained `ai_llm_provider`, `ai_llm_model`, `pending_orders: PendingOrder[]`.
- `StrategyTree.tsx`:
  - Row 2 of each card shows an LLM pill (`provider / model`) for `ai_engine` strategies.
  - Expanded (ⓘ) detail panel shows an `LLM` row.
  - New "Pending Orders" section renders under "Open Positions" inside the expanded card, one
    `PendingOrderCard` per resting order with side/symbol/price/mark/SL/TP.
  - Left accent bar gets an additional yellow band when a strategy has pending orders.
  - Row 1 dot: green if the strategy has an open position, else yellow if it only has pending
    orders (both-true case shows green only, confirmed with user).

## Verification (live stack)

Typecheck (host, pre-deploy sanity only):
```
dashboard-ui: npx tsc --noEmit  → no output, exit 0
dashboard-api: npx tsc --noEmit → no output, exit 0
```

Redeploy:
```
./scripts/redeploy.sh dashboard-api  → Up, health: starting → healthy
./scripts/redeploy.sh dashboard-ui   → new asset index-Dw9ABeFj.js
```

Health check:
```
$ docker compose exec nginx wget -qO- http://dashboard-api:8003/health
{"status":"ok","service":"dashboard-api"}
```

Live API response confirming the join + pending-order + mark-price fanout work end to end
(`GET /strategies/tree` via nginx→dashboard-api, real data from the running stack):
```json
{"id":"tao-ai-range-rotation-d257", ..., "ai_llm_model":"llama-3.3-70b-versatile","ai_llm_provider":"groq","pending_orders":[]}
{"id":"bnb-ai-scalper-edbb", ..., "ai_llm_model":"claude-sonnet-4-5-20250929","ai_llm_provider":"anthropic","pending_orders":[]}
{"id":"eth-ai-34d2", ..., "ai_llm_model":"gemini-2.5-flash","ai_llm_provider":"google",
 "pending_orders":[{
   "id":"f7539182-2e78-4775-a238-ee7e9bc7c688","symbol":"ETH-USDT","side":"buy",
   "price":1762.808661,"sl_price":1751,"tp_price":1780.0842,"mark_price":1804.7,
   "received_at":"2026-07-10T11:03:07.384Z"
 }]}
{"id":"sui-manual-59d9", ..., "ai_llm_model":null,"ai_llm_provider":null,"pending_orders":[]}
```
`eth-ai-34d2` (ETH AI Geometric Range) has a real resting order and confirms: LLM fields populated
for `ai_engine` strategies, `null` for `tradingview`; pending order carries price/sl/tp plus a live
mark price (1804.7) fetched on demand from order-executor.

Live UI bundle confirms the new strings shipped:
```
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-Dw9ABeFj.js
$ docker compose exec -T dashboard-ui grep -rl 'Pending Orders' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-Dw9ABeFj.js
$ docker compose exec -T dashboard-ui grep -rl 'LLM' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-Dw9ABeFj.js
```

Not yet done: a visual/browser pass on the ETH AI Geometric Range card (the one strategy with a live
pending order right now) to eyeball the yellow band, yellow-vs-green dot, and pending-order card
layout. Recommend a quick look at `/strategies/tree` in the browser before considering this fully
verified.
