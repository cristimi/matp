# Five fixes for the SOL stop-out, implemented and verified in order

**Date:** 2026-07-30
**Follows:** `.gemini/reports/2026-07-30_sol_closed_by_trailed_stop.md` (the diagnosis)
**Services changed:** `ai-signal-generator`, `order-listener`, plus DB migration 072

Each item was implemented, deployed, and checked against the **running container** with
real trade data or real exchange orders before moving to the next.

Everything below is pasted command output.

---

## Item 1 — a stop may not lock a loss on a position that is in profit

`ai-signal-generator/app/graph/nodes/node_guard.py`, `adjust_stops` branch.

The existing check only asked whether the stop sat on the correct side of the **live
price**. 73.88 is a structurally valid stop for a short trading at 73.69, so it passed —
it just happened to be above the 73.79 entry, i.e. a guaranteed loss.

New rejection `stop_locks_loss_in_profit`: while the position is in profit, the stop must
be at breakeven or better. Deliberately **not** applied while underwater — a tighten taken
on a losing trade legitimately sits in the loss zone, and so does every opening stop.

> Note on the earlier suggestion: I first described this as "never worse than entry".
> That would have wrongly blocked legitimate loss-side stops on underwater positions and
> the opening stop itself. The rule as built is scoped to in-profit positions.

### Check — the real 08:30 decision replayed through the deployed guard

```
[PASS] REAL 08:30 SOL short: entry 73.79, price 73.69 (in profit), new sl 73.88
         gate_passed=False expected=False reason=stop_locks_loss_in_profit
[PASS] REAL 01:15 SOL short: entry 73.22, price 73.55 (underwater), new sl 73.84
         gate_passed=True expected=True reason=None
[PASS] Control short in profit, stop AT breakeven (73.79)
         gate_passed=True expected=True reason=None
[PASS] Control short in profit, stop BETTER than entry (73.75)
         gate_passed=True expected=True reason=None
[PASS] Control long in profit, stop below entry (locks loss) 99 vs entry 100
         gate_passed=False expected=False reason=stop_locks_loss_in_profit
[PASS] Control long in profit, stop above entry (locks gain) 105 vs entry 100
         gate_passed=True expected=True reason=None
[PASS] Control long underwater, stop below entry 95 vs entry 100, price 96
         gate_passed=True expected=True reason=None
[PASS] Control: wrong-side stop still caught (short, sl 73.60 below price 73.69)
         gate_passed=False expected=False reason=stop_wrong_side

ALL AS EXPECTED
```

Log line emitted by the running service:

```
node_guard reject adjust_stops: stop 73.88 locks a loss on an in-profit short
(entry 73.79, price 73.69) — breakeven or better is required
```

Existing suite: `123 passed`.

---

## Item 2 — a minimum distance between the stop and the live price

Migration `db/migrations/072_ai_min_stop_distance.sql` adds
`ai_strategy_config.min_stop_distance_pct`, default **0.30**, range-checked 0-20, `0`
disables. Applied and self-verified:

```
ALTER TABLE
DO
COMMENT
NOTICE:  Migration 072 verified OK: min_stop_distance_pct present on 7 strategy rows, default 0.30
```

The 0.30 default is not arbitrary — it is the same number `min_close_move_pct` already
uses for discretionary exits, and both encode the same "inside its own noise band" idea.
Calibration data (30 days of real opening stops) is recorded in the migration; the
tightest genuine strategy is bnb-ai-scalper at 0.281%, which can lower its own row.

### Check — deployed guard, reading the real sol-ai-6486 config row from the DB

```
live config min_stop_distance_pct = 0.30 (min_close_move_pct = 0.30)

[PASS] REAL 08:30 SOL stop 73.88 vs price 73.69 (0.258% away), in profit
         gate_passed=False reason=stop_locks_loss_in_profit
[PASS] Same 0.258% stop but UNDERWATER (entry 73.60) — item 1 cannot apply
         gate_passed=False reason=stop_too_close
[PASS] Underwater short, stop 73.60 vs price 73.55 (0.068% away)
         gate_passed=False reason=stop_too_close
[PASS] REAL 01:15 SOL stop 73.84 vs price 73.55 (0.394% away), underwater
         gate_passed=True reason=None
[PASS] Underwater short, stop just above the 0.30% floor (0.3001%)
         gate_passed=True reason=None
[PASS] Underwater short, stop a hair UNDER the 0.30% floor (0.2999%)
         gate_passed=False reason=stop_too_close
[PASS] Long underwater, stop 0.10% below price — too close
         gate_passed=False reason=stop_too_close
[PASS] Long underwater, stop 1.00% below price — fine
         gate_passed=True reason=None

ALL AS EXPECTED
```

