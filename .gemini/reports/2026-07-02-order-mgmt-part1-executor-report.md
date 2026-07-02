# Active Order Management — Part 1: order-executor primitives

Scope: `order-executor` only (per prompt). Both phases complete and verified live against
Blofin demo (`blofin-blofin-demo-v5vr`) and Hyperliquid testnet (`hyperliquid-hyperliquid-hqdy`).

## Summary of changes

- `app/adapters/base.py`: added abstract `get_open_orders(symbol=None)` and
  `amend_order(symbol, order_id, new_price=None, new_size=None)`.
- `app/adapters/blofin.py`:
  - `get_open_orders`: lists `/api/v1/trade/orders-pending`, maps contracts→base units.
  - `submit_order`: limit/resting branch now queries `_get_order_state` (new helper — checks
    orders-pending first, falls back to order history) and returns `status="pending"` for a
    resting/partially-filled order, `"filled"` only once it's actually gone from the pending
    book. Market branch is untouched.
  - `amend_order`: Blofin has **no native amend endpoint** for perpetual orders (see Phase 2
    findings below) — implemented as cancel-then-replace, with documented failure semantics.
  - `cancel_order`: verified unchanged against a regular resting limit order — works via its
    existing cancel-tpsl→cancel-order fallback.
- `app/adapters/hyperliquid.py`:
  - `get_open_orders`: uses `frontendOpenOrders`, filters out `isTrigger` entries.
  - `_place_order`: now reads the already-parsed `first.get("resting")` vs `first.get("filled")`
    and returns `status="pending"` for a resting order instead of hardcoded `"filled"`. Market
    orders (IOC) always come back with `filled` or an error, so this path is unaffected.
  - `amend_order`: uses HL's native `modify` action (full replacement order spec, preserving
    side/reduceOnly from the existing order).
  - `cancel_order`: hardened to `int(oid)` so it accepts a string oid from a JSON request body
    (previously only worked when the caller already passed an int, e.g. from
    `list_trigger_orders`).
- `app/main.py`: added `GET /accounts/{account_id}/orders`,
  `POST /accounts/{account_id}/orders/cancel`, `POST /accounts/{account_id}/orders/amend`.
  All follow the existing never-raising `{"success": False, ...}` convention.

Full unit test suite still passes: `10 passed in 22.29s`.

---

## Phase 1 — Query open orders + correct limit fill status

### Blofin: resting limit order now returns `status="pending"`

Placed a limit order far from mark (mark was ~61626):

```
$ docker compose exec -T order-executor curl -s -X POST "http://localhost:8004/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "probe-blofin-2",
    "account_id": "blofin-blofin-demo-v5vr",
    "symbol": "BTC-USDT",
    "side": "buy",
    "signal": "open_long",
    "order_type": "limit",
    "size": "0.001",
    "price": "31000",
    "leverage": 5,
    "margin_mode": "isolated"
  }'

{"success":true,"exchange_order_id":"1000131530593","status":"pending","error_msg":null,
 "raw_response":{"code":"0","msg":"","data":[{"orderId":"1000131530593","clientOrderId":"",
 "msg":"Order placed","code":"0"}]},"actual_fill_price":null,"actual_fill_size":null,
 "realized_pnl":null}
```

(Before the fix, the identical call — order `1000131530284` at price 30000 — returned
`"status":"filled"`, confirming the bug was live.)

### Hyperliquid: resting limit order now returns `status="pending"`

Placed on ETH-USDT (BTC-USDT already had an open position at a different leverage, which
blocks `updateLeverage`; used ETH to avoid that unrelated conflict):

```
$ docker compose exec -T order-executor curl -s -X POST "http://localhost:8004/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "probe-hl-4",
    "account_id": "hyperliquid-hyperliquid-hqdy",
    "symbol": "ETH-USDT",
    "side": "buy",
    "signal": "open_long",
    "order_type": "limit",
    "size": "0.02",
    "price": "850",
    "leverage": 5,
    "margin_mode": "isolated"
  }'

{"success":true,"exchange_order_id":"55870927365","status":"pending","error_msg":null,
 "raw_response":{"status":"ok","response":{"type":"order","data":{"statuses":
 [{"resting":{"oid":55870927365}}]}}},"actual_fill_price":null,"actual_fill_size":null,
 "realized_pnl":null}
```

