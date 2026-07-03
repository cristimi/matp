# FIX: Blofin limit-order price precision rejection (error 102016)

Continues: docs/process/reports/ai-btc-limit-price-precision-investigation.md
(investigation, no code changed). This is the fix.

## Root cause (recap)

`BlofinAdapter.submit_order` sent `order.price` to Blofin's `/api/v1/trade/order`
completely unrounded. Blofin validates the price server-side against the
instrument's tick size and rejects anything off-tick with code `102016`
("Precision does not match: <tick>"). An AI-generated limit order for BTC-USDT
(`ai-btc-6f8c`, price `61684.373858` vs. a `0.1` tick) was rejected this way.
`HyperliquidAdapter` already rounds every limit price (`_round_price`); Blofin never
got the equivalent treatment, even though the tick-size data it needs was already
fetched/cached (`_get_instrument`) and exposed via `get_instrument_specs()`.

## Fix

`order-executor/app/adapters/blofin.py`:

- Added `BlofinAdapter._round_to_tick(inst_id, price)`: reads the cached instrument's
  `tickSize` (same `"0.1"` fallback already used by `get_instrument_specs()`), rounds
  `price` to the nearest tick multiple (`round(round(raw / tick) * tick, 10)` — same
  style as `_to_contracts`'s lot-size rounding, with the trailing `round(..., 10)`
  guarding against float noise).
- `submit_order()`: `body_data["price"] = str(order.price)` →
  `body_data["price"] = str(await self._round_to_tick(order.symbol, order.price))`.

Scope: only `blofin.py`. `tpTriggerPrice`/`slTriggerPrice` are untouched (separate,
lower-priority follow-up — that gap hasn't triggered a rejection yet). Hyperliquid
adapter and `ai-signal-generator`/`node_guard.py` SL/TP rounding untouched, per
instruction. No DB migration.

## Deploy

```
docker compose build --no-cache order-executor && docker compose up -d order-executor
```
(force-recreate via `up -d`, not `restart` — picks up the new image). Confirmed
healthy after recreate:
```
$ docker compose ps order-executor
NAME                    STATUS
matp-order-executor-1   Up (healthy)
```

Confirmed the running container is actually serving the new code (not a stale
image):
```
$ docker compose exec order-executor grep -n "_round_to_tick" /app/app/adapters/blofin.py
81:    async def _round_to_tick(self, inst_id: str, price) -> float:
328:                body_data["price"] = str(await self._round_to_tick(order.symbol, order.price))
```

## Verification

**1. `_round_to_tick` directly**, against the exact case from the investigation
report plus two other symbols with different tick sizes:
```
BTC-USDT 61684.373858 -> 61684.4
SUI-USDT 0.73912345 -> 0.7391
HYPE-USDT 67.32456789 -> 67.325
```
`61684.373858` → `61684.4` is the nearest `0.1` multiple, as expected.

**2. Real demo-account BTC-USDT limit order at an intentionally unrounded price**
(`59812.123456`, size `0.001`, well below the live mark of `61812.5` so it rests
without filling), submitted through `BlofinAdapter.submit_order` — the exact code
path that previously produced the `102016` rejection:
```
result = await a.submit_order(OrderRequest(
    symbol='BTC-USDT', side='buy', order_type='limit',
    size='0.001', price='59812.123456', leverage=10, margin_mode='isolated', ...
))
->
success=True exchange_order_id='1000131555903' status='pending' error_msg=None
raw_response={'code': '0', 'msg': '', 'data': [{'orderId': '1000131555903',
  'clientOrderId': '', 'msg': 'Order placed', 'code': '0'}]}
```
No `102016` — accepted. Read back the resting order directly from the exchange to
confirm it landed at the **rounded** price, not the raw one:
```
$ GET /accounts/blofin-blofin-demo-v5vr/orders?symbol=BTC-USDT
[{"order_id":"1000131555903","symbol":"BTC-USDT","side":"buy","price":59812.1,
  "size":0.001,"filled_size":0.0,"status":"resting", ...}]
```
`59812.1` — exactly `_round_to_tick`'s output for that input, confirming the fix is
live in the actual order-placement path, not just the helper in isolation.

**Cleanup**: cancelled the test order immediately after confirming it rested
(`cancel_order('BTC-USDT', '1000131555903')` → `{'success': True, ...}`); re-read
confirms no resting orders remain on the account. `order-executor` logs show no
errors, service healthy throughout.

## Not done / follow-ups (unchanged from the investigation report)

- `tpTriggerPrice`/`slTriggerPrice` in `submit_order` and `place_trigger_orders` are
  still unrounded — same class of gap, not yet observed failing, flagged as a
  separate follow-up.
- `node_guard.py`'s SL/TP rounding (flat 4dp, not tick-aware) is unchanged — out of
  scope for this pass per instruction.
