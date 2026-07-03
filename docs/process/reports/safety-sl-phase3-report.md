# Phase 3: after-fill liquidation-safety guard

Date: 2026-07-03
Continues: docs/process/reports/safety-sl-fix-report.md (Phases 1 & 2, `3b288e6`)
Investigation: docs/process/reports/safety-sl-vs-liq-investigation.md
Scope: Phase 3a (detect + backfill) + Phase 3b (tighten). Both landed together after
3a was confirmed against live data.

## Why this phase exists

Phases 1/2 fixed the SL math at OPEN time, using a live per-symbol MMR plus a 0.15%
conservatism buffer — but that buffer is thin (HYPE-USDT landed ~0.02% inside real
liquidation) and the open-time formula can't see the exchange's actual liquidation
price, which drifts with funding. This phase adds a correct-by-construction guard:
after a position is open, cross-check the real placed SL against the exchange's own
reported `liquidation_price` and tighten if it's ever on the wrong side. It also
retroactively protects positions opened before the Phase 1/2 fix.

## Authoritative "current SL" source — and why

Used: the exchange's own resting SL trigger order, read via a new route,
`GET /accounts/{account_id}/trigger-orders/{symbol}` (order-executor), wrapping each
adapter's existing `list_trigger_orders()`. Exposed to the listener via
`executor_client.get_trigger_orders()`.

Why not the DB: `strategy_positions` has **no `sl_price` column at all**.
`orders.sl_price` only reflects the value at the position's **original fill** — and the
existing `/strategies/{id}/adjust-stops` management route (webhook_handler.py:456) can
change a position's live stop **without writing anything back to the DB**. So the DB can
silently go stale the moment anyone (a strategy, a human via that route) adjusts stops
post-open; only the exchange's own resting trigger order reflects what will actually
fire. Risk if this fallback were used instead: a stop moved via `/adjust-stops` after
the original fill would be invisible to this guard, which could then either wrongly
flag a deliberately-adjusted-but-safe stop as unsafe (comparing against the stale DB
value) or, worse, miss a real unsafe DB-recorded value that was already fixed on the
exchange. Reading the exchange directly avoids both failure modes.

Consequence: `modify-stops` (both adapters) **cancels all resting triggers and only
re-places what's explicitly passed** — so tightening the SL requires reading the
current TP trigger first and passing it straight through, or it's silently dropped.
Implemented in `_guard_liquidation_safety()`.

## Phase 3a — detection + backfill

Added to `reconcile_once()`'s existing per-account loop (reuses the `positions` list
already fetched for size-reconciliation — no extra position read):
- Backfills `strategy_positions.liquidation_price` (was always `NULL`) from the live
  position's `liquidation_price`, only writing on change.
- Compares the resting SL trigger against `liquidation_price`:
  `short: unsafe if active_sl >= liq_price`, `long: unsafe if active_sl <= liq_price`.
- If no resting SL trigger exists, no comparison is made (guard never invents a stop
  for a position it never touched) — see the self-heal exception in 3b below.

### Live verification (real production data, not constructed)

Both currently-open positions turned out to be genuinely unsafe at the moment this
guard first ran — one is the exact case from the original investigation, still
carrying its pre-fix stop; the other is a stop that had drifted far out of range:

```
order-listener-1 | [WARNING] app.reconciler: reconciler: UNSAFE SL detected for position
  9be7efbf-2af2-4356-82f2-90ad4fa3c674 (HYPE-USDT short) active_sl=98.100000000000000000
  liquidation_price=71.769902582751985064 — SL is on the wrong side of live liquidation

order-listener-1 | [WARNING] app.reconciler: reconciler: UNSAFE SL detected for position
  cb73ec38-c9ca-4ad3-8a35-a6fcb827cb09 (BTC-USDT short) active_sl=62633.0
  liquidation_price=62447.7776790123 — SL is on the wrong side of live liquidation
```

No false positive on the third open position (`SUI-USDT`, long, already safe):
active SL `0.6593` vs. live liq `0.630063` — correctly not flagged (checked directly,
did not appear in the WARNING logs).

`liquidation_price` backfill confirmed (previously always `NULL`):

