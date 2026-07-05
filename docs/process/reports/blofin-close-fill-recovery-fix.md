# Fix: recover Blofin close fills when the close response has no orderId, and persist close raw_response

Two independent fixes, per `docs/process/reports/blofin-close-orderid-investigation.md`:
Blofin's `/api/v1/trade/close-position` returns a dict-shaped `data` with no `orderId` on
~50% of full closes (structural, not a timing flake), and `orders.raw_response` was never
populated for any close order in the system (0/94), so blank closes couldn't even be
inspected retroactively.

## Phase 1 — Persist `raw_response` on closes

**What changed:** the Blofin adapter already returned `raw_response=data` on close
`OrderResult`s, and the executor's two close endpoints (`/close-position`,
`/accounts/{id}/positions/close`) already return the full `OrderResult` object — so
nothing needed to change on the executor side. The gap was entirely in
`order-listener/app/webhook_handler.py`: `close_strategy_position`'s return dict dropped
`raw_response` from the executor's response, and both places in `_process_order` that
rebuild an `OrderResult` from that dict (`close_long`/`close_short` and the
`target_position: "flat"` handler) never carried it forward, even though
`_update_order_status` already persists `result.raw_response` when present.

Fixed by threading `raw_response` through: `close_strategy_position`'s `ret` dict now
includes `ret["raw_response"] = close_result.get('raw_response')`, and both `OrderResult(...)`
constructions in `_process_order` now pass `raw_response=close_result.get("raw_response")`.

**Verification — real webhook path, not a synthetic close:**

All enabled strategies on the Blofin demo account (`blofin-blofin-demo-v5vr`) are live
(`tv_test_harness`'s webhook secret is literally `shadow-only-no-webhook`, deliberately
set during the signal-engine work to block manual webhook injection). Per user direction,
used `sui-manual-59d9` (real webhook secret, demo account, no real funds) for a small
open+close round trip on top of its existing 113 SUI position — user explicitly accepted
the resulting small entry-price averaging as an acceptable side effect.

Open (5 SUI):
```
$ curl -s -X POST http://localhost/api/listener/webhook/sui-manual-59d9 \
  -H "Content-Type: application/json" \
  -d '{"base_asset":"SUI","quote_asset":"USDT","side":"buy","order_type":"market","size":5,
       "signal":"open_long","timestamp":"...","token":"0dec0da7ccccfc9ec205f8a939f7b069",
       "signal_source":"raw-response-verification"}'
{"order_id":"1d6de8fa-8092-4f49-aaaf-09b2a4b78399","status":"received","message":"OK"}

strategy_positions: size 113 -> 118 @ entry_price 0.7525847457627118644067796610
```

Close (same 5 SUI, exercising the exact code path fixed above):
```
$ curl -s -X POST http://localhost/api/listener/webhook/sui-manual-59d9 \
  -H "Content-Type: application/json" \
  -d '{"base_asset":"SUI","quote_asset":"USDT","side":"sell","order_type":"market","size":5,
       "signal":"close_long","timestamp":"...","token":"0dec0da7ccccfc9ec205f8a939f7b069",
       "signal_source":"raw-response-verification"}'
{"order_id":"242b8193-8a74-4da2-9a05-e77dfc944809","status":"received","message":"OK"}

$ docker compose exec postgres psql -U matp -d matp -c \
  "SELECT id, signal, status, actual_fill_price, exchange_order_id, exchange_fee, pnl, raw_response
   FROM orders WHERE id='242b8193-8a74-4da2-9a05-e77dfc944809';"
                  id                  |   signal   | status | actual_fill_price | exchange_order_id | exchange_fee |         pnl
--------------------------------------+------------+--------+-------------------+-------------------+--------------+---------------------
 242b8193-8a74-4da2-9a05-e77dfc944809 | close_long | filled |            0.7543 | 1000131661656     |    0.0022629 | 0.00857627118644068
raw_response: {"msg": "", "code": "0", "data": [{"msg": "Order placed", "code": "0", "orderId": "1000131661656", "clientOrderId": ""}]}

strategy_positions size restored to 113 (partial close of 5 SUI applied against the
118 topped-up position), entry_price unchanged from the open leg above.
```

`raw_response` is now non-NULL on a fresh close order — previously 0/94 close orders ever
had this populated.

## Phase 2 — Order-level fill-recovery fallback (Blofin adapter)

**What changed:** added `_recover_close_fill(symbol, close_side, size, since_ms)` to
`order-executor/app/adapters/blofin.py`. It queries `orders-history`, falling back to
`fills-history`, for the instrument **without** an orderId filter, and matches candidates
on: `reduceOnly == "true"`, `side == close_side`, `state == "filled"`, `createTime` within
`[since_ms, since_ms + _RECOVERY_WINDOW_MS]` (`_RECOVERY_WINDOW_MS = 5000` — the
investigation observed a real match at ~359ms; this is generous headroom, not a measured
bound), and — if a size hint is known — `filledSize` within `_RECOVERY_SIZE_TOLERANCE`
(`Decimal("0.02")`, i.e. 2% relative) of the size converted to contracts via the existing
`_to_contracts` helper.

**Tie-break rule, exactly as implemented:**
- Zero candidates after filtering → return `None`, log a warning (checked against both
  `orders-history` and `fills-history` before giving up).
- Exactly one candidate → return it.
- Multiple candidates → narrow to the tightest `|createTime - since_ms|`; if that's still
  not unique, narrow further to exact `filledSize` match; if **still** tied → return `None`
  and log the tied `orderId`s. Never guesses between tied candidates.

