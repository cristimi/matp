# BTC short on the social strategy doubled: 0.0078 → 0.0154 BTC (2026-09-18)

## Request

Double the BTC position held on `social-btc-astro`.

## Before

```
GET .../positions  BTC-USDT short 0.0078 entry 76670.05 mark 80834.66 upnl -32.48 liq 84034.53
GET .../trigger-orders  tp 74500 sz 3.9 | sl 83298.1 sz 7.8
```

## How it was sent

Four `add_short` orders of one standard 10x entry each (order-listener clamps any single
order to one margin unit, 15 × 10 / mark), through the listener's own webhook path —
same one-off script as earlier today, ids `-202609183..-202609186`:

```
add 1/4: mark=80822.9 unit=0.00185591  webhook 200 c4f8518e  -> 0.0097 entry 77488.76
add 2/4: mark=80841.3 unit=0.00185549  webhook 200 5097c596  -> 0.0116 entry 78037.03
add 3/4: mark=80832.0 unit=0.0018557   webhook 200 f6b97c09  -> 0.0135 entry 78435.20
add 4/4: mark=80850.0 unit=0.00185529  webhook 200 b329dbd1  -> 0.0154 entry 78735.84
```

Each fill came with its own guaranteed stop (~88,58x), above the new liquidation price
(86,298). The reconciler's liquidation guard tightened and consolidated them twice
(mid-run at 9.7 contracts, then at 15.4), re-placing the TP at full size each time:

```
14:48:48 TIGHTENED SL 88558.6 -> 84187.6 (liq=84931.88, tp_preserved=74500.0)   [9.7]
14:49:54 TIGHTENED SL 88594.6 -> 85542.5 (liq=86298.75, tp_preserved=74500.0)   [15.4]
```

TP1 was then put back to half through the adapter inside order-executor:

```
before: sl 85542.5 sz 15.4 | tp 74500 sz 15.4
cancel tp 10003957152 {"success": true}
place tp1: {"success": true, "placed": [{"tpsl": "tp", "oid": "10003957205"}]}
after:  tp 74500 sz 7.7 | sl 85542.5 sz 15.4
```

## Verification

```
 c4f8518e | 0.0019 | filled | 80849.8 | lev 10 | intent add_short
 5097c596 | 0.0019 | filled | 80836.1 | lev 10 | intent add_short
 f6b97c09 | 0.0019 | filled | 80866.1 | lev 10 | intent add_short
 b329dbd1 | 0.0019 | filled | 80872.0 | lev 10 | intent add_short

 2bf2123f | social-btc-astro | BTC-USDT | short | 0.0154 | 78735.84 | open

 social_shadow_orders -202609183..186: ADD acted manual_add add_size ~0.001856

GET .../positions  BTC-USDT short 0.0154 entry 78735.84 leverage 10 liq 86298.75
GET .../trigger-orders  tp 74500.0 sz 7.7 | sl 85542.5 sz 15.4
```

## Worth knowing

- 7.6 contracts were added, not 7.8: each unit rounds to 1.9 contracts at this price.
- The stop (85,542.5) is the liquidation-safety stop, ~8.6% above the blended entry.
- TP1 stays half-size only until the next `adjust-stops` / `modify-stops`; re-place it
  after any stop change. Script deleted from the containers.