```
$ docker compose exec postgres psql -U matp -d matp -c "SELECT id, symbol, side, liquidation_price FROM strategy_positions WHERE status='open';"
                  id                  |  symbol   | side  |   liquidation_price
--------------------------------------+-----------+-------+-----------------------
 9be7efbf-2af2-4356-82f2-90ad4fa3c674 | HYPE-USDT | short | 71.769902582751985064
 3cb26cf7-c672-4daa-9fac-3f5e383a828e | SUI-USDT  | long  |  0.630063450498539631
 cb73ec38-c9ca-4ad3-8a35-a6fcb827cb09 | BTC-USDT  | short |      62447.7776790123
(3 rows)
```

`updated_at` on all three matched the exact reconciler-pass timestamp, confirming this
pass performed the backfill (not stale data from a prior manual query).

## Phase 3b — tighten

### Safe-margin value + rationale

`new_sl = liq_price ∓ margin`, where
`margin = clamp(distance * 10%, entry_price * 0.05% floor, distance * 90% cap)` and
`distance = |liquidation_price - entry_price|`.

10% of the entry→liquidation distance was chosen because it's proportional (scales
correctly across symbols/leverage, unlike a fixed price offset) and lands in the same
ballpark as the Phase 1/2 open-time conservatism buffer — confirmed on the live BTC
40x case: Phase 1/2's own live-MMR formula computed `62371.6`; Phase 3's
distance-based tighten independently landed at `62372.3` (within 0.001%), a strong
cross-check that both approaches agree on what "safe" means here. The 0.05%-of-entry
floor guards the case where `distance` itself is tiny (extreme leverage) and the cap
prevents a pathological tiny-distance case from pushing the new SL past entry.

### Live verification — both real unsafe positions tightened

```
order-listener-1 | [WARNING] app.reconciler: reconciler: liquidation-safety TIGHTENED SL
  for 9be7efbf-2af2-4356-82f2-90ad4fa3c674 (HYPE-USDT short): 98.100000000000000000 ->
  71.1651 (liq=71.769902582751985064, margin=0.60482454267464627450, tp_preserved=None)

order-listener-1 | [WARNING] app.reconciler: reconciler: liquidation-safety TIGHTENED SL
  for cb73ec38-c9ca-4ad3-8a35-a6fcb827cb09 (BTC-USDT short): 62633.0 -> 62372.3
  (liq=62447.7776790123, margin=75.477767901230, tp_preserved=57500.0)
```

`71.1651 < 71.7699` and `62372.3 < 62447.78` — both **inside** real liquidation.
BTC-USDT's existing TP (`57500.0`) was correctly carried through the modify-stops call
(would otherwise have been silently cancelled with no replacement).

### A real bug this surfaced (found and fixed live, not hypothetical)

The first BTC-USDT tighten attempt exposed a pre-existing bug in
`HyperliquidAdapter.place_trigger_orders()`: it returns `{"success": True, ...}` at the
top level as long as the signed action itself was accepted by Hyperliquid, **even when
an individual TP/SL leg inside that action was rejected** (the `placed` list carries a
per-leg `error`, but the caller — `modify_stops` in `main.py`, and therefore
`call_executor_modify_stops` — never inspects it). Live testnet hit exactly this: one
attempt returned `success: True` while HL rejected the SL leg with
`"Only post-only orders allowed immediately after network upgrade"`. Since
`modify-stops` cancels-then-places (not atomic), this transiently left the BTC-USDT
position with **zero resting stops** — worse than the original bug, briefly.

Confirmed live:
```
order-executor-1 | [WARNING] app.adapters.hyperliquid: HL trigger leg (tp) error: Only
  post-only orders allowed immediately after network upgrade
$ curl .../accounts/hyperliquid-hyperliquid-hqdy/trigger-orders/BTC-USDT
[]   # nothing resting — position briefly unprotected
```

Fixed two ways (both now in `_guard_liquidation_safety`):
1. **Verify, don't trust.** `_place_and_verify()` calls modify-stops, then reads the
   trigger orders back and only accepts success if a resting SL is confirmed present
   AND on the safe side of `liquidation_price`. Retries up to 3 times (1.5s apart) —
   this specific rejection proved transient (a manual retry ~1 minute later placed
   both legs cleanly).