Wired into both call sites:
- `close_position` (full close, required): since the close-position endpoint's request
  never carries a size, added a read-only pre-close lookup via the existing
  `get_open_positions()` to capture the position's live size as the matching hint (never
  used to size the actual close call — close-position always closes the whole position).
  When `order_info.get("orderId")` is empty, calls `_recover_close_fill` with that captured
  size and the exchange-side mapping (`"sell" if side=="long" else "buy"`, same convention
  as `_partial_close`).
- `_partial_close` (parity): already has the exact `size` argument in hand, so passes it
  directly to the same fallback when `orderId` is missing.

In both cases, once a fill is recovered, its `orderId`, `averagePrice`/`pnl`/`fee` fields
are read directly off the recovered history entry via the existing `_parse_fill_price` and
the same `pnl`/`fee` extraction already used for the normal orderId path — no new parsing
logic duplicated.

**Verification — deterministic replay against the real blank close from the investigation
(HYPE-USDT, `close_short` → exchange side `buy`, since_ms=1783257576000), read-only, no
order placed/closed/amended/cancelled:**

First attempt used size=5 (the raw `orders.size` DB value for that close) and returned no
match — because `orders.size` stores the **raw webhook payload size**, not the size actually
sent to the exchange after clamping to the open position (`close_strategy_position` does
`eff_close_size = min(close_size, current_size)`). The linked `strategy_positions` row shows
the real closed size was 2.9 HYPE (`opening_order_id`'s fill size), confirming the correct
size hint is the **live position size fetched before the close**, exactly what `close_position`
now captures via `get_open_positions()` — not the order row's `size` column.

```
$ docker compose exec order-executor python3 -c "
adapter._recover_close_fill('HYPE-USDT', 'buy', Decimal('2.9'), 1783257576000)
"
RECOVERED: {'orderId': '1000131655671', 'side': 'buy', 'reduceOnly': 'true', 'state': 'filled',
 'filledSize': '29.000000000000000000', 'pnl': '0.0348', 'averagePrice': '69.226',
 'fee': '0.12045324', 'createTime': '1783257577117', ...}
```

Matches the investigation's real recovered figures exactly: `fee=0.12045324`, `pnl=0.0348`.
Also confirmed the no-size-hint path (as would occur if the pre-close position lookup
failed) still resolves unambiguously, since only one filled reduceOnly `buy` exists in the
window:
```
RECOVERED (size=None): orderId=1000131655671 fee=0.12045324
```

**Ambiguous-tie branch**, constructed from a throwaway synthetic `orders-history` response
(the instrument cache was pre-warmed with a real read-only call first, then `httpx.AsyncClient`
was monkey-patched only for the matcher's own query — no DB row fabricated, no order placed):
two candidates with identical `side`/`reduceOnly`/`state`/`createTime`/`filledSize` (mirroring
the report's real ambiguity case — two strategies, `hype-test-7db4` + `hype-breakout-da2e`,
both trading HYPE-USDT, closing at the same size in the same window):
```
Blofin _recover_close_fill: ambiguous candidates for HYPE-USDT buy near since_ms=1783257576000
 — tied orderIds=['AAA111', 'BBB222'], leaving exchange_order_id/fill/pnl/fee unresolved
AMBIGUOUS RESULT (expect None): None
```

## Deploy

```
./scripts/redeploy.sh order-listener   # Phase 1
./scripts/redeploy.sh order-executor   # Phase 2
```

Confirmed the running containers serve the new code, not just the host source tree:
```
$ docker compose exec -T order-listener grep -n 'ret\["raw_response"\]\|raw_response=close_result' /app/app/webhook_handler.py
1246:        ret["raw_response"] = close_result.get('raw_response')
1460:                    raw_response=close_result.get("raw_response"),
1508:                    raw_response=close_result.get("raw_response"),

$ docker compose exec -T order-executor grep -n '_recover_close_fill\|_RECOVERY_WINDOW_MS' /app/app/adapters/blofin.py
26:_RECOVERY_WINDOW_MS = 5000
188:    async def _recover_close_fill(
...
674:                    details = await self._recover_close_fill(symbol, reduce_side, position_size_before, since_ms)
767:                details = await self._recover_close_fill(symbol, reduce_side, size, since_ms)
```

Both services healthy post-deploy:
```
$ docker compose ps order-listener order-executor
matp-order-executor-1   Up (healthy)
matp-order-listener-1   Up (healthy)

$ docker compose exec nginx wget -qO- http://order-listener:8001/health
{"status":"ok","service":"order-listener"}
$ docker compose exec nginx wget -qO- http://order-executor:8004/health
{"status":"ok","service":"order-executor","version":"1.0.0"}
```

No regressions: the only `ConnectError` warnings in the post-deploy log window happened at
17:55:36, exactly during the order-executor container's own recreate (it came back healthy
at 17:55:54) — a transient effect of the redeploy itself, not a code defect. No pre-existing
failing test is attributed to this work.

## Scope notes

- Hyperliquid adapter, the open path, and the reconciler were not touched, per the task's
  guard rails.
- `get_closed_position_details`/`positions-history` was not repurposed — it remains
  position-lifecycle granularity (round-trip fee, inverted sign) and is unrelated to this
  order-level fix.
- No migration: `orders.raw_response` and `orders.exchange_fee` already existed.
- All exchange calls added are read-only GETs (`get_open_positions`, `orders-history`,
  `fills-history`). No order was placed, closed, amended, or cancelled by the fix itself;
  the only live orders placed were the user-approved manual open/close pair on
  `sui-manual-59d9` used for Phase 1 verification.
