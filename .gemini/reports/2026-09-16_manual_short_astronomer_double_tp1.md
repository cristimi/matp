# Manual BTC short on the social strategy — double size, 10x, TP1 half at 74,500 (2026-09-16)

## Request

Open a short on `social-btc-astro` at market, double size, leverage 10x, TP1 on half
the position at 74,500. No stop-loss was given.

## Pre-checks

```
recorded legs: Legs(FLAT)
open position before: None
exchange BTC position: none (only unrelated TAO / BNB shorts)
mark=76101.1 margin=15 lev=10 unit size=0.00197106
```

## How it was sent

Same path as 2026-09-14: a one-off script in the `social-listener` container built the
listener's own webhook payload (`emitter._payload`) and posted it to order-listener —
one `open_short` then one `add_short` of the same size (order-listener clamps a single
order to one margin unit, so "double" is two orders). `leverage=10` was added to both
payloads (strategy default is 20). No `sl_price` was sent, so order-listener injected its
liquidation-safe guaranteed stop (`sl_source=liquidation_safe`).

```
webhook: 200 {"order_id":"34c7bc1a-51c7-4fd5-8cd5-aa7b516b6721","status":"received","message":"OK"}
open position after open: size 0.002 entry 76120.8 sl 83386.6
mark=76100.9 adding 0.002
webhook: 200 {"order_id":"08283ee5-9bbe-46dc-bd2d-f97cbcc8ff29","status":"received","message":"OK"}
position now: size 0.004 entry 76120.8 sl 83386.6
```

**TP1 (half size).** The platform has no partial take-profit: `adjust-stops` /
`modify-stops` always size the triggers from the full position. So the half TP was
placed by a one-off script inside `order-executor`, through the account's own adapter
(`BlofinAdapter.place_trigger_orders`, size 0.002 = 2 contracts, reduce-only) — no
exchange call was made outside order-executor.

```
position: BTC-USDT short size 0.0040 entry 76120.8
place tp1: {"success": true, "placed": [{"tpsl": "tp", "oid": "10003922809", "status": "placed"}]}
triggers: [{"oid": "10003922809", "tpsl": "tp", "triggerPx": "74500", "sz": "2"},
           {"oid": "10003922808", "tpsl": "sl", "triggerPx": "83386.9", "sz": "2"},
           {"oid": "10003922806", "tpsl": "sl", "triggerPx": "83386.6", "sz": "2"}]
```

## Verification

Orders (both fills, leverage 10, guaranteed stop from the platform):

```
 34c7bc1a-51c7-4fd5-8cd5-aa7b516b6721 | open_short | 0.002 | filled | 76120.8 | lev 10 | sl 83386.6 | sl_source liquidation_safe
 08283ee5-9bbe-46dc-bd2d-f97cbcc8ff29 | open_short | 0.002 | filled | 76120.8 | lev 10 | sl 83386.9 | intent add_short
```

Position under the strategy:

```
 2bf2123f-b912-477c-b0c0-51a18699321c | social-btc-astro | BTC-USDT | short | 0.004 | 76120.8 | open | 2026-09-16 20:55:14+00
```

Venue, read through order-executor:

```
GET .../positions  BTC-USDT short size 0.0040 entry 76120.8 leverage 10 liquidation 83432.52
GET .../trigger-orders/BTC-USDT
  tp 74500.0  sz 2      <- TP1, half the position
  sl 83386.9  sz 2      <- one guaranteed stop per fill, together = full 4 contracts
  sl 83386.6  sz 2
```

Listener state and audit rows:

```
 telegram:AstronomerZero | BTC | SHORT | OPEN | last_msg_id -20260916 | stop_price NULL | tp_price 74500

 -20260916  | OPEN | open_short | acted | manual_entry | tp 74500
 -202609161 | ADD  | add_short  | acted | manual_add   | add_size 0.002
```

## Choices worth knowing

- **Stop-loss is the platform's liquidation-safe default (~83,387, +9.5%)**, because
  none was requested. It sits just under the 83,432 liquidation price — it is a
  last-resort stop, not a trading stop. Set a real one with the usual adjust-stops path.
- **TP1 covers 2 of 4 contracts.** Any later `adjust-stops` / `modify-stops` call
  (a Telegram stop-move post, or a manual level change) cancels every trigger and
  re-places them at full position size — the half sizing would be lost and the TP
  would then close the whole position at 74,500. Re-place the half TP after any such
  change.
- `social_position_state.tp_price=74500` and `orders.signal_metadata.tp1_*` on the
  opening order record the level; `orders.tp_price` was left NULL on purpose so the
  dashboard does not show a full-size TP.
- Synthetic `channel_msg_id = -20260916` / `-202609161` keep the catch-up watermark on
  the last real Telegram message. Both scripts were deleted from the containers.
