# FIX: modify-stops could silently leave a position with no stop-loss

Continues: docs/process/reports/safety-sl-phase3-report.md ("A real bug this surfaced" /
"Not done / out of scope" sections). Phase 3's reconciler guard worked around the two
issues below for its own path (verify+retry+self-heal); this fixes them at the source
so every caller — `/adjust-stops`, the post-fill reconciler path, and the guard itself
— is safe.

## The two issues (recap)

1. `HyperliquidAdapter.place_trigger_orders` / `BlofinAdapter.place_trigger_orders`
   returned `{"success": True, ...}` even when a leg in `placed` carried a per-leg
   `error`. `/accounts/{id}/positions/modify-stops` propagated that top-level success
   verbatim. Because modify-stops is cancel-then-place (non-atomic), a rejected SL leg
   after the old stop was already cancelled left the position with NO stop while
   reporting `success=True`.
2. Callers trusted that lie, and reads were ambiguous: both adapters'
   `list_trigger_orders` swallowed exchange-call errors and returned `[]`, making a
   masked read failure indistinguishable from "genuinely no triggers."

## Phase 1 — honest contract

**`place_trigger_orders`** (`order-executor/app/adapters/hyperliquid.py`,
`blofin.py`): top-level `success` is now `True` only if every requested leg landed
with no `"error"` (Hyperliquid also checks the exchange returned as many `statuses`
as legs requested). `placed` is unchanged — still the per-leg breakdown.

**`list_trigger_orders`** (both adapters): returns `None` (not `[]`) on a genuine
exchange-call failure. Return type is now `Optional[list[dict]]`.

**`GET /accounts/{id}/trigger-orders/{symbol}`** (`order-executor/app/main.py`):
passes the adapter's result straight through — `null` on unknown, not `[]`. The
listener's `executor_client.get_trigger_orders` already discriminated non-list JSON
as `None`; only its docstring (which documented the old ambiguity as a known gap) was
updated.

**`POST /accounts/{id}/positions/modify-stops`** (`order-executor/app/main.py`):
- Refuses to cancel anything if the pre-cancel `list_trigger_orders` read is unknown
  (`None`) — returns `success=False` with an explicit "refusing to proceed blindly"
  error. Nothing is touched, so this is safe by construction.
- After cancel, places with a verify-read-back+retry loop (3 attempts / 1.5s apart).
  Each retry only re-requests legs **not yet confirmed landed** (matched by tpsl type
  + price within 0.1% tolerance), so a retry can never stack a duplicate trigger.