(Before the fix, the identical call — order `55870778029` — returned `"status":"filled"`
despite the raw response containing `"resting"`, confirming the bug was live.)

### `GET /accounts/{id}/orders` — normalized shape, both exchanges

```
$ docker compose exec -T order-executor curl -s \
  "http://localhost:8004/accounts/blofin-blofin-demo-v5vr/orders"

[{"order_id":"1000131530593","symbol":"BTC-USDT","side":"buy","price":31000.0,"size":0.001,
  "filled_size":0.0,"status":"resting","created_at_ms":1783009242020},
 {"order_id":"1000131530284","symbol":"BTC-USDT","side":"buy","price":30000.0,"size":0.001,
  "filled_size":0.0,"status":"resting","created_at_ms":1783008928420}]

$ docker compose exec -T order-executor curl -s \
  "http://localhost:8004/accounts/hyperliquid-hyperliquid-hqdy/orders"

[{"order_id":"55870927365","symbol":"ETH-USDT","side":"buy","price":850.0,"size":0.02,
  "filled_size":0.0,"status":"resting","created_at_ms":1783009253334},
 {"order_id":"55870778029","symbol":"ETH-USDT","side":"buy","price":800.0,"size":0.02,
  "filled_size":0.0,"status":"resting","created_at_ms":1783008997792}]
```

### Market orders unchanged — still `status="filled"`

```
$ docker compose exec -T order-executor curl -s -X POST "http://localhost:8004/execute" \
  -H "Content-Type: application/json" \
  -d '{"order_id":"probe-blofin-market-1","account_id":"blofin-blofin-demo-v5vr",
      "symbol":"BTC-USDT","side":"buy","signal":"open_long","order_type":"market",
      "size":"0.001","leverage":5,"margin_mode":"isolated"}'

{"success":true,"exchange_order_id":"1000131530613","status":"filled","error_msg":null,
 "raw_response":{...},"actual_fill_price":"61432.4",
 "actual_fill_size":"0.001000000000000000000","realized_pnl":"0"}

$ docker compose exec -T order-executor curl -s -X POST "http://localhost:8004/execute" \
  -H "Content-Type: application/json" \
  -d '{"order_id":"probe-hl-market-1","account_id":"hyperliquid-hyperliquid-hqdy",
      "symbol":"ETH-USDT","side":"buy","signal":"open_long","order_type":"market",
      "size":"0.02","leverage":5,"margin_mode":"isolated"}'

{"success":true,"exchange_order_id":"55870943053","status":"filled","error_msg":null,
 "raw_response":{"status":"ok","response":{"type":"order","data":{"statuses":
 [{"filled":{"totalSz":"0.02","avgPx":"1702.4","oid":55870943053}}]}}},
 "actual_fill_price":"1702.4","actual_fill_size":"0.02","realized_pnl":null}
```

Both test-market-orders opened a tiny position as a side effect of testing (not the resting
orders under test) — both were closed immediately via the existing `/close-position` endpoint
to keep the demo accounts tidy (confirmed filled, non-zero PnL, no impact on Phase 1's resting
orders).

**Phase 1 gate: confirmed.** Resting orders `1000131530593` (Blofin) and `55870927365` (HL)
left in place for Phase 2 as instructed.

---

## Phase 2 — Cancel + amend

### Blofin `cancel_order` verified against a regular resting limit order

`cancel_order`'s existing cancel-tpsl-first ordering was left unchanged — verified it correctly
falls through to `cancel-order` for a regular (non-TP/SL) order id:

```
$ docker compose logs order-executor --since 5m | grep -i cancel
order-executor-1 | ... HTTP Request: POST .../api/v1/trade/cancel-tpsl "HTTP/1.1 200 OK"
order-executor-1 | ... HTTP Request: POST .../api/v1/trade/cancel-order "HTTP/1.1 200 OK"
order-executor-1 | INFO: "POST /accounts/blofin-blofin-demo-v5vr/orders/cancel HTTP/1.1" 200 OK
```

The `cancel-tpsl` call returns HTTP 200 with a non-success `code` in the body (tpslId doesn't
match a regular orderId), so the code correctly falls back to `cancel-order`, which succeeds.
No change was needed to the ordering.

### Blofin amend investigation — no native endpoint exists

Probed the native amend endpoint plus plausible alternates, including one deliberately-bogus
path, to check whether "not supported" was a real rejection or a generic 404-equivalent:

