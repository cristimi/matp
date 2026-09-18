# Manual BTC short NOT opened — Blofin demo API key no longer exists (2026-09-18)

## Request

Same as 2026-09-16: short on `social-btc-astro` at market, double size, 10x, TP1 half
at 74,500.

## What happened

The same one-off script as on 2026-09-16 posted the first `open_short`
(`leverage=10`, size 0.00194238) to order-listener. order-listener accepted it, built
the guaranteed stop, and handed it to order-executor — which the exchange rejected:

```
webhook: 200 {"order_id":"199235de-4869-4532-b0f9-60e550d7a596","status":"received","message":"OK"}
open position after open: None
position not recorded after open — not adding

orders.status    = rejected
orders.error_msg = Access key does not exist ({'code': '152401', 'msg': 'Access key does not exist'})

order-executor: BlofinAdapter: set-leverage failed for BTC-USDT: Access key does not exist
GET order-executor:8004/accounts/blofin-blofin-demo-v5vr/balance
{"total_balance":0.0,"available_balance":0.0,"used_margin":0.0,"currency":"USDT",
 "error":"Blofin API error 152401: Access key does not exist"}
```

The add step and the TP1 placement were not attempted. Nothing is open.

## Since when

First `152401` in order-executor's log: **2026-09-17 08:01 UTC** (set-leverage on the
TAO-USDT reconcile). Every authenticated Blofin call has failed since. The account row
`exchange_accounts.blofin-blofin-demo-v5vr` was last updated 2026-06-19 — the demo
key was not changed on our side; Blofin dropped it.

Side effect: the 2026-09-16 short (position `2bf2123f`, 0.004 BTC @ 76,120.8) was
marked `Closed on exchange` by the reconciler at 2026-09-17 07:34 after three passes
read `exchange=0`. With the key dead, that read was an auth failure, not a real
"no position" — whether the short still exists on the venue, and what happened to TP1
at 74,500, cannot be known until a working key is in place.

```
2026-09-17 07:31:59 reconciler: position 2bf2123f (BTC-USDT short) miss 1/3 db=0.004 exchange=0
2026-09-17 07:34:01 reconciler: pnl_unconfirmed for position 2bf2123f close_reason=Closed on exchange
```

## Clean-up done

The script records the listener's leg right after the 200 (before the fill is known),
so the phantom state was removed by hand:

```
DELETE FROM social_position_state WHERE last_msg_id=-20260918;     -> DELETE 1
social_shadow_orders -20260918: decision=skipped, to_state=FLAT,
  reason='exchange rejected order 199235de: Access key does not exist (152401)'
social_position_state count: 0
```

The rejected order row `199235de` stays as the record of the attempt. Script deleted
from the container.

## To unblock

1. Create a new API key on the Blofin demo account and update the credentials of
   account `blofin-blofin-demo-v5vr` in the dashboard (Accounts page).
2. Check the venue for the leftover 2026-09-16 short before opening a new one.
3. Then re-run the entry.

Worth fixing later (not done here): the reconciler treats an auth failure on the
positions read as "position gone" and closes the DB record after 3 misses; it should
treat a failed read as unknown, like the trigger-orders guard already does.