- Response gained `sl_ok`, `tp_ok` (`None` if that leg wasn't requested), `sl_oid`,
  `tp_oid`, `attempts`. `success` = every requested leg confirmed. `placed` stays
  (all attempts, concatenated) for back-compat. If SL was requested and never
  confirmed, `error_msg` explicitly says the position may be unprotected.

### Phase 1 verification (live, demo Blofin account, real network calls)

`list_trigger_orders` unknown vs. confirmed-empty:
```
=== confirmed EMPTY (real account, symbol never traded) ===
[]
=== UNKNOWN (bogus account id -> adapter load fails -> route returns null, not []) ===
null
```

Forced SL rejection on the live demo SUI-USDT long position (`tp_price=1.5` valid,
`sl_price=0` invalid):
```
BEFORE: [{"oid":"10002398602","tpsl":"tp","triggerPx":"1.5",...},{"oid":"10002398602","tpsl":"sl","triggerPx":"0.6593",...}]

POST /modify-stops {"symbol":"SUI-USDT","side":"long","tp_price":1.5,"sl_price":0}
200 {"success":false,"cancelled":[...],"placed":[{"tpsl":"tp","oid":"10002462886","status":"placed"},
  {"tpsl":"sl","error":"Parameter slTriggerPrice error."},
  {"tpsl":"sl","error":"Parameter slTriggerPrice error."},
  {"tpsl":"sl","error":"Parameter slTriggerPrice error."}],
  "sl_ok":false,"tp_ok":true,"sl_oid":null,"tp_oid":"10002462886","attempts":3,
  "error_msg":"SL leg did NOT land after 3 attempt(s) — position may be UNPROTECTED. tp_ok=True"}

AFTER: [{"oid":"10002462886","tpsl":"tp","triggerPx":"1.5",...}]   # confirmed: no SL resting
```

Valid SL restore (`sl_price=0.6593`, matching original):
```
200 {"success":true,"cancelled":[...],"placed":[{"tpsl":"tp","oid":"10002462889",...},
  {"tpsl":"sl","oid":"10002462890","status":"placed"}],
  "sl_ok":true,"tp_ok":true,"sl_oid":"10002462890","tp_oid":"10002462889","attempts":1,"error_msg":null}

AFTER: [{"oid":"10002462890","tpsl":"sl","triggerPx":"0.6593",...},{"oid":"10002462889","tpsl":"tp","triggerPx":"1.5",...}]
```
Position restored to its original protected state.

## Phase 2 — every caller safe on top of the honest contract

**`/strategies/{id}/adjust-stops`** (`order-listener/app/webhook_handler.py`): already
checked `result.get("success")`/`error_msg`, which now automatically inherits the
honest contract. Added: an `ERROR`-level log on failure (there was previously no log
line at all on this path — a failure was invisible in the logs) and `sl_ok`/`tp_ok` in
the 502 response body, so the caller can distinguish "SL ok, TP failed" from "SL
failed."

**Post-fill path** (`order-listener/app/reconciler.py`, `_reconcile_pending_orders`):
was fire-and-forget (logged `success` at INFO and moved on). Now: if SL was requested
and `sl_ok` is not `True`, logs at `ERROR` and writes
`sl_placement_unconfirmed: true` (+ timestamp + detail) into the *position's actual*
`opening_order_id`'s `signal_metadata` (looked up fresh from `strategy_positions`,
not assumed to be the filling order's own id — a top-up fill's `opening_order_id`
can point at an earlier order). This flag is consumed by the liquidation-safety
guard's self-heal path (see below), so a freshly-filled, unprotected position gets
picked up and fixed on the next reconcile pass instead of staying silently stopless.

**Guard `_is_guard_managed`** (`reconciler.py`): extended from checking only
`liq_safety_tightened` to also checking the new `sl_placement_unconfirmed` flag —
either condition now makes a missing resting SL trigger "our gap to close" rather
than an intentional no-SL choice the guard should leave alone. When the guard
successfully re-establishes protection, its audit patch now also writes
`sl_placement_unconfirmed: false`, clearing the flag.

**Guard `_place_and_verify`**: **simplified**, not kept as a duplicate retry loop.
Now that `modify-stops` verifies+retries centrally and returns an honest `sl_ok`,
the guard's own 3×/1.5s read-back-and-retry loop was purely redundant — it would
have re-verified the same thing the route already verified, doubling latency for no
extra safety. It's now a single `call_executor_modify_stops` call that trusts
`sl_ok`; kept as a named wrapper (not inlined) because it still owns the one thing
`modify-stops` can't know — `new_sl` was computed with a liquidation-safety margin by
the caller, so a confirmed landing at that price is safe by construction. Unused
`_TIGHTEN_ATTEMPTS`/`_TIGHTEN_RETRY_DELAY_S` constants and the now-dead `asyncio`
import were removed.

### Phase 2 verification (live, demo Blofin account, real fills — not mocked)

**`/adjust-stops`**, forced SL rejection (`tp_price=1.5`, `sl_price=0`):
```
STATUS 502
{"success": false, "error": "SL leg did NOT land after 3 attempt(s) — position may be
 UNPROTECTED. tp_ok=True", "sl_ok": false, "tp_ok": true}
```
Log line confirmed:
```
[ERROR] app.webhook_handler: adjust-stops FAILED strategy=sui-manual-59d9
  pos=3cb26cf7-c672-4daa-9fac-3f5e383a828e (SUI-USDT long) tp=1.5 sl=0.0: SL leg did
  NOT land after 3 attempt(s) — position may be UNPROTECTED. tp_ok=True
  (sl_ok=False, tp_ok=True)
```
Read-back confirmed the position was genuinely left with TP only, no SL. Restore call
(`sl_price=0.6593`) returned `200 {"success": true, "sl_ok": true, "tp_ok": true, ...}`
and read-back confirmed both legs resting again.

