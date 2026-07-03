# FIX: partial-close stop resize + modify-stops duplicate-SL race

Continues: docs/process/reports/modify-stops-contract-fix-report.md ("Not done /
follow-ups" section) — this closes items 1 and 3 from that list. Two independent
fixes, verified separately below. No DB migration.

## Phase 1 — resize resting TP/SL after a partial close

**Problem**: a reduce-only partial close shrinks the position but any resting TP/SL
trigger keeps the OLD (larger) size — an oversized trigger tries to close more than
exists once it fires.

**Fix** (`order-listener/app/webhook_handler.py`, `close_strategy_position` — the
single canonical routine every partial-close path already funnels through: the
webhook `close_long`/`close_short` handler, the manual `/positions/{id}/close` route,
and the reconciler's partial-reduction detection at `reconciler.py:224`):

- Before anything changes (before the exchange reduce-only call for the live paths;
  at detection time for the reconciler's `skip_exchange=True` sync path, since the
  reduction already happened externally), read the position's resting trigger prices
  via `get_trigger_orders`. A confirmed-empty read means nothing to resize. An
  UNKNOWN read falls back to the stops recorded on the position's opening order
  (`orders.tp_price`/`sl_price`).
- Once the DB row is confirmed resized (`updated is not None`, partial-reduce
  branch), a new helper `_resize_stops_after_partial_close` re-applies the captured
  TP/SL prices via `call_executor_modify_stops`. Because `modify-stops` resolves the
  *live* position size itself, re-placing the SAME prices resizes the resting
  trigger(s) to match the new, smaller position — no separate "resize" primitive
  needed on the executor side.
- Honest-contract respected: checks `sl_ok`; on failure, ERROR-logs and writes the
  same `sl_placement_unconfirmed` flag (via a new `_flag_sl_unconfirmed` helper) that
  the post-fill path already writes, on the position's `opening_order_id` — so the
  liquidation-safety guard's existing self-heal (`reconciler._is_guard_managed`)
  picks it up on the next pass. If neither the pre-read nor the DB fallback yields a
  price at all (double failure, unattemptable), this is also ERROR-logged and flagged
  rather than silently doing nothing.

Nothing changed in the adapters or in `modify-stops` itself — this is purely
orchestration in the listener, reusing the existing honest `modify-stops` contract.

### Phase 1 verification (live, demo Blofin account, real calls)

Setup note: the first test attempt (`POST /positions/{id}/close` with an empty body)
triggered a **full** close instead of the intended partial one — `close_size=None`
means full close by the existing contract, and I passed no `size`. This fully closed
the persistent demo SUI-USDT position. It was restored via a real `open_long` webhook
call (142 SUI requested; Blofin's lot-size rounding landed the fill at 133 SUI,
entry 0.7525 — a fresh fill can't reproduce the exact prior entry since price moved).
All testing below proceeds from that restored, real position.

**Happy path** — partial close resizes the resting TP/SL:
```
BEFORE: [{"oid":"10002465921","tpsl":"tp","triggerPx":"1.5","sz":"133"},
         {"oid":"10002465921","tpsl":"sl","triggerPx":"0.6831","sz":"133"}]
position size: 133

POST /positions/{id}/close {"size": 20}
200 {"success":true,"status":"filled","is_full_close":false,...}

log: "close_strategy_position: resized resting stops for SUI-USDT long to the new
      position size (sl_ok=True, tp_ok=True)"

AFTER:  [{"oid":"...","tpsl":"sl","triggerPx":"0.6831","sz":"113"},
         {"oid":"...","tpsl":"tp","triggerPx":"1.5","sz":"113"}]
position size (DB and exchange): 113
```
Same prices, new size — matches the reduced position, not the original.

**Forced resize failure → flag → self-heal** — called `_resize_stops_after_partial_close`
directly (same code path `close_strategy_position` calls) with `pre_sl=0` (invalid,
same rejection technique as the prior report) and `pre_tp=1.5` (valid):
```
[ERROR] close_strategy_position: post-partial-close stop resize FAILED to confirm SL
  for SUI-USDT long — position may be UNPROTECTED at the new size: SL leg did NOT
  land after 3 attempt(s) — position may be UNPROTECTED. tp_ok=True
[ERROR] close_strategy_position: flagged opening_order=bb72f49f-... for
  liquidation-safety guard self-heal next pass
```
DB confirmed the flag landed on the position's actual opening order:
```
signal_metadata: {..., "sl_placement_unconfirmed": true,
  "sl_placement_unconfirmed_detail": "SL leg did NOT land after 3 attempt(s) —
  position may be UNPROTECTED. tp_ok=True", ...}
```
Trigger read-back confirmed the position was genuinely left with TP only:
```
[{"oid":"...","tpsl":"tp","triggerPx":"1.5","sz":"113"}]
```
Ran the real `reconcile_once` — self-heal fired automatically:
```
reconciler: position ...(SUI-USDT long) is guard-managed but has NO resting SL
  trigger — exchange-side stop is MISSING, re-establishing protection
reconciler: liquidation-safety TIGHTENED SL for ...: None -> 0.689134
  (liq=0.682092859301037365, margin=0.00704071406989626350, tp_preserved=1.5)
```
Read-back after self-heal: SL and TP both resting again at `sz=113`, and the
`sl_placement_unconfirmed` flag cleared to `false`. A genuinely-unprotected position
(from a failed resize) was self-healed within one reconcile pass, via the same
mechanism the earlier fix proved for the post-fill path.

## Phase 2 — close the duplicate-SL race in the modify-stops verify loop

**Problem**: in the retry loop, if `place_trigger_orders` placed a leg successfully
but the immediately-following `list_trigger_orders` read-back came back `None`
(transient unknown), the old code kept the leg "remaining" and the *next* attempt
re-called `place_trigger_orders` for that same leg — stacking a duplicate trigger at
the same price.

**Fix** (`order-executor/app/main.py`, `modify_stops`): replaced the two-value
`remaining_tp`/`remaining_sl` tracking with an explicit per-leg state machine —
`pending` → (place) → `awaiting_confirm` → either `confirmed` (a **successful**
read-back finds it) or back to `pending` (a **successful** read-back shows it
genuinely absent). An `UNKNOWN` (`None`) read-back never causes that demotion. On an
unknown read, the read itself is retried in place (`_MODIFY_STOPS_READ_RETRIES = 2`
extra reads, `_MODIFY_STOPS_READ_RETRY_DELAY_S = 1.0s`) before the outer attempt loop
moves on — so a transient read blip is absorbed without ever re-placing a leg the
adapter already reported as landed. Only a leg the adapter explicitly rejected
(`error` in its per-leg result), or a leg a **confirmed** read-back shows missing, is
eligible to be re-placed on the next attempt. `sl_ok`/`tp_ok` now mean "confirmed",
same as before — a leg stuck in `awaiting_confirm` reports as not-ok (honest,
unconfirmed) rather than being silently duplicated.

### Phase 2 verification (live, demo Blofin account, real calls)

**Happy path** (no fault):
```
POST /modify-stops {tp_price:1.5, sl_price:0.6891}
200 {"success":true,"sl_ok":true,"tp_ok":true,"attempts":1,...}
```

**Forced-SL-rejection regression** (`sl_price=0`, same as the prior report's test):
```
200 {"success":false,"sl_ok":false,"tp_ok":true,"attempts":3,
  "error_msg":"SL leg did NOT land after 3 attempt(s) — position may be UNPROTECTED.
  tp_ok=True"}
AFTER: [{"tpsl":"tp","triggerPx":"1.5","sz":"113"}]   # TP only, no duplicate, no SL
```
Restored (`sl_price=0.6891`): `sl_ok=true, tp_ok=true` confirmed by read-back.

**Fault-injection #1** — `list_trigger_orders` monkeypatched (real adapter, real
exchange calls for everything else) to return `None` on exactly the FIRST read-back
call after the first successful place (the exact "place succeeds, next read-back
None" condition from the bug report):
```
[place] tp landed tpslId=..289, sl landed tpslId=..290 (real Blofin calls)
[WARNING] verify read-back UNKNOWN on attempt 1/3 (read retry 1/2)
[real read, retry 1] -> confirms both legs at their placed prices
result: {"success":true,"sl_ok":true,"tp_ok":true,"attempts":1,...}
Final independent read-back: exactly 1 SL leg, 1 TP leg — no duplicate.
```
The read-retry absorbed the transient unknown; no second place call happened.

**Fault-injection #2 (harder)** — `list_trigger_orders` forced to return `None` on
*every* call after the initial pre-cancel read (persistent read outage, so the read
retries can never recover):
```
[place] tp landed tpslId=..289, sl landed tpslId=..290 (real Blofin calls)
[WARNING] verify read-back UNKNOWN on attempt 1/3 (read retry 1/2)
[WARNING] verify read-back UNKNOWN on attempt 1/3 (read retry 2/2)
[WARNING] verify read-back still UNKNOWN after 2 extra read(s) on attempt 1/3 —
  leaving any awaiting-confirm leg as-is (not re-placing on an unknown read)
[ERROR] SL leg did NOT land after 1 attempt(s) — position may be UNPROTECTED.
  tp_ok=False
result: {"success":false,"sl_ok":false,"tp_ok":false,"attempts":1,
  "placed":[{"tpsl":"tp","oid":"..289","status":"placed"},
            {"tpsl":"sl","oid":"..290","status":"placed"}]}
Final independent (unpatched) read-back: exactly 1 SL leg, 1 TP leg — both actually
resting on the exchange at the requested prices, despite the route (correctly)
reporting them as unconfirmed rather than duplicating the place call.
```
This is the strongest proof: even under a total, persistent read outage the loop
stops re-placing after the legs move to `awaiting_confirm` (nothing left `pending`,
so the while-loop exits) — it reports an honest `sl_ok=False`/`tp_ok=False` instead of
stacking a duplicate, exactly matching "unconfirmed, not silently duplicated."

Final cleanup call (no fault) confirmed `sl_ok=true, tp_ok=true` and restored the
demo position to a fully protected, honestly-confirmed state (SUI-USDT long, 113,
SL 0.6891, TP 1.5) — matching `strategy_positions` (size 113, status open) and with
`sl_placement_unconfirmed=false` on its opening order.

## Deploys

```
./scripts/redeploy.sh order-listener   # Phase 1: webhook_handler.py
./scripts/redeploy.sh order-executor   # Phase 2: main.py
```
Both confirmed healthy after each redeploy.

## Not done / follow-ups

- Phase 1's DB-fallback source (`orders.tp_price`/`sl_price` on the opening order) can
  go stale if `/adjust-stops` changed the live stop without writing back to the DB
  (same caveat noted in the modify-stops-contract-fix report for the liq-safety
  guard's "current SL" source). It's used only as a last resort when the live
  pre-read itself fails, so a stale fallback is still better than resizing nothing,
  but it's not guaranteed accurate.
- If both the pre-read and the DB fallback are unavailable (no opening_order_id, or
  neither price recorded), Phase 1 can only log + flag — it cannot literally resize
  a trigger it has no price for. The existing (unchanged) resting trigger stays at
  the old, oversized amount in that narrow double-failure case; the flag makes the
  guard aware but the guard's own missing-SL check only fires once that trigger is
  genuinely absent, not merely oversized. Considered out of scope here (same
  narrow-edge-case tradeoff the constraints asked to accept via the flag mechanism).
