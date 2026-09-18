# Social short restored + doubled, false "Closed on exchange" fixed, account-down alerts (2026-09-18)

## Request

After a new Blofin demo API key was saved: (1) add to the still-live BTC short per the
original prompt (double size, 10x, TP1 half at 74,500) and restore it in the app;
(2) fix the bug that marked the position "Closed on exchange"; (3) send a notification
whenever an account stops working.

## 1. Position restored and doubled

New key confirmed working; the 2026-09-16 short was still on the venue untouched:

```
GET .../balance   {"total_balance":1070.74,"available_balance":1045.70,"used_margin":25.04}
GET .../positions [{"symbol":"BTC-USDT","side":"short","size":"0.0040","entry_price":"76120.8","leverage":10,"liquidation_price":"83432.52"}]
GET .../trigger-orders/BTC-USDT  tp 74500 sz 2 | sl 83386.9 sz 2 | sl 83386.6 sz 2
```

Restored in the DB (the reconciler's false close reverted):

```
UPDATE strategy_positions SET status='open', closed_at=NULL, closing_order_id=NULL,
  close_reason=NULL, closing_price=NULL, pnl_realized=NULL, reconcile_miss_count=0
  WHERE id='2bf2123f-…'                                                     -> UPDATE 1
UPDATE orders SET status='cancelled', error_msg='false close: exchange read failed (API key 152401) …'
  WHERE id='9f1847df-…' (the reconciler's exchange_close row)               -> UPDATE 1
INSERT social_position_state (BTC, SHORT, OPEN, last_msg_id -20260916, tp_price 74500) -> INSERT 1
```

Doubled with two standard 10x entries (`add_short`, one margin unit each — order-listener
clamps a single order to one unit) via the listener's own webhook path:

```
add 1/2: mark=77251.6 unit=0.00194171
webhook: 200 {"order_id":"4ba5eaed-a84a-43f4-aeeb-f9cf04065e54"}   position -> 0.0059 entry 76482.86
add 2/2: mark=77251.4 unit=0.00194171
webhook: 200 {"order_id":"a94d44fe-e32d-4ea2-8975-d821ba569bf1"}   position -> 0.0078 entry 76670.05
```

TP1 (half of the new total = 3.9 contracts). First an extra 1.9-contract TP was placed
next to the existing 2-contract one. One minute later the reconciler's liquidation guard
found the two new per-fill stops (84,625) above the blended liquidation price (84,034),
tightened the SL to 83,298.1 and — as `modify-stops` always does — re-placed the TP at
full size. The full-size TP was then cancelled and a 3.9-contract TP placed through the
adapter (no exchange call outside order-executor):

```
reconciler: UNSAFE SL detected … active_sl=84625.1 liquidation_price=84034.53
reconciler: liquidation-safety TIGHTENED SL … 84625.1 -> 83298.1 (tp_preserved=74500.0)
before: sl 83298.1 sz 7.8 | tp 74500 sz 7.8
cancel tp 10003948182 {"success": true}
place tp1: {"success": true, "placed": [{"tpsl": "tp", "oid": "10003948188"}]}
after:  tp 74500 sz 3.9 | sl 83298.1 sz 7.8
```

Final state:

```
 4ba5eaed | open_short | 0.0019 | filled | 77245.1 | lev 10 | intent add_short
 a94d44fe | open_short | 0.0019 | filled | 77251.3 | lev 10 | intent add_short
 9f1847df | exchange_close | cancelled (reverted false close)

 2bf2123f | short | 0.0078 | entry 76670.05 | open | miss_count 0
 social_position_state: BTC SHORT OPEN last_msg_id -20260916 tp_price 74500
 social_shadow_orders: -202609181 ADD acted manual_add | -202609182 ADD acted manual_add

GET .../positions        BTC-USDT short 0.0078 entry 76670.05 liquidation 84034.53
GET .../trigger-orders   tp 74500.0 sz 3.9 | sl 83298.1 sz 7.8
```

## 2. Bug: dead API key read as "no positions"

Cause: BloFin answers an auth failure with **HTTP 200** and an error `code` in the body
(`{"code":"152401","msg":"Access key does not exist"}`, no `data`). `BlofinAdapter`'s
readers took `data.get("data", [])` → `[]` → order-executor returned a *confirmed empty*
positions list → the reconciler counted three misses and closed the position in the DB.
The reconciler / route contract ("None/503 = unknown, never []") was right; the adapter
broke it.

Fix (`order-executor/app/adapters/blofin.py`): new `_data_or_unavailable()` checks the
body's `code` and raises `ExchangeUnavailableError` (→ 503 from the positions route →
`get_account_positions` returns None → reconciler skips the account). Applied to
`get_open_positions`, `get_open_orders` (now re-raises instead of returning `[]`) and
`list_trigger_orders` (→ None). Tests in `tests/test_blofin_api_error_reads.py`:

```
docker compose run … pytest tests/test_blofin_api_error_reads.py tests/test_blofin_hedge.py
  tests/test_blofin_close.py tests/test_modify_stops_preserve.py
47 passed, 1 warning in 43.04s
```

Deployed and checked in the container: `grep -c _data_or_unavailable blofin.py` → 4;
positions route still returns the live short with the good key.

## 3. Account-down notifications

`notification-service` health watcher now probes every active `exchange_accounts` row
through order-executor's `/accounts/{id}/balance` every 120 s (`account_poll_interval_s`,
timeout 20 s for homelab load). Any `error` in the answer, a non-200, or no answer counts
as a miss; after 2 consecutive misses (`account_fail_threshold`) it emits `account.down`,
and `account.up` once the account answers again. Rendered as a push like the existing
service/exchange alerts (`render.py`, dedup key `account:<id>:down|up`, 24 h window).

Verified in the container with emit captured (no real push sent): real probe of both
accounts OK, a simulated dead-key answer trips `account.down` on the 2nd miss, recovery
emits `account.up`:

```
real probe: None
after real check: fails={'blofin-blofin-demo-v5vr': 0, 'hyperliquid-hyperliquid-hqdy': 0} events=[]
dead pass 1: fails=1 events=[]
dead pass 2: state={…: False} events=['account.down', 'account.down']
recovered: state={…: True} events=['account.down','account.down','account.up','account.up']
account:blofin-blofin-demo-v5vr:down {"title": "🚨 Account not working: Blofin Demo",
  "body": "blofin demo account blofin-blofin-demo-v5vr is rejecting calls — orders and stop
  changes will fail, and open positions cannot be watched. Blofin API error 152401: Access key does not exist"}
account:blofin-blofin-demo-v5vr:up {"title": "✅ Account working again: Blofin Demo", …}
```

Both services redeployed with `./scripts/redeploy.sh` (`order-executor`,
`notification-service`), state `running`.

## Worth knowing

- The stop is the platform's liquidation-safety stop (83,298.1, ~8.6% above the blended
  entry), not a trading stop.
- TP1 (3.9 contracts) is still lost on any later `adjust-stops` / `modify-stops` — they
  re-place triggers at full position size. Re-place the half TP after any stop change.
- Scripts ran as `/app/manual_add2.py` (social-listener), `/app/manual_tp1.py`,
  `/app/manual_tp1_fix.py` (order-executor), `/app/probe_test.py` (notification-service);
  all deleted afterwards.