**Post-fill path** — real order lifecycle, not simulated: placed a real resting limit
buy (15 SUI, priced away from market) via the webhook on the same demo SUI-USDT
position, set that pending order's `tp_price=1.5, sl_price=0` (simulating a strategy
signal with an invalid SL), amended its price to marketable so it filled for real on
the exchange (position 142 → 157 SUI), then ran the actual `_reconcile_pending_orders`
reconciler function against it:
```
reconciler: post-fill modify-stops FAILED to confirm SL for order
  b60f0a80-84d7-4d1e-9c81-046c9cb36c11 (SUI-USDT long) — position may be UNPROTECTED
  after fill: SL leg did NOT land after 3 attempt(s) — position may be UNPROTECTED.
  tp_ok=True
reconciler: flagged opening_order=e3cbf8cd-530c-4fac-8981-e80dba47dc20 for
  liquidation-safety guard self-heal next pass
```
DB confirmed the flag landed on the correct row:
```
signal_metadata: {..., "sl_placement_unconfirmed": true,
  "sl_placement_unconfirmed_detail": "SL leg did NOT land after 3 attempt(s) —
  position may be UNPROTECTED. tp_ok=True", ...}
```
Trigger read-back confirmed the position was genuinely left with TP only, no SL:
```
[{"oid":"10002462947","tpsl":"tp","triggerPx":"1.5","sz":"157"}]
```
Ran the real `reconcile_once` (guard included) — self-heal fired automatically:
```
trigger-orders: [{"oid":"10002462950","tpsl":"sl","triggerPx":"0.640487",...},
                 {"oid":"10002462949","tpsl":"tp","triggerPx":"1.5",...}]
signal_metadata: {..., "liq_safety_tightened": true, "liq_safety_new_sl": 0.640487,
  "liq_safety_liquidation_price": 0.6339432262054215,
  "sl_placement_unconfirmed": false, ...}
```
SL re-established at `0.640487`, safely inside the live liquidation price
(`0.633943`), and the `sl_placement_unconfirmed` flag cleared — a freshly-filled
position that started genuinely unprotected was self-healed within one reconcile
pass, with no code path other than the one being tested.

Test cleanup: closed the 15 SUI test top-up (position back to 142) and re-applied the
original TP/SL (1.5 / 0.6593) via `modify-stops`, restoring the demo account to its
pre-test state (confirmed via a final trigger-orders read-back and `strategy_positions`
query, both matching the original values, all services healthy).

## Deploys

```
./scripts/redeploy.sh order-executor   # Phase 1: adapters + main.py
./scripts/redeploy.sh order-listener   # Phase 2: reconciler.py + webhook_handler.py
```
Both confirmed healthy after each redeploy.

## Not done / follow-ups

- Same-symbol Blofin quirk observed during testing (not a regression, pre-existing):
  Blofin groups TP+SL under one `tpslId`, so cancelling one leg via `cancel-tpsl`
  cancels both — visible in the forced-rejection test's `cancelled` array (the SL
  cancel entry shows `"already ... does not exist"` because the TP cancel in the same
  call already removed it). Handled correctly already (modify-stops's cancel loop
  tolerates a redundant cancel failure), just noting it's expected, not a new bug.
- A reduce-only partial close via `close_position()` does not resize an existing
  resting TP/SL trigger to the new position size (observed during test cleanup, fixed
  manually there via `modify-stops`). Pre-existing behavior, outside this fix's scope
  (only the post-*fill* path is documented as re-applying triggers at the confirmed
  size); worth a backlog entry if partial closes should get the same treatment.
