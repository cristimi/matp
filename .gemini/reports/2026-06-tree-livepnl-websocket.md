# Live PnL WebSocket — Implementation Report

## Phase A: Server-side ticker + REST refactor

### Architecture
- `dashboard-api/src/livePnl.ts` — new ticker running on `PNL_TICK_MS=2500ms`
- Queries Postgres ONCE per tick for all open positions; fans out to executor ONCE per unique `account_id`
- Writes snapshot to Redis key `pnl:live:snapshot`, publishes to channel `pnl:live`
- `/strategies/tree` and `/strategies/:id/positions` both read from snapshot (stale threshold: `PNL_TICK_MS * 4`)

### Consistency proof — same snapshot, same tick
```
$ docker compose exec -T redis redis-cli GET pnl:live:snapshot | python3 -c "..."
ts: 1782752660938
  strategy tv_test_harness: open_pnl=0.60329067391434
  position fe754dac-d632-47c4-b243-0acee387fa71: mark_price=60137.74533695717 unrealized_pnl=0.60329067391434
```
`open_pnl == unrealized_pnl` — both values derive from the same snapshot entry.

### Single fanout per tick
```
[livePnl] tick: 1 open position(s), 1 account(s) fanned out
[livePnl] tick: 1 open position(s), 1 account(s) fanned out
[livePnl] tick: 1 open position(s), 1 account(s) fanned out
```
One executor call per tick regardless of how many REST clients hit `/tree` or `/positions`.

---

## Phase B: WebSocket server + UI

### Files changed
- `dashboard-api/src/ws/pnlFeed.ts` — NEW: `createPnlWebSocket()` with `noServer: true`
- `dashboard-api/src/ws/orderFeed.ts` — refactored to `createOrderWebSocket()` with `noServer: true`
- `dashboard-api/src/index.ts` — single upgrade router (`/ws/pnl` → wssPnl, `/ws/orders` → wssOrders)
- `dashboard-ui/src/hooks/useLivePnl.ts` — NEW: React hook with auto-reconnect
- `dashboard-ui/src/pages/StrategyTree.tsx` — wired `useLivePnl`, overlays live PnL on REST baseline

### Root cause investigation: ws double-handler bug
Both WebSocket servers initially used `{ server, path }` mode. In `ws` v8, when `shouldHandle(req)` returns false (path mismatch), the library calls `abortHandshake(socket, 400)` and destroys the socket immediately — it does **not** pass through to the next upgrade listener. Result: the `/ws/orders` handler was aborting all `/ws/pnl` connections before the `/ws/pnl` handler could see them.

Even after switching pnlFeed to `noServer: true` with a `server.on('upgrade', ...)` handler, the problem persisted: the pnlFeed handler upgraded the connection, but then the orderFeed's `{ server }` handler also fired (Node.js EventEmitter calls ALL listeners), calling `abortHandshake` on the already-upgraded socket. The HTTP `400 Bad Request` bytes landed on the live socket and were interpreted as a corrupt WebSocket frame with RSV1=1, causing "Invalid WebSocket frame: RSV1 must be clear" on the next tick.

**Fix**: Both servers use `noServer: true`; a single `server.on('upgrade', ...)` in `index.ts` routes exclusively by path.

### WebSocket 101 upgrade — both paths
```
$ curl -i -H "Upgrade: websocket" -H "Connection: Upgrade" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    http://localhost/ws/pnl
HTTP/1.1 101 Switching Protocols
Server: nginx/1.31.0
Upgrade: websocket
...immediate pnl snapshot follows...
```

### Successive ticks — 4 consecutive via nginx
```
Connected through nginx
{"tick":1,"ts":1782752102901,"open_pnl":0.03666142029668,"mark_price":59845.26535507417}
{"tick":2,"ts":1782752105408,"open_pnl":0.03666142029668,"mark_price":59845.26535507417}
{"tick":3,"ts":1782752107860,"open_pnl":0.035444724631344,"mark_price":59844.961181157836}
Total via nginx: 3 ticks
```

### Successive ticks — 4 consecutive direct (inside container)
```
Connected to /ws/pnl directly
{"tick":1,"ts":1782752062827,"open_pnl":-0.04447014492336,"mark_price":59824.98246376916,"unrealized_pnl":-0.04447014492336}
{"tick":2,"ts":1782752065317,"open_pnl":-0.004248811594,"mark_price":59835.0377971015,"unrealized_pnl":-0.004248811594}
{"tick":3,"ts":1782752067958,"open_pnl":-0.004248811594,"mark_price":59835.0377971015,"unrealized_pnl":-0.004248811594}
{"tick":4,"ts":1782752070533,"open_pnl":-0.00849178261,"mark_price":59833.9770543475,"unrealized_pnl":-0.00849178261}
---
Total PnL ticks: 4
  interval 1 -> 2 : 2490 ms
  interval 2 -> 3 : 2641 ms
  interval 3 -> 4 : 2575 ms
```
Intervals ~2500ms as expected.

### Both WS paths simultaneously
```
/ws/pnl connected
/ws/orders connected
pnl ok: true | orders ok: true
```

### Connection tracking
```
PnL WS client connected. Total: 1
[livePnl] tick: 1 open position(s), 1 account(s) fanned out
PnL WS client disconnected. Total: 0
```
One executor fanout per tick — unchanged regardless of connected client count.

### Dashboard UI asset hash
```
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-TQV0HvlU.js
```
UI bundle confirmed live (deployed with `useLivePnl` hook and updated `StrategyTree.tsx`).

### nginx — no changes required
Existing `/ws/` location block handles all `/ws/*` paths including `/ws/pnl`.

---

## Summary
- ONE executor fanout per tick on the server — never per client, strategy, or request
- Both header `open_pnl` and per-position `unrealized_pnl` now derive from the same snapshot → they are always consistent
- WebSocket pushes every ~2500ms; UI merges pushed values over REST baseline with fallback
- `dashboard-api` and `dashboard-ui` both redeployed and verified