The second row is the important one: it isolates item 2 by putting the same 0.258% stop on
an underwater position, where item 1 cannot fire. Migration 072 reaching the service also
proves the new column flows through the `SELECT *` config load.

Existing suite: `123 passed`.

---

## Item 3 — the saved stop now matches the exchange

`order-listener/app/webhook_handler.py`, `adjust_stops_for_strategy`.

The route updates the exchange and now also writes the **effective** level of each leg
onto the position's opening order, plus an `order_price_history` row with
`source='adjust_stops'`. "Effective" includes the *preserved* leg — one the caller left
unpriced, which the executor carries forward and reports in `preserved`. That is exactly
the shape the SOL call used. A persistence failure is logged and never turned into a
reported failure, because by then the exchange is already correct.

### Check A — real adjust-stops round trip, BloFin demo account, open BTC long

Safe by construction: 60680.4 → 60700 → 60680.4, both ~6% below the 64585 mark price on a
long, so unreachable, and it ends where it started.

```
--- BEFORE ---
  exchange live SL : 60680.400000000000000000
  orders.sl_price  : 60680.4
  price_history    : none

Moving stop 60680.4 -> 60700.0 (real exchange call)
  -> POST adjust-stops sl=60700.0: HTTP 200 success=True preserved=[]
--- AFTER MOVE ---
  exchange live SL : 60700.000000000000000000
  orders.sl_price  : 60700.0
  price_history    : [(0, '60700.0', 'adjust_stops')]

Restoring stop 60700.0 -> 60680.4
  -> POST adjust-stops sl=60680.4: HTTP 200 success=True preserved=[]
--- AFTER RESTORE ---
  exchange live SL : 60680.400000000000000000
  orders.sl_price  : 60680.4
  price_history    : [(0, '60700.0', 'adjust_stops'), (1, '60680.4', 'adjust_stops')]

=== VERDICT ===
  [OK] exchange actually moved to 60700
  [OK] DB followed the exchange to 60700
  [OK] DB matches exchange after move
  [OK] exchange restored to 60680.4
  [OK] DB matches exchange after restore
  [OK] DB changed from its stale value
ALL OK
```

### Check B — the preserved-leg branch, i.e. the SOL-shaped call

TP set to 90000 (+39%, unreachable), then adjust-stops called with the **SL only**:

```
2. adjust-stops with SL ONLY (60690.0) — TP must be carried forward
   HTTP 200 success=True preserved=[{'tpsl': 'tp', 'triggerPx': 90000.0}]
--- AFTER SL-ONLY CALL ---
  exchange : sl=60690.0 tp=90000.0
  database : sl=60690.0 tp=90000.0

=== VERDICT ===
  [OK] preserved TP reached the database (the SOL-shaped case)
  [OK] priced SL leg persisted and matches the exchange
  [OK] position left with SL 60680.4 and no TP
```

Position left exactly as found:

```
                  id                  | sl_price | tp_price
--------------------------------------+----------+----------
 0c52fd6a-e274-41df-a9a3-3a4531ae393f |  60680.4 |
[{"oid":"10003030806","tpsl":"sl","triggerPx":"60680.400000000000000000","sz":"3.1"}]
```

Two real `order_price_history` rows from check A remain on that order. They are a truthful
record — the stop genuinely moved and moved back — so they were left in place.

---

## Item 4 — the close reason now names the trigger it hit

`order-listener/app/reconciler.py`. New `classify_external_close(fill, sl, tp)` returns
`"Stop-loss hit"` / `"Take-profit hit"` when the fill lands within `_TRIGGER_MATCH_TOL_PCT`
(0.15%) of a resting level, nearer leg winning, and `None` whenever it is not clear-cut.
It only ever **refines** the generic label, so an exchange-supplied `"Liquidated"` still
wins and the synthetic order's `signal` column is unaffected.

### Check — deployed classifier run over every real "Closed on exchange" position