```
POST /api/v1/trade/amend-order  {"instId":"BTC-USDT","orderId":"...","newPrice":"32000"}
  -> 200 {"code":"152404","msg":"This operation is not supported"}
POST /api/v1/trade/modify-order {"instId":"BTC-USDT","orderId":"...","newPrice":"32000"}
  -> 200 {"code":"152404","msg":"This operation is not supported"}
POST /api/v1/trade/amend-order  {"instId":"BTC-USDT","orderId":"...","price":"32000"}
  -> 200 {"code":"152404","msg":"This operation is not supported"}
POST /api/v1/trade/order-algo/amend (bogus path) {"instId":"BTC-USDT","orderId":"...","newPrice":"32000"}
  -> 200 {"code":"152404","msg":"This operation is not supported"}
```

All four — including the nonsense path — return the identical error, which means `152404` is
Blofin's generic "no such route" response, not a real amend-endpoint rejection. Conclusion:
Blofin has no native amend for perpetual orders. Implemented `amend_order` as
**cancel-then-replace**, sourcing side/leverage/margin-mode from the existing order so the
caller only has to supply the fields that change.

### Blofin full lifecycle: amend → cancel

```
$ docker compose exec -T order-executor curl -s -X POST \
  "http://localhost:8004/accounts/blofin-blofin-demo-v5vr/orders/amend" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTC-USDT","order_id":"1000131530593","new_price":32000}'

{"success":true,"order_id":"1000131530714","cancelled_order_id":"1000131530593",
 "raw_response":{"code":"0","msg":"","data":[{"orderId":"1000131530714",
 "clientOrderId":"","msg":"Order placed","code":"0"}]}}

$ docker compose exec -T order-executor curl -s \
  "http://localhost:8004/accounts/blofin-blofin-demo-v5vr/orders"

[{"order_id":"1000131530714","symbol":"BTC-USDT","side":"buy","price":32000.0,"size":0.001,
  "filled_size":0.0,"status":"resting","created_at_ms":1783009442137},
 {"order_id":"1000131530284","symbol":"BTC-USDT","side":"buy","price":30000.0,"size":0.001,
  "filled_size":0.0,"status":"resting","created_at_ms":1783008928420}]
```

Old order (`...593`) gone, new order (`...714`) resting at the amended price 32000. Then cancel:

```
$ docker compose exec -T order-executor curl -s -X POST \
  "http://localhost:8004/accounts/blofin-blofin-demo-v5vr/orders/cancel" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTC-USDT","order_id":"1000131530714"}'

{"success":true,"oid":"1000131530714"}

$ docker compose exec -T order-executor curl -s \
  "http://localhost:8004/accounts/blofin-blofin-demo-v5vr/orders"

[{"order_id":"1000131530284","symbol":"BTC-USDT","side":"buy","price":30000.0,"size":0.001,
  "filled_size":0.0,"status":"resting","created_at_ms":1783008928420}]
```

Cancelled order is gone; only the untouched second resting order (`...284`) remains.

### Blofin amend failure semantics — demonstrated live

Placed a fresh order, then amended it with an invalid price (`-1`) to force the cancel to
succeed but the replacement placement to be rejected by Blofin:

```
$ docker compose exec -T order-executor curl -s -X POST "http://localhost:8004/execute" \
  -d '{"order_id":"probe-blofin-fail-demo","account_id":"blofin-blofin-demo-v5vr",
      "symbol":"BTC-USDT","side":"buy","signal":"open_long","order_type":"limit",
      "size":"0.001","price":"31000","leverage":5,"margin_mode":"isolated"}'
{"success":true,"exchange_order_id":"1000131530741","status":"pending", ...}

$ docker compose exec -T order-executor curl -s -X POST \
  "http://localhost:8004/accounts/blofin-blofin-demo-v5vr/orders/amend" \
  -d '{"symbol":"BTC-USDT","order_id":"1000131530741","new_price":-1}'

{"success":false,"original_cancelled":true,
 "error":"cancel succeeded but replacement failed — order is GONE: Parameter price error. ({'code': '152002', 'msg': 'Parameter price error.'})",
 "raw_response":{"code":"152002","msg":"Parameter price error."}}

$ docker compose exec -T order-executor curl -s \
  "http://localhost:8004/accounts/blofin-blofin-demo-v5vr/orders"
[]
```

