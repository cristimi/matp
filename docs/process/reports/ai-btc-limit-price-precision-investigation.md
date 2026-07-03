# Investigation: ai-btc-6f8c limit order rejected — "Precision does not match: 0.1"

Investigation only — no code changed.

## Symptom

Strategy `ai-btc-6f8c` (BTC-USDT, Blofin demo account `blofin-blofin-demo-v5vr`) had a
limit order rejected:

```
order id: 0e1fb144-96c4-4aee-86fc-0f415fd0e0b2
received_at: 2026-07-03 04:02:54 UTC
side: buy   order_type: limit   size: 0.00405289   price: 61684.373858
status: rejected
error_msg: All operations failed ({'code': '1', 'msg': 'All operations failed',
  'data': [{'orderId': None, 'clientOrderId': '', 'msg': 'Precision does not match: 0.1',
  'code': '102016'}]})
```

## Root cause

The submitted price, `61684.373858`, is not a multiple of Blofin's BTC-USDT price
tick. Confirmed live from the exchange's own instrument spec (already cached and
exposed by `order-executor`):

```
GET /accounts/blofin-blofin-demo-v5vr/instrument-specs → BTC-USDT:
  {'price': {'mode': 'tick', 'tick': 0.1}, 'size': {'dp': 4}}
```

`61684.373858 / 0.1 = 616843.73858` — not an integer, so Blofin's server-side
validation rejects it with exactly the tick size in the message (`0.1`), matching the
error verbatim.

### How an unrounded price reaches the exchange

This strategy is `type=internal, platform=auto` — an AI-generated signal, part of the
recently-added "work the range with limit orders" feature
(`1ef736f feat(ai-signal-generator): work the range with limit orders`). Traced the
full path from signal to exchange call:

1. **`ai-signal-generator/app/graph/nodes/node_analyze.py:30`** — the LLM's structured
   output schema declares `limit_price: Optional[float]` with no precision constraint
   or tick-size guidance in the field description or (checked) the prompt. The model
   is free to emit any float precision.
2. **`ai-signal-generator/app/graph/nodes/node_guard.py:104-144`** (`place_limit_long`
   / `place_limit_short` handling) — takes that raw value: `limit_price =
   float(signal.get('limit_price'))` and passes it straight through as
   `resolved_limit_price`. No rounding to any tick size anywhere in this function.
   (Note: `resolved_sl_price`/`resolved_tp_price` in the same block *are* rounded, but
   only to a fixed 4 decimal places — not to the instrument's actual tick — so those
   are a related, not-yet-triggered version of the same gap; see below.)
3. **`ai-signal-generator/app/webhook/dispatcher.py:44`** — `'price': str(state
   ['resolved_limit_price'])` — forwards the unrounded value verbatim into the
   webhook payload sent to order-listener.
4. **order-listener** → **order-executor** — the price flows through as
   `OrderRequest.price` with no rounding at any hop.
5. **`order-executor/app/adapters/blofin.py:318-319`** (`BlofinAdapter.submit_order`):
   ```python
   if order.price:
       body_data["price"] = str(order.price)
   ```
   Sends `order.price` to Blofin's `/api/v1/trade/order` completely unrounded. This is
   the point where the bug is actually realized — nothing upstream *or* here rounds
   to the instrument's tick.
6. Blofin's exchange API validates server-side and rejects with code `102016`.

### Why this wasn't caught earlier

All previously-tested limit orders on this account (`hype-test-7db4`,
`sui-manual-59d9`, and even earlier manual test orders on `ai-btc-6f8c` itself) used
round, human-chosen prices (`40`, `90`, `92`, `95`, `70000`, `45000`, `0.50`, `0.80`)
that happened to already be exact multiples of their symbol's tick. None of that
testing exercised a price with the AI's native float precision, so the missing
rounding step was never exercised until the first real AI-generated `limit_price`
went through. This is the only order in the DB matching this error code — a first
occurrence, not a recurring pattern (checked: `SELECT ... WHERE error_msg ILIKE
'%102016%'` returns exactly this one row).

### Confirmed Blofin-specific — Hyperliquid already handles this correctly

`HyperliquidAdapter.submit_order` (`hyperliquid.py:406`) already rounds every limit
price through `self._round_price(...)` before building the signed order — this exact
class of bug was already solved on the Hyperliquid side. `BlofinAdapter` has no
equivalent call in `submit_order`; the tick-size data it would need
(`inst.get("tickSize")`) is already fetched and cached (`_get_instrument`) and is
even already exposed for other purposes via `get_instrument_specs()`
(`blofin.py:660-682`, backing the `/accounts/{id}/instrument-specs` route) — the data
and precedent both already exist in this codebase, they're just not wired into the
order-submission path for Blofin.

### Blast radius

Every Blofin instrument has a nonzero price tick, so any AI-generated `limit_price`
that doesn't happen to already land on a tick multiple will fail the same way.
Checked tick sizes for this account's active symbols:

```
BTC-USDT  tick 0.1      (coarsest — highest rejection odds for a 6-decimal AI price)
ETH-USDT  tick 0.01
HYPE-USDT tick 0.001
SUI-USDT  tick 0.0001   (finest — lowest but nonzero rejection odds)
```

BTC-USDT's coarse 0.1 tick makes it the most exposed, but the gap applies to every
Blofin symbol the AI signal generator trades. The related SL/TP rounding
(`round(x, 4)` in `node_guard.py`, not tick-aware) hasn't triggered a rejection yet
only because trigger-order endpoints have looser/different validation observed so
far — same root gap, lower probability of biting, not yet exercised.

### Current state — no stuck/orphaned state

No open position and no resting order exist for `ai-btc-6f8c`/BTC-USDT right now —
the rejection was clean (order marked `rejected`, nothing partially applied, no
phantom position). This is a functional bug blocking the AI limit-order feature for
BTC-USDT (and probabilistically for other symbols), not a data-integrity issue
needing urgent remediation of existing state.

## Recommended fix (not applied — investigation only, per instruction)

Round `order.price` to the instrument's tick size inside `BlofinAdapter.submit_order`
before building `body_data`, mirroring `HyperliquidAdapter._round_price` — the tick
value is already available via the same `_get_instrument()`/`tickSize` data that
backs `get_instrument_specs()`. Same fix class should also be considered for the
SL/TP rounding in `node_guard.py` (currently a flat 4dp, not tick-aware) as a
follow-up, lower-priority since it hasn't failed yet.
