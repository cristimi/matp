# FIX: Blofin TP/SL trigger price precision (tick rounding)

Continues: docs/process/reports/blofin-limit-price-tick-rounding-fix.md, which
rounded the *entry* limit price in `BlofinAdapter.submit_order` and flagged two
follow-ups: `tpTriggerPrice`/`slTriggerPrice` were still sent unrounded in both
`submit_order` (inline TP/SL) and `place_trigger_orders` (standalone TP/SL via
`/api/v1/trade/order-tpsl`). This fixes both, closing the same class of gap
(exchange rejects off-tick prices with code 102016 "Precision does not match")
for TP/SL, not just entry prices.

## Root cause (recap)

Same underlying issue as the entry-price fix: Blofin validates trigger prices
server-side against the instrument's tick size, and any caller-supplied
`tp_price`/`sl_price` that isn't an exact tick multiple would be rejected the same
way the raw AI-generated limit price was. `node_guard.py` (`ai-signal-generator`)
only rounds SL/TP to a flat 4 decimal places — not tick-aware — so this was a live
gap for symbols with coarser ticks (e.g. BTC-USDT's `0.1`). Per the architecture
invariant, adapter code owns all exchange-specific logic, so the fix belongs at the
Blofin adapter boundary, not upstream in `node_guard.py` — rounding here makes it
authoritative regardless of what precision comes in.

## Fix

`order-executor/app/adapters/blofin.py` — reused the existing `_round_to_tick`
helper (added in the entry-price fix, unchanged here) at two more call sites:

- **`submit_order`**: `tpTriggerPrice`/`slTriggerPrice` are now rounded through
  `await self._round_to_tick(order.symbol, order.tp_price / order.sl_price)`
  before `str(...)`, mirroring the entry-price line immediately above them.
- **`place_trigger_orders`**: inside the `for tpsl_type, price in [("tp", tp_price),
  ("sl", sl_price)]` loop, `price` is rounded via `await self._round_to_tick(symbol,
  price)` once, right after the `if price is None: continue` guard — so both the
  `slTriggerPrice`/`tpTriggerPrice` assignment and the placed-log message use the
  rounded value.

Scope: only `blofin.py`, both call sites. No changes to `_round_to_tick` itself,
`HyperliquidAdapter`, `node_guard.py`, or `ai-signal-generator`. No DB migration.

## Deploy

```
docker compose build --no-cache order-executor && docker compose up -d order-executor
```
(force-recreate via `up -d`, not `restart`). Confirmed healthy:
```
$ docker compose ps order-executor
matp-order-executor-1   Up (healthy)
```

Confirmed the running container serves the new code — `_round_to_tick` now called
from all three intended sites (entry price from the prior fix, plus the two new
ones):
```
$ docker compose exec order-executor grep -n "_round_to_tick" /app/app/adapters/blofin.py
81:    async def _round_to_tick(self, inst_id: str, price) -> float:
328:                body_data["price"] = str(await self._round_to_tick(order.symbol, order.price))
330:                body_data["tpTriggerPrice"] = str(await self._round_to_tick(order.symbol, order.tp_price))
333:                body_data["slTriggerPrice"] = str(await self._round_to_tick(order.symbol, order.sl_price))
946:                price = await self._round_to_tick(symbol, price)
```

## Verification (live demo Blofin account, real exchange calls)

### 1. `submit_order` with off-tick TP/SL

Placed a real resting BTC-USDT limit order (tick `0.1`, priced well below the live
mark of `62002.9` so it wouldn't fill) with deliberately off-tick `tp_price`/
`sl_price` through the actual `submit_order` code path:
```
OrderRequest(symbol='BTC-USDT', side='buy', order_type='limit', size='0.001',
  price='59812.123456', tp_price='70123.456789', sl_price='55321.987654', ...)
->
success=True exchange_order_id='1000131565960' status='pending' error_msg=None
```
Accepted — no `102016`. Read the order back directly from Blofin
(`_get_order_state`) to confirm what actually landed:
```
{'orderId': '1000131565960', ..., 'price': '59812.1',
 'tpTriggerPrice': '70123.5', 'slTriggerPrice': '55322.0', 'state': 'live', ...}
```
`70123.456789 → 70123.5` and `55321.987654 → 55322.0` — both the nearest `0.1`
multiple, confirming the rounding is applied in the real order path, not just in
the helper. Cancelled immediately after confirming (`cancel_order` →
`{'success': True, ...}`); no resting orders remain for BTC-USDT afterward.

### 2. `place_trigger_orders` with off-tick TP/SL

Used the real, already-open SUI-USDT long position (tick `0.0001`) on the same demo
account. Baseline (existing protective stops, untouched throughout this test):
```
[{"oid":"10002462959","tpsl":"sl","triggerPx":"0.6593","sz":"142"},
 {"oid":"10002462958","tpsl":"tp","triggerPx":"1.5","sz":"142"}]
```
Called `place_trigger_orders` directly with off-tick prices (a small size=1
standalone trigger, additive — doesn't touch the existing position stops):
```
place_trigger_orders(symbol='SUI-USDT', trigger_side='sell', size=1,
  tp_price=1.50006789123, sl_price=0.65936789123)
->
{'success': True, 'placed': [{'tpsl': 'tp', 'oid': '10002464241', 'status': 'placed'},
                             {'tpsl': 'sl', 'oid': '10002464242', 'status': 'placed'}]}
```
Accepted — no `102016`. Read the trigger orders back from Blofin:
```
[{"oid":"10002464242","tpsl":"sl","triggerPx":"0.6594","sz":"1"},
 {"oid":"10002464241","tpsl":"tp","triggerPx":"1.5001","sz":"1"},
 {"oid":"10002462959","tpsl":"sl","triggerPx":"0.6593","sz":"142"},   <- original, untouched
 {"oid":"10002462958","tpsl":"tp","triggerPx":"1.5","sz":"142"}]      <- original, untouched
```
`1.50006789123 → 1.5001` and `0.65936789123 → 0.6594` — both the nearest `0.0001`
multiple, matching `_round_to_tick`'s math exactly.

### Cleanup

Cancelled both test triggers (`10002464241`, `10002464242`); confirmed only the
original SUI-USDT position stops remain:
```
[{"oid":"10002462959","tpsl":"sl","triggerPx":"0.6593","sz":"142"},
 {"oid":"10002462958","tpsl":"tp","triggerPx":"1.5","sz":"142"}]
```
Final check: no resting orders on BTC-USDT, `order-executor` healthy, logs show no
errors throughout.

## Not done / follow-ups

- `node_guard.py`'s flat-4dp SL/TP rounding is unchanged, per instruction — moot now
  that the adapter boundary is the authoritative last step regardless of upstream
  precision.
- `HyperliquidAdapter` untouched — it already rounds all prices via `_round_price`.
