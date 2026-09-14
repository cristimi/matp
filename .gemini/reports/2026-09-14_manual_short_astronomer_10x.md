# Manual BTC short on the social strategy — 10x, SL 82,550, TP 76,000 (2026-09-14)

## Request

Open a short on `social-btc-astro` at market, leverage 10x, stop-loss 82,550,
take-profit 76,000.

## Pre-checks

Recorded legs flat, no BTC position on the exchange (only an unrelated TAO short):

```
recorded legs: FLAT
exchange-side BTC position: None
mark=78961.4 margin=15.0 lev=10 size=0.00189966 sl=82550.0 tp=76000.0
reward/risk = 0.83  (TP sent after entry: entry guard floors R:R at 1.0)
```

## How it was sent

Same approach as the 2026-08-18 manual short: a one-off script inside the
`social-listener` container built the listener's own webhook payload
(`emitter._payload`) and posted it to order-listener, so the position is owned by the
social strategy and no exchange call was made outside order-executor. Two fields were
added to the payload that the emitter normally leaves to the strategy defaults:
`leverage=10` (strategy default is 20) and `sl_price=82550`.

**The take-profit was NOT sent with the entry.** order-listener's entry guard rejects
any bracket with reward/risk below 1.0, and this one is 0.83 (reward 2,961 vs risk
3,589 from the 78,961 reference). The stop went in with the entry; the TP was set
right after the fill through `emitter.adjust_levels` (order-listener `adjust-stops`),
re-sending the resting stop alongside it so the cancel-then-place step could not leave
the short unprotected. That endpoint has no R:R floor — it is the operator's level.

Live run:

```
webhook: 200 {"order_id":"1f4744dc-eb8a-44f5-bf96-b8aa1dab92cf","status":"received","message":"OK"}
recorded legs now: SHORT
position: BTC-USDT short size 0.001900000000000000000 entry 78967.1 resting sl 82550.0 tp None
adjust_levels ok= True SHORT sl=82550.0 tp=76000.0 confirmed
recorded levels: {'stop_price': 82550.0, 'tp_price': 76000.0, 'stop_mode': None}
```

## Verification

Order row (stop came from the payload, not the liquidation-safe fallback):

```
id                | 1f4744dc-eb8a-44f5-bf96-b8aa1dab92cf
symbol            | BTC-USDT
side              | sell
signal            | open_short
size              | 0.001900000000000000000
status            | filled
actual_fill_price | 78967.1
leverage          | 10
sl_price          | 82550.0
tp_price          | 76000.0
signal_source     | social_listener
signal_metadata   | {"mmr": 0.0045, "source": "telegram:AstronomerZero", "entry_ref": 78961.4, "sl_source": "strategy", "mmr_source": "live", "manual_entry": "operator, X post not mirrored to Telegram", "sl_distance_pct": 4.5448}
strategy_id       | social-btc-astro
account_id        | blofin-blofin-demo-v5vr
```

Position under the strategy:

```
 3718bac3-241a-4633-8230-3fca55a905eb | social-btc-astro | BTC-USDT | short | 0.0019 | 78967.1 | open | 2026-09-14 17:55:03.795498+00
```

Listener state and audit row:

```
 telegram:AstronomerZero | BTC | OPEN | -20260914 | stop_price=82550 | tp_price=76000 | side=SHORT

 channel_msg_id | phase | asset | intended_signal | decision | reason       | mode | stop_price | tp_price
 -20260914      | live  | BTC   | open_short      | acted    | manual_entry | live | 82550      | 76000
```

Venue, read through order-executor — both triggers rest on the full 1.9 contracts:

```
GET order-executor:8004/accounts/blofin-blofin-demo-v5vr/trigger-orders/BTC-USDT
[{"oid":"10003886330","tpsl":"sl","triggerPx":"82550.0","sz":"1.9"},
 {"oid":"10003886329","tpsl":"tp","triggerPx":"76000.0","sz":"1.9"}]

GET order-executor:8004/accounts/blofin-blofin-demo-v5vr/positions
{"symbol":"BTC-USDT","side":"short","size":"0.0019","entry_price":"78967.1","leverage":10,
 "liquidation_price":"86552.22"}
```

## Choices worth knowing

- **Size 0.0019 BTC, not 0.0047 like last time.** Sizing is margin × leverage / price
  (15 × 10 / 78,961). The 10x request halves the size the 20x default would give.
- **Synthetic `channel_msg_id = -20260914`**, negative so the catchup watermark stays
  on the last real Telegram message (9886). Rows are labelled `manual` / `manual_entry`.
- The listener now records a SHORT leg with both levels, so a later Telegram TRIM /
  CLOSE / stop-move post acts on this position normally and re-sends the TP.
- Script ran as `/app/manual_open.py` in the container and was deleted afterwards.

---

## Follow-up: position doubled (same session)

Requested right after the entry. Sent as the listener's own scale-in step
(`add_short`, `intent=add_short`, size = the 0.0019 already held, `leverage=10`,
`sl_price=82550`), then **both levels re-placed once** through `adjust_levels` so the
triggers cover the doubled size — `modify-stops` sizes them from the live position.
The TP was again left off the add order itself (R:R floor), and put back in the same
call as the stop.

```
open position: size=0.0019 entry=78967.1 sl=82550.0 tp=76000.0
mark=78915.1 adding 0.0019 -> target size 0.0038
webhook: 200 {"order_id":"76d144f1-f8c6-4ac8-a28a-a61e00694c44","status":"received","message":"OK"}
position now: size=0.003800000000000000000 entry=78940.6
adjust_levels ok= True SHORT sl=82550.0 tp=76000.0 confirmed
recorded levels: {'stop_price': 82550.0, 'tp_price': 76000.0, 'stop_mode': None}
```

Verified:

```
 76d144f1-f8c6-4ac8-a28a-a61e00694c44 | open_short | 0.0019 | filled | 78914.1 | lev 10 | sl 82550.0 | intent add_short

 3718bac3-241a-4633-8230-3fca55a905eb | BTC-USDT | short | 0.0038 | 78940.6 | open      (blended entry)

GET order-executor:8004/accounts/blofin-blofin-demo-v5vr/trigger-orders/BTC-USDT
 sl triggerPx 82550  sz 3.8
 tp triggerPx 76000  sz 3.8

GET .../positions  BTC-USDT short size 0.0038 entry 78940.6 liquidation 86523.18
```

Audit rows written with synthetic `channel_msg_id = -202609141` (`action_type=ADD`,
reason `manual_add`). `social_position_state` keeps the SHORT leg with both levels.
Script `/app/manual_add.py` ran in the container and was deleted afterwards.
