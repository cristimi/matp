# modify-stops: an omitted leg is now preserved, not deleted

**Date:** 2026-07-28
**Branch:** main
**Files changed:** `order-executor/app/main.py`, `order-listener/app/executor_client.py`,
`order-listener/app/webhook_handler.py`, `order-executor/tests/test_modify_stops_preserve.py`
**Follows:** `.gemini/reports/sol-missing-tp-and-rr-zone-borders.md` (the investigation)

---

## 1. The bug

`POST /accounts/{id}/positions/modify-stops` cancels **every** resting trigger order and
then places back only the legs the caller handed a price for. A request carrying just a
stop therefore deleted the take-profit permanently.

Live evidence from `sol-ai-6486`:

```
2026-07-28 14:17:13,715 [INFO] app.webhook_handler: adjust-stops strategy=sol-ai-6486
  pos=aeb2bfff-4b97-4544-ab5f-c640aa031597 (SOL-USDT short) tp=None sl=73.5 cancelled=1 placed=1
```

The AI's `dispatch_adjust_stops` omits `tp_price` whenever `resolved_tp_price` is None
(`ai-signal-generator/app/webhook/dispatcher.py:133`), so **every stop-only adjustment did
this**. SOL fired 5 gate-passed `adjust_stops` on 2026-07-28.

## 2. The change

`modify_stops` now resolves an *effective* price per leg, before anything is cancelled:

```python
        eff_tp = request.tp_price
        if eff_tp is None and not request.clear_tp:
            eff_tp = existing_tp
        eff_sl = request.sl_price
        if eff_sl is None and not request.clear_sl:
            eff_sl = existing_sl
```

`existing_tp` / `existing_sl` come from the step-2 `list_trigger_orders` read that the route
already performs and already refuses to proceed without. Because that read is *confirmed*
(an UNKNOWN result returns early, untouched), an absent leg here genuinely is absent rather
than unreadable — so preservation never invents a stop.

The placement loop, the read-back verification and the `sl_ok`/`tp_ok` reporting all now key
off `eff_tp`/`eff_sl` instead of the raw request. Three consequences worth stating:

- **Deleting a leg is still possible, but must be deliberate**: `clear_tp` / `clear_sl` were
  added to `ModifyStopsRequest` and plumbed through `call_executor_modify_stops`.
- **A preserved leg that fails to land is now reported as a failure.** Under the old
  behaviour that leg simply vanished and the call still returned `success: true`. This is
  the intended trade-off — a missing target should be loud.
- **A call with no legs requested and none resting now returns early without cancelling
  anything**, rather than stripping the position for no reason.

The listener's `adjust-stops` log line gained a `preserved=` suffix so the carry-forward is
visible in the logs.

### Scope check — no other caller changes behaviour

Every other `call_executor_modify_stops` call site passes **both** legs explicitly, so
preservation is a no-op for them:

| Call site | Passes |
|---|---|
| `webhook_handler.py:1421` — partial-close resize | `tp_price` + `sl_price` from the pre-close trigger read |
| `webhook_handler.py:1793` — post-fill stop re-anchor | `_tp_final` + `_sl_final` |
| `reconciler.py:456` — liquidation-safety guard | `current_tp` + `new_sl` |
| `reconciler.py:817` — post-fill (re)apply | `_tp_final` + `_sl_final` |
| `webhook_handler.py:559` — AI `adjust_stops` | **the one that could omit a leg** |

## 3. Verification

New regression file, `order-executor/tests/test_modify_stops_preserve.py`, covering the
exact SOL case plus the symmetric one, deliberate clearing, the unchanged both-legs path, the
no-op path, and the pre-existing "never cancel what you cannot see" rule.

Baseline before the change (old image):

```
$ docker compose exec -T order-executor sh -c "cd /app && python -m pytest tests/ -q"
..............................................                           [100%]
46 passed in 30.94s
```

After the change, deployed:

```
$ ./scripts/redeploy.sh order-executor
✓ order-executor redeployed.

$ docker compose exec -T order-executor sh -c "cd /app && python -m pytest tests/ -q"
....................................................                     [100%]
52 passed, 1 warning in 37.46s
```

46 → 52: the six new tests, no regressions.

Listener side, deployed and re-run:

```
$ ./scripts/redeploy.sh order-listener
✓ order-listener redeployed.

$ docker compose exec -T order-listener sh -c "cd /app && python -m pytest tests/ -q"
FAILED tests/test_fill_size_open_path.py::test_create_position_uses_actual_fill_size_when_provided
FAILED tests/test_fill_size_open_path.py::test_create_position_falls_back_to_payload_size_when_fill_size_none
FAILED tests/test_webhook_handler.py::test_valid_token_passes_auth - assert 4...
FAILED tests/test_webhook_handler.py::test_quote_variant_accepted_when_flag_on
FAILED tests/test_webhook_handler.py::test_daily_signal_cap_returns_429 - ass...
5 failed, 60 passed, 2 warnings in 30.24s
```

**Those 5 failures are pre-existing**, present on the unmodified image before any of today's
work (recorded identically at the start of the outcome-instrumentation session). No new
failures.

## 4. What this does NOT do

**The fix is preventative. It does not restore the take-profit SOL already lost.**

`sol-ai-6486`'s open short is still running with a stop and no target:

```
$ docker compose exec nginx wget -qO- http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/trigger-orders/SOL-USDT
[{"oid":"10002979398","tpsl":"sl","triggerPx":"73.500000000000000000","sz":"2.66"}]
```

The DB still records `tp_price = 72.3783` on its opening order, so DB and exchange disagree
and the UI draws a target that is not there. Restoring it means placing a trigger order on a
live position — a trading action, not telemetry — so it is left for an explicit decision
rather than done as a side effect of a bug fix.

Live end-to-end confirmation of preservation is likewise still outstanding: it needs either a
stop-only `adjust_stops` to fire against a position that *has* a TP (BNB currently does), or
a deliberate no-op `modify-stops` call against one. Both touch a live position's protection
during the non-atomic cancel-then-place window, so neither was done unprompted. The unit
tests cover the logic; the live proof is pending.