2. **Self-heal across passes.** If a position has no resting SL trigger at all AND its
   opening order's `signal_metadata` carries `liq_safety_tightened: true` (meaning a
   prior pass of *this guard* is responsible for that stop existing), a missing SL is
   now treated as unsafe and re-established — rather than silently skipped under the
   "never invent a stop" rule, which was designed for positions this guard never
   touched, not for a gap the guard itself just caused.

Both fixes deployed and the position re-verified protected (see below).

### Deadband proof — no thrash across three subsequent reconcile cycles

Watched three full ~64s reconcile passes after both tightens landed:

```
order-listener-1 | [INFO] app.main: Reconciler: automatic pass complete
order-listener-1 | [INFO] app.main: Reconciler: automatic pass complete
order-listener-1 | [INFO] app.main: Reconciler: automatic pass complete
```

No `UNSAFE` or `TIGHTENED` log lines in any of the three passes for either position.
Stronger proof than log absence alone — the exchange order IDs themselves are
unchanged across all three checks (a re-fire would cancel and re-place, minting new
IDs):

```
$ curl .../hyperliquid-hyperliquid-hqdy/trigger-orders/BTC-USDT
[{"oid":55892566616,"tpsl":"sl","triggerPx":"62372.0",...},{"oid":55892566615,"tpsl":"tp","triggerPx":"57500.0",...}]
$ curl .../blofin-blofin-demo-v5vr/trigger-orders/HYPE-USDT
[{"oid":"10002462612","tpsl":"sl","triggerPx":"71.165100000000000000",...}]
```

Same `oid`s before and after the three-cycle wait. `liq_safety_tightened_at` in
`signal_metadata` also unchanged across the wait (would update on any re-tighten):

```
$ psql ... "SELECT o.id, o.signal_metadata->>'liq_safety_tightened_at' FROM orders o
  JOIN strategy_positions sp ON sp.opening_order_id = o.id
  WHERE sp.status='open' AND o.signal_metadata->>'liq_safety_tightened'='true';"
 eaea133c-... | 2026-07-03T05:30:44.759167+00:00
 36d6efb7-... | 2026-07-03T05:31:55.187691+00:00
```

`SUI-USDT` (already safe, never touched by this guard) confirmed to carry no
`liq_safety_tightened` key at all — the guard never fires on an already-safe position:

```
$ psql ... "SELECT o.signal_metadata ? 'liq_safety_tightened' FROM orders o
  JOIN strategy_positions sp ON sp.opening_order_id=o.id WHERE sp.symbol='SUI-USDT';"
 f
```

### Auditability

`orders.signal_metadata` (the opening order, via `strategy_positions.opening_order_id`)
gets merged with:
```json
{
  "liq_safety_tightened": true,
  "liq_safety_tightened_at": "<ISO8601>",
  "liq_safety_prior_sl": <float or null>,
  "liq_safety_new_sl": <float>,
  "liq_safety_liquidation_price": <float>
}
```
`liq_safety_prior_sl` is `null` specifically for the self-heal case (no resting SL
existed to record a "prior" value from). Distinguishable from the Phase 1/2 open-time
fields (`sl_source`, `mmr`, `mmr_source`, `entry_ref`) already in the same object.

## Deploys

```
./scripts/redeploy.sh order-executor   # new /accounts/{id}/trigger-orders/{symbol} route
./scripts/redeploy.sh order-listener   # 3a detection+backfill, then 3b tighten+verify+self-heal
```

Both confirmed healthy after each redeploy.

## Not done / out of scope

- No migration — confirmed `strategy_positions.liquidation_price` already existed
  (`\d strategy_positions`) before writing any code.
- The `place_trigger_orders` per-leg-error-vs-top-level-success mismatch in
  `HyperliquidAdapter` (and the parallel ambiguity in `BlofinAdapter.amend_order`'s
  documented cancel-then-replace risk) is a general `modify_stops`/adapter contract
  issue beyond this guard's scope — worked around here via verify+retry+self-heal,
  not fixed at the adapter layer. Worth a backlog entry if other callers of
  `modify_stops` (e.g. `/adjust-stops`, the post-fill path in
  `_reconcile_pending_orders`) should get the same verification treatment.