Confirms the documented failure mode exactly: the cancel went through, the replacement was
rejected, and the response flags `original_cancelled: true` so a caller knows the order is
truly gone (not "unchanged") and must re-place explicitly if desired.

### Hyperliquid full lifecycle: amend (native `modify`) → cancel

```
$ docker compose exec -T order-executor curl -s -X POST \
  "http://localhost:8004/accounts/hyperliquid-hyperliquid-hqdy/orders/amend" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"ETH-USDT","order_id":"55870927365","new_price":900}'

{"success":true,"order_id":"55870927365","raw_response":{"status":"ok","response":{"type":"default"}}}

$ docker compose exec -T order-executor curl -s \
  "http://localhost:8004/accounts/hyperliquid-hyperliquid-hqdy/orders"

[{"order_id":"55871047903","symbol":"ETH-USDT","side":"buy","price":900.0,"size":0.02,
  "filled_size":0.0,"status":"resting","created_at_ms":1783009469632},
 {"order_id":"55870778029","symbol":"ETH-USDT","side":"buy","price":800.0,"size":0.02,
  "filled_size":0.0,"status":"resting","created_at_ms":1783008997792}]
```

Note: HL's native `modify` assigns a **new oid** (`55871047903`) even though the old oid
(`55870927365`) was passed in — the old oid is gone and a new one takes its place at the
amended price. This is expected HL behavior (unlike Blofin's cancel-then-replace, it's a single
atomic exchange action, but the identifier still changes).

```
$ docker compose exec -T order-executor curl -s -X POST \
  "http://localhost:8004/accounts/hyperliquid-hyperliquid-hqdy/orders/cancel" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"ETH-USDT","order_id":"55871047903"}'

{"success":true,"oid":55871047903}

$ docker compose exec -T order-executor curl -s \
  "http://localhost:8004/accounts/hyperliquid-hyperliquid-hqdy/orders"

[{"order_id":"55870778029","symbol":"ETH-USDT","side":"buy","price":800.0,"size":0.02,
  "filled_size":0.0,"status":"resting","created_at_ms":1783008997792}]
```

Cancelled order gone; untouched second resting order (`...029`) remains. This cancel call also
exercised the new `int(oid)` cast fix — `order_id` arrives from the JSON body as the string
`"55871047903"`, which previously would have serialized incorrectly into HL's signed msgpack
cancel action.

### Cleanup — final state on both exchanges

```
$ docker compose exec -T order-executor curl -s -X POST \
  "http://localhost:8004/accounts/blofin-blofin-demo-v5vr/orders/cancel" \
  -d '{"symbol":"BTC-USDT","order_id":"1000131530284"}'
{"success":true,"oid":"1000131530284"}

$ docker compose exec -T order-executor curl -s -X POST \
  "http://localhost:8004/accounts/hyperliquid-hyperliquid-hqdy/orders/cancel" \
  -d '{"symbol":"ETH-USDT","order_id":"55870778029"}'
{"success":true,"oid":55870778029}

$ docker compose exec -T order-executor curl -s \
  "http://localhost:8004/accounts/blofin-blofin-demo-v5vr/orders"
[]

$ docker compose exec -T order-executor curl -s \
  "http://localhost:8004/accounts/hyperliquid-hyperliquid-hqdy/orders"
[]
```

No stray resting test orders remain on either demo account. (Both accounts retain their
pre-existing, unrelated open positions — SUI-USDT/HYPE-USDT on Blofin, BTC-USDT short on HL —
which were never touched by this work.)

**Phase 2 gate: confirmed.**

---

## Deploy verification

```
$ ./scripts/redeploy.sh order-executor
...
✓ order-executor redeployed.

$ docker compose ps order-executor
NAME                    IMAGE                 STATUS
matp-order-executor-1   matp-order-executor   Up (healthy)

$ docker compose exec -T order-executor curl -s "http://localhost:8004/health"
{"status":"ok","service":"order-executor","version":"1.0.0"}

$ docker compose exec -T order-executor python3 -m pytest tests/ -q
..........
10 passed in 22.29s
```

## Explicitly out of scope (per prompt)

`order-listener`, `ai-signal-generator`, and the dashboard were not touched. The AI graph still
only dispatches market orders, so today's live behavior is unchanged — the `"pending"` status
value is new but dormant until a caller starts placing limit orders. Teaching `order-listener`
to handle `status="pending"` (instead of assuming any successful order created a position) is
the explicitly deferred next prompt.