```
tolerance = 0.15% of fill

closed_at            symbol     side           fill           sl           tp  -> new label
2026-07-30 09:09:33  SOL-USDT   short       73.8776      74.5011      72.1764  -> (unchanged: Closed on exchange)
2026-07-30 01:34:27  SOL-USDT   short       73.8492      73.9193      71.9413  -> Stop-loss hit
2026-07-29 20:12:22  ETH-USDT   long      1879.1000    1879.7000    1923.6961  -> Stop-loss hit
2026-07-28 15:03:47  SOL-USDT   short       73.5025      74.2559      72.3783  -> (unchanged: Closed on exchange)
2026-07-27 22:48:20  ETH-USDT   long      1907.0211    1910.7000    2005.5000  -> (unchanged: Closed on exchange)
2026-07-27 22:43:06  TAO-USDT   long       190.7600     190.7850     194.6217  -> Stop-loss hit
2026-07-27 14:33:10  BNB-USDT   long       568.5200     569.1210     582.9182  -> Stop-loss hit
2026-07-26 22:02:33  ETH-USDT   short     1936.0165    1934.4000    1890.9000  -> Stop-loss hit
2026-07-26 14:08:19  SOL-USDT   short       75.1589      75.2516      74.0168  -> Stop-loss hit
2026-07-24 13:56:43  BNB-USDT   long       557.8400     558.1060     569.3236  -> Stop-loss hit
2026-07-24 12:59:14  BNB-USDT   long       563.2900     563.8660     575.2005  -> Stop-loss hit
2026-07-23 12:47:42  BNB-USDT   long       569.3550     568.4140     572.6982  -> (unchanged: Closed on exchange)
2026-07-23 06:26:43  SOL-USDT   long        77.3191      76.7860      79.3481  -> (unchanged: Closed on exchange)
2026-07-22 14:07:54  BNB-USDT   long       573.6900     569.9930     573.6731  -> Take-profit hit
2026-07-22 03:17:44  BNB-USDT   long       571.8200     571.5080     576.6775  -> Stop-loss hit
2026-07-21 11:36:18  BNB-USDT   short      577.5200     580.5780     574.8016  -> (unchanged: Closed on exchange)
2026-07-19 18:06:12  ETH-USDT   long      1863.8686    1855.8500    1894.4800  -> (unchanged: Closed on exchange)
2026-07-17 18:17:49  ETH-USDT   long      1845.0000    1831.7300    1935.6261  -> (unchanged: Closed on exchange)
2026-07-16 17:57:25  BNB-USDT   short      576.3753     581.4230     574.9431  -> (unchanged: Closed on exchange)
2026-07-16 17:08:41  HYPE-USDT  long        63.3720      63.3798      66.1967  -> Stop-loss hit
2026-07-16 08:17:57  BNB-USDT   short      575.2000     582.8900     575.4661  -> Take-profit hit
2026-07-15 11:08:51  HYPE-USDT  short       68.4050      68.3919      64.3456  -> Stop-loss hit
2026-07-13 20:22:58  HYPE-USDT  long        63.5774      63.0745      66.3281  -> (unchanged: Closed on exchange)
2026-07-13 03:14:29  ETH-USDT   long      1788.9626    1790.2400    1830.4337  -> Stop-loss hit
2026-07-11 23:24:34  TAO-USDT   short      210.2200     216.6650     210.2296  -> Take-profit hit

15 of 25 would now carry a specific label.

--- refusal cases ---
  no levels at all                         -> None
  fill far from both levels                -> None
  fill is None                             -> None
  fill is zero                             -> None
  just outside tolerance (0.16%)           -> None
  just inside tolerance (0.14%)            -> Stop-loss hit
  nearer leg wins (sl 0.01 vs tp 0.10)     -> Stop-loss hit
```

**The first row is the honest limitation.** Today's SOL position stays unnamed, because its
stored `sl_price` is still the stale 74.5011 — item 3 was not in place when it closed. The
classifier can only be as good as the recorded levels, so it refuses rather than guessing.
Positions closing from now on carry live levels and will be labelled.

Deployed function confirmed live:

```
deployed classifier live: Stop-loss hit
```

### Test-suite honesty

`order-listener` suite: **5 failed, 60 passed**. The same 5 fail on the unmodified code —
verified by copying `HEAD` versions of `webhook_handler.py` and `reconciler.py` into the
container and re-running:

```
FAILED tests/test_fill_size_open_path.py::test_create_position_uses_actual_fill_size_when_provided
FAILED tests/test_fill_size_open_path.py::test_create_position_falls_back_to_payload_size_when_fill_size_none
FAILED tests/test_webhook_handler.py::test_valid_token_passes_auth
FAILED tests/test_webhook_handler.py::test_quote_variant_accepted_when_flag_on
FAILED tests/test_webhook_handler.py::test_daily_signal_cap_returns_429
5 failed, 60 passed, 2 warnings in 28.28s
```

They are environment failures (no Redis, no mark price available in the test harness), not
regressions. **They were already broken and I did not fix them** — out of scope here.

---

## Item 5 — partial closes now close what the model meant

Root cause: `size_pct` had **no** schema description and the prompt never mentioned it
(`grep size_pct app/prompt/` returns nothing), so the model supplied a fraction.

