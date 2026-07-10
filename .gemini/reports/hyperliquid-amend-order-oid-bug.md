# Hyperliquid amend_order: lost-oid bug (root cause + fix + live verification)

## The bug

Follow-up to the "pending order not visible anywhere" investigation. Root cause found by
querying Hyperliquid's own `historicalOrders` for the real order lifecycle (ground truth,
independent of our DB):

Hyperliquid's `modify` action is cancel-and-replace under the hood: the resting order comes
back under a **brand new oid**, not the same one. `order-executor/app/adapters/hyperliquid.py`'s
`amend_order()` didn't know this — on a successful ack it always returned
`{"order_id": order_id}`, echoing the *input* (now-dead) oid. `order-listener` then wrote that
stale id back into `orders.exchange_order_id`. The next reconciler pass polled the exchange for
that dead id, found it gone, and marked the order `cancelled` — while the *real* replacement
order kept resting live on the exchange, invisible to the DB, dashboard, and reconciler, for as
long as it lasted.

Confirmed via `historicalOrders` for the original incident (`ai-signal-generator` → ETH AI
Geometric Range): old oid `56272857224` cancelled and new oid `56274925490` opened at the
**identical timestamp** (1783685176036) — i.e. genuinely one atomic replace event, immediately
misfiled by our system as a plain cancel.

## First fix attempt — wrong, caught immediately by live testing

Assumed Hyperliquid's modify ack carried the new oid in `response.data.statuses[0].resting.oid`
(mirroring order placement's ack shape). Deployed, then tested live against a real resting order
on `ai-btc-6f8c` (BTC AI Regime router, Hyperliquid demo account) via the exact webhook path the
AI's dispatch node uses. The real response was `{"status":"ok","response":{"type":"default"}}` —
**no statuses array at all** for a modify ack, unlike order placement's `{"type":"order", ...}`.
So the first fix silently fell through to the same stale-id fallback. Live test proved this:
adapter returned `order_id: 56274977349` (old/dead) while the exchange had actually replaced it
with `56277326772` at the new price.

## Actual fix

Since Hyperliquid's modify ack carries no oid, `amend_order()` now re-queries open orders
(`_fetch_frontend_open_orders()`, same helper the method already used to look up the pre-modify
order) after a successful ack, up to 3 attempts with a 0.3s backoff, and identifies the
replacement as the highest-oid resting order matching the original order's `coin`/`side` that
isn't the input oid. Falls back to the old oid (with a warning log) only if no replacement is
found after retries — e.g. an immediate fill instead of a resting replacement.

## Live verification (real Hyperliquid testnet calls, not mocked)

1. Redeployed `order-executor` with the fix.
2. Triggered a manual AI cycle for `ai-btc-6f8c` (`POST
   ai-signal-generator:8005/internal/schedulers/ai-btc-6f8c/trigger`) — the AI held (unclear
   regime), so it didn't naturally exercise amend_order this cycle.
3. Called the exact webhook path the AI's dispatch node uses
   (`order-listener:8001/strategies/ai-btc-6f8c/orders/amend`) directly against the strategy's
   live resting order — this is what first proved the initial fix incomplete (see above).
4. After the real fix, called `order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/orders/amend`
   directly against the still-resting order (`56277326772` @ 64500.0), amending to 64510.0:
   ```
   {"success":true,"order_id":"56277441821","raw_response":{"status":"ok","response":{"type":"default"}}}
   ```
5. Cross-checked against Hyperliquid's live open-orders for the account:
   ```
   [{"order_id":"56277441821","symbol":"BTC-USDT","side":"sell","price":64510.0, ...}]
   ```
   Adapter-returned oid matches the exchange's actual resting order exactly.
6. `GET dashboard-api:8003/strategies/tree` for `ai-btc-6f8c` now shows the pending order with
   correct price/sl/tp and a live mark price (63299.4), fully consistent end-to-end.

## Side-effect cleanup

The live testing sequence (steps 2-3, before the real fix was deployed) reproduced the bug for
real on `ai-btc-6f8c`'s live resting order, leaving its DB row marked `cancelled` while a real
order kept resting on the exchange (oid drifted `56274977349` → `56277326772` → `56277441821`
across the test steps). Manually corrected the DB row back to `status='pending'` with the
correct final `exchange_order_id` (`56277441821`) and `price` (64510.0) to match live exchange
state — verified via the `/strategies/tree` check above.

## Blofin: checked, not affected

`order-executor/app/adapters/blofin.py`'s `amend_order()` (lines 1004-1074) is explicit
cancel-then-place: it calls `self.submit_order(replacement)` for the new order and returns
`"order_id": result.exchange_order_id` taken directly from that real placement's result — never
assumes the id is unchanged. No equivalent bug.
