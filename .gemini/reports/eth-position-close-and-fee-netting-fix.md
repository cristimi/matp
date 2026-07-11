# ETH position closed with no AI reasoning + missing/unnetted exchange fees — root cause, fix, verification

## User report

> ai eth strategy, once more a position closed without clear explanation why (ai reasoning) i
> suspect a bug in the code. moreover, the transaction fees are not reported either.

Investigated `eth-ai-34d2` position `7f9709d0-474a-45fd-92b0-43ee8212fa33`
(opened 2026-07-11 00:10:54, closed 00:14:08, ~3 min later, near-breakeven). Found two
distinct, confirmed bugs. Both fixed, deployed, tested live, and backfilled.

## Bug 1 — `amend_order` drops the LLM's re-fitted SL/TP

The ETH strategy trades a channel: it places a resting limit order, then re-amends it
repeatedly as the channel re-fits (5 amends over 9h for this order: 1768.78 → 1786.97 →
1789 → 1791.5 → 1791.5 → **1796.81**). Every amend's reasoning explicitly recomputed a new
SL/TP (e.g. "SL 0.75×ATR below new boundary, TP at upper boundary 1818.79") — but:

- `ai-signal-generator/app/graph/nodes/node_guard.py` hardcoded
  `resolved_sl_price`/`resolved_tp_price` to `None` for the `amend_order` action (unlike
  `adjust_stops`, which correctly resolves them from the LLM's `new_sl_price`/`new_tp_price`).
- `ai-signal-generator/app/webhook/dispatcher.py::dispatch_amend_order` only ever sent
  `order_id`/`new_price` to the listener.
- `order-listener/app/webhook_handler.py::amend_order_for_strategy` only persisted
  `price`/`size` onto the order row, never `tp_price`/`sl_price`.

Net effect: the order's stored `tp_price` stayed frozen at whatever the **original**
placement computed (1783.11, based on an entry price 9 hours and 5 amends earlier), while
the limit price kept climbing. By the time the order finally filled at 1797.24, its stored
TP (1783.11) was **below** the entry — already-satisfied for a long. When the order filled,
`reconciler.py::_reconcile_pending_orders` applied that stale TP via `modify-stops`, and
Hyperliquid's exchange-side TP fired almost immediately — explaining the ~3-minute,
near-breakeven, exchange-initiated close with no linked `ai_signal_log` entry (the close
was never routed through our webhook path, so there was nothing to log a reason against).

### Fix
- `node_guard.py`: resolve `new_sl_price`/`new_tp_price` for `amend_order`, same as
  `adjust_stops`.
- `dispatcher.py`: forward `tp_price`/`sl_price` in the amend POST body when resolved.
- `webhook_handler.py::amend_order_for_strategy`: accept and persist `tp_price`/`sl_price`
  onto the pending order row (`COALESCE`, only overwrites when provided).
- `node_analyze.py` / `prompt/builder.py`: documented the field semantics and added an
  explicit prompt instruction so the LLM is told to set these for `amend_order` too.

### Verification (live, against the real ai-signal-generator/order-listener containers)
```
$ docker compose exec ai-signal-generator python3 -c "... node_guard(state) with action=amend_order ..."
gate_passed: True
resolved_sl_price: 1788.37
resolved_tp_price: 1818.79
resolved_limit_price: 1796.808477
resolved_target_order_id: 56294091697

$ docker compose exec ai-signal-generator python3 -c "... dispatch_amend_order(state) ..."
captured body: {"token": "shh", "order_id": "56294091697", "new_price": 1796.808477,
                "tp_price": 1818.79, "sl_price": 1788.37}

$ docker compose exec order-listener python3 -c "... POST /strategies/strat1/orders/amend ..."
status: 200 {'success': True, 'order_id': 'NEWOID999'}
SQL: UPDATE orders SET exchange_order_id = $1, price = COALESCE($2, price),
     size = COALESCE($3, size), tp_price = COALESCE($4, tp_price),
     sl_price = COALESCE($5, sl_price) WHERE exchange_order_id = $6 ...
args: ('NEWOID999', Decimal('1796.808477'), None, Decimal('1818.79'), Decimal('1788.37'),
       'OLDOID111', 'strat1')
```

## Bug 2 — exchange fees mostly uncaptured, and never netted into PnL

`orders.exchange_fee` was only ever fetched **synchronously at placement time**
(`HyperliquidAdapter.place_order` / `BlofinAdapter.submit_order`), for fills that complete
immediately. Any order that rests and fills **later** — i.e. essentially every limit-order
entry this and other range/channel strategies place — is picked up asynchronously by
`reconciler.py::_reconcile_pending_orders`, which built its `OrderResult` with no fee at
all. Fleet-wide, before the fix: `ai_engine` 11/45 filled orders had a fee, `reconciler`
8/47, `tradingview` 3/25, `tv_test` 45/126.

Separately, even where a fee *was* known, it was never subtracted anywhere:
`strategy_positions.pnl_realized` and the strategy's `pnl_total`/`capital_allocation`
compounding were always the gross, pre-fee figure.

### Fix
- New `ExchangeAdapter.get_order_fill_fee(symbol, order_id)` (base + Hyperliquid + Blofin
  impls, reusing each adapter's existing per-order fill/fee lookup).
- New order-executor endpoint `GET /accounts/{id}/orders/{order_id}/fee`.
- New order-listener client `get_order_fill_fee()`, wired into
  `_reconcile_pending_orders`'s fill-detection branch so async fills now get a real fee.
- Netting: `close_strategy_position` (new `close_fee` param), `sync_position_pnl`, and
  `_recover_manual_close_pnl` now all compute
  `pnl_realized = gross_pnl − close_fee − open_fee` for **position/strategy-level**
  numbers. `orders.pnl`/`orders.exchange_fee` deliberately stay raw/gross (audit trail,
  matches exchange records) — only the rollups are net.

### Verification (live, read-only exchange queries against real historical orders)
```
$ wget -qO- http://order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/orders/56299196353/fee?symbol=ETH-USDT
{"fee":"0.030482"}          # the ETH position's entry order — was NULL before

$ wget -qO- http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/orders/1000131711229/fee?symbol=HYPE-USDT
{"fee":"0.08067778"}        # Blofin path, also previously NULL

$ docker compose exec order-listener python3 -c "... close_strategy_position(..., realized_pnl=10, close_fee=0.09, open_fee(from DB)=0.05) ..."
result realized_pnl (raw, unchanged): 10
booked pnl (net = 10 - 0.05 - 0.09): 9.86
```

### Test suites (inside freshly rebuilt containers)
```
order-listener:  45 passed, 5 failed (pre-existing, unrelated — same 5 fail on the
                  pre-fix image too: auth/quote-variant/daily-cap/fill-size-fallback tests)
order-executor:  10 passed
```

## Historical backfill (user chose: net fees + backfill history)

Backfilled `strategy_positions.pnl_realized` and `strategies.capital_allocation` /
`allocation_peak` / `pnl_total` / `pnl_today` for every closed position with known fee
data, netting both legs' fees. Also extended the fee **capture** backfill itself — fetched
real fees from Hyperliquid/Blofin for 18 previously-NULL entry orders on live (enabled)
strategies (`ai-btc-6f8c`, `eth-ai-34d2`, `hype-breakout-da2e`, `sui-manual-59d9`) and
netted those too.

Mid-backfill, the just-deployed `sync_position_pnl` background loop (runs every 60s) began
auto-re-netting positions concurrently with my manual backfill script — traced this fully
via `updated_at` timestamps and reconciler pass logs, confirmed it **self-heals to the
correct idempotent value** (recomputes from source `orders` data every pass, doesn't
compound), and verified all 31 originally-affected positions converged to exactly the
expected single-netted value with zero drift, by cross-checking against the formula
independently:

```sql
-- current pnl_realized vs. freshly-recomputed formula, all 31 affected positions:
diff_vs_synced_formula = 0.000000  (for every row reachable by sync_position_pnl)
-- the 3 rows not reachable (no linked closing-order pnl) were individually verified
-- to match the one-time backfill exactly, and are never touched again.
```

Final self-consistency check (capital_allocation vs. initial_allocation + Σposition pnl):
```
ai-btc-6f8c                 drift ≈ 0 (float noise, 1e-16)
eth-ai-34d2                 drift = 0
hype-breakout-da2e          drift ≈ 0
sui-manual-59d9             drift = 0  (after fixing its one position, which has no
                                        linked closing order so sync_position_pnl can't
                                        reach it — netted directly, one-time)
tao-ai-range-rotation-d257  drift = 0
```

Two **disabled** test-harness strategies (`hype-test-7db4`, `tv_test_harness`,
`tv-btc-test-hl-94e1`) retain small pre-existing drift (0.28–0.81) between
`capital_allocation` and the raw position sum — confirmed via the same arithmetic that this
drift **predates** today's changes entirely (present before any fee data was touched) and
is unrelated to fees. Left untouched — out of scope, immaterial (all are disabled test
strategies), and not something to silently paper over.

No enabled strategy crossed into drawdown-breach territory as a result of the backfill —
checked explicitly (`capital_allocation` vs. `allocation_peak × (1 − max_drawdown_pct)`)
before and after.

## Deploy
`./scripts/redeploy.sh order-executor`, `order-listener`, `ai-signal-generator` — all
healthy (`/health` 200 on all three) post-redeploy.

## Not done (flagged, not fixed)
- ~78 more historical opening orders (mostly on disabled test/demo strategies) still have
  unknown fees — left uncaptured; fetching them is a lot of live exchange calls for
  immaterial, disabled test data. Can backfill on request.
- The pre-existing capital_allocation drift on the 3 disabled test strategies is unresolved
  (by design — unrelated to this fix, flagged above).