* `LLMSignalOutput.size_pct` now states the unit: *"a PERCENT from 1 to 100 — 50 means half
  the position, NOT 0.5"*.
* `node_guard` reads a value strictly inside `(0, 1)` as the fraction it plainly is and
  logs a warning. **`1` is deliberately left alone** — it is genuinely ambiguous, and
  turning a possible "1%" into a 100% close would be far worse than the dust it makes now.
* `_MIN_PARTIAL_CLOSE_PCT = 2.0` then rejects the remainder as `partial_close_too_small`.

### Check — all 14 real partial closes replayed through the deployed guard

```
schema description now shipped to the model:
  size_pct -> For partial_close ONLY: how much of the open position to close, as a PERCENT from 1 to 100 — 50 means half the position, NOT 0.5. Use 0 for every other action.

floor = 2.0% of position

when         strategy                 pos before    sent   old size new outcome
2026-07-30   sol-ai-6486                2.696450     0.5   0.013482 closes 1.348225 (50.0%)
2026-07-30   sol-ai-6486                2.696518   0.502   0.013550 closes 1.353652 (50.2%)
2026-07-30   sol-ai-6486                2.730000     0.5   0.013650 closes 1.365 (50.0%)
2026-07-28   bnb-ai-scalper-edbb        0.010112   1.108   0.000112 REJECTED (partial_close_too_small)
2026-07-28   sol-ai-6486                2.652677     0.5   0.013263 closes 1.326338 (50.0%)
2026-07-28   sol-ai-6486                2.652744   0.502   0.013330 closes 1.331677 (50.2%)
2026-07-28   sol-ai-6486                2.652811   0.505   0.013397 closes 1.33967 (50.5%)
2026-07-28   sol-ai-6486                2.652878   0.508   0.013464 closes 1.347662 (50.8%)
2026-07-28   bnb-ai-scalper-edbb        0.011000   9.091   0.001000 closes 0.001 (9.1%)
2026-07-28   sol-ai-6486                2.652946    0.51   0.013532 closes 1.353002 (51.0%)
2026-07-28   sol-ai-6486                2.653014   0.513   0.013600 closes 1.360996 (51.3%)
2026-07-28   bnb-ai-scalper-edbb        0.012050  17.012   0.002050 closes 0.00205 (17.0%)
2026-07-27   sol-ai-6486                2.500000     0.5   0.012500 closes 1.25 (50.0%)
2026-07-26   sol-ai-6486               12.150000     0.5   0.060750 closes 6.075 (50.0%)

--- unit cases ---
  size_pct=0.5 (the real SOL input, meant 50%)          -> size=1.348225 (50.0% of position)
  size_pct=0.25 fraction                                -> size=0.674113 (25.0% of position)
  size_pct=1 (ambiguous: left alone, caught by floor)   -> REJECTED (partial_close_too_small)
  size_pct=2 (exactly at the floor)                     -> size=0.053929 (2.0% of position)
  size_pct=25 (a normal percent)                        -> size=0.674113 (25.0% of position)
  size_pct=50 (correctly-expressed half)                -> size=1.348225 (50.0% of position)
  size_pct=100 (whole position)                         -> size=2.69645 (100.0% of position)
  size_pct=150 (over-large, clamped by size)            -> size=2.69645 (100.0% of position)
```

bnb's genuine 9.1% and 17.0% trims pass through untouched; only its 1.1% dust is rejected.

Existing suite: `123 passed`.

---

## Final state

```
order-listener:8001          {"status":"ok","service":"order-listener"}
order-executor:8004          {"status":"ok","service":"order-executor","version":"1.0.0"}
dashboard-api:8003           {"status":"ok","service":"dashboard-api"}
ai-signal-generator:8005     {"status":"ok","service":"ai-signal-generator","collector":{"running":true,...
```

The one open position (social-btc-astro BTC-USDT long) is untouched: stop 60680.4 on the
exchange, 60680.4 in the database, no TP — exactly as it started.

## What is NOT covered

* **A monotonic ratchet** (a stop may only ever move in the favourable direction) is still
  not enforced. The SOL trail was already monotonic so it would not have helped here, but
  it is the natural third rule and it is now cheap to add, because item 3 finally records
  what the previous stop was.
* **The take-profit leg** has no distance floor — only the stop-loss does. A TP parked next
  to the price scratches a profit rather than causing a loss, so it was left alone.
* **The 5 pre-existing `order-listener` test failures** remain, unrelated and untouched.
* `min_stop_distance_pct` is a **database-level** knob with no settings-UI field, matching
  its sibling `min_close_move_pct`. Change it with SQL.
