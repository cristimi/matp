# SOL missing take-profit + risk/reward zone borders hiding wicks

**Date:** 2026-07-28
**Branch:** main
**Investigation:** why `sol-ai-6486`'s open short never took profit (no code changed — see §3)
**Fix applied:** `dashboard-ui/src/charts/adapters/lightweightCharts/riskRewardPrimitive.ts`

---

## 1. SOL: the take-profit is not on the exchange, and was destroyed by `adjust_stops`

### What the position looks like

```
                  id                  |  symbol  | side  |         size          | entry_price |           opened_at           | status | mfe_price | mae_price | mfe_r |              open_order              | tp_price | sl_price | oo_status
--------------------------------------+----------+-------+-----------------------+-------------+-------------------------------+--------+-----------+-----------+-------+--------------------------------------+----------+----------+-----------
 aeb2bfff-4b97-4544-ab5f-c640aa031597 | SOL-USDT | short |  2.639414000000000000 |       73.61 | 2026-07-28 01:01:20.527126+00 | open   |     72.55 |     72.97 | 1.641 | 0343bb84-6518-4cfc-ac4d-e0cfef195bfa |  72.3783 |  74.2559 | filled
```

The DB says this position has a take-profit at **72.3783**. The exchange says otherwise:

```
$ docker compose exec nginx wget -qO- http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/trigger-orders/SOL-USDT
[{"oid":"10002979398","tpsl":"sl","triggerPx":"73.500000000000000000","sz":"2.66"}]
```

**One trigger order. It is a stop. There is no take-profit resting on the exchange.** The
position could not have taken profit because nothing was there to fire. The DB and the
exchange disagree, which is why the UI still draws a TP line that does not exist.

For contrast, BNB — which has had no `adjust_stops` fire against it — still has both legs:

```
BNB-USDT: [{"oid":"10002979245","tpsl":"sl","triggerPx":"578.34","sz":"3"},
           {"oid":"10002979244","tpsl":"tp","triggerPx":"559.97","sz":"3"}]
```

### The mechanism

`modify-stops` is a **cancel-everything-then-place-what-you-were-handed** operation.
`order-executor/app/main.py:540`:

```python
        # 3. Cancel them
        cancelled = []
        for trig in existing:
            oid = trig["oid"]
            cancel_result = await adapter.cancel_order(request.symbol, oid)
```

It cancels *every* trigger order it finds — TP and SL alike. Then step 4 only re-places a
leg if a price was supplied for it (`main.py:560`):

```python
        sl_state = _LEG_PENDING if request.sl_price is not None else None
        tp_state = _LEG_PENDING if request.tp_price is not None else None
```

An `adjust_stops` carrying only a stop therefore **deletes the take-profit permanently**.
Nothing anywhere restores it. The caller chain that makes this happen:

1. The AI proposes `adjust_stops` with a new stop and no target, so
   `state['resolved_tp_price']` is None.
2. `ai-signal-generator/app/webhook/dispatcher.py:133` omits the key entirely:
   `if tp_price is not None: body['tp_price'] = tp_price`.
3. `order-listener` passes `tp_price=None` through to the executor
   (`webhook_handler.py:527`).
4. The executor cancels both legs and re-places one.

Caught in the act in the live log:

```
2026-07-28 14:17:13,715 [INFO] app.webhook_handler: adjust-stops strategy=sol-ai-6486
  pos=aeb2bfff-4b97-4544-ab5f-c640aa031597 (SOL-USDT short) tp=None sl=73.5 cancelled=1 placed=1
```

`tp=None`, one trigger cancelled, one placed. `cancelled=1` rather than 2 means the TP was
already gone by 14:17 — killed by one of the earlier `adjust_stops` calls that fired today
(10:00, 11:16, 13:30, 14:15). Container logs older than the 14:03 recreate are gone, so the
exact first offender is not recoverable, but the mechanism is identical for all of them.

`sol-ai-6486` ran **51 non-hold proposals** today, of which 8 were `adjust_stops` and 5 of
those passed the gate:

```
 2026-07-28 10:00:30 | adjust_stops | 0.680 | gate_passed=t | webhook_fired=t
 2026-07-28 11:16:04 | adjust_stops | 0.700 | gate_passed=t | webhook_fired=t
 2026-07-28 13:30:31 | adjust_stops | 0.680 | gate_passed=t | webhook_fired=t
 2026-07-28 14:15:19 | adjust_stops | 0.710 | gate_passed=t | webhook_fired=t
```

### Why this matters beyond one trade

The partial-close path already guards against exactly this. `close_strategy_position`
(`webhook_handler.py:1223`) reads the resting triggers *before* it reduces, precisely so both
legs can be restored at the new size:

```python
    # A partial reduce leaves any resting TP/SL sized to the OLD position — capture
    # its price(s) now, before anything changes, so they can be re-applied at the new
    # size once the reduce lands
```

`modify-stops` has no equivalent read-and-preserve. So the same codebase is careful about
this in one path and destructive in the other.

This is a plausible contributor to a pattern the forensics report measured: **21 of 81
AI-engine trades closed in the 0..0.4R band for +4.96 USDT gross combined** — winners cut
short. A position whose target has been silently deleted can only exit via a stop or a
discretionary close.

### Not fixed here — deliberately

Restoring the TP, or making `modify-stops` preserve an unspecified leg, **changes trading
behaviour**. The standing instruction on the current work is telemetry only, and to stop and
flag anything that would alter a trading decision. Flagging it. The fix is small and I can
apply it on your word; my recommendation is that `modify-stops` should treat "no price given
for this leg" as *leave it alone* rather than *delete it*, with an explicit
`cancel_tp: true` needed to remove a target on purpose.

---

## 1b. UPDATE 2026-07-28 15:03 — the position closed, and the missing TP did NOT cost it

The position closed while this was being written. It is **not** live any more:

```
  symbol  | side  | status |           closed_at           | close_reason       |  closing_price  |  pnl
----------+-------+--------+-------------------------------+--------------------+-----------------+--------
 SOL-USDT | short | closed | 2026-07-28 15:03:47.643656+00 | Closed on exchange | 73.5025         | 0.0523
```

It exited at 73.5025 — the trailed stop at 73.50 — for **+0.05 USDT**.

**Correction to §1.** I wrote that the position "could not have taken profit because nothing
was there to fire". Mechanically true, but it implied the deleted TP cost this trade money.
It did not, as far as the data can show — and where the data is silent I should not have
implied anything. The new excursion instrumentation (migration 069) recorded:

```
 symbol   | opened_at           | excursion_first_at  | samples | entry | sl_at_entry | tp_at_entry | mfe_price | mfe_r | closing_price
 SOL-USDT | 2026-07-28 01:01:20 | 2026-07-28 14:03:19 |      57 | 73.61 |     74.2559 |     72.3783 |     72.55 | 1.641 | 73.5025
```

With risk = |73.61 − 74.2559| = 0.6459:

- the **target** at 72.3783 sat at **1.907 R**
- the best price **observed** was 72.55 = **1.641 R**
- it exited at **0.166 R**

So in the hour that was measured, price never reached the target — the TP would not have
filled even had it been resting. **But sampling only began at 14:03 and the position opened
at 01:01, so 13 of its 14 hours are unobserved.** Whether price touched 72.3783 in that
window is unknown and unknowable: no price history is retained. The honest verdict is
**the deleted TP cannot be shown to have cost this trade anything, and cannot be cleared
either**.

What the data *does* show is the more interesting failure: the trade ran to at least
**+1.64 R** and closed at **+0.17 R**, giving back nearly all of it to a trailed stop. That
is precisely the "winner given back" shape the forensics report could only guess at — and it
is the first trade in the system's history where it is measurable rather than invisible.

The bug in §1 is real and worth fixing on its own merits (a stop-only adjustment silently
deleting a target is indefensible), and it is fixed in
`.gemini/reports/modify-stops-preserve-unpriced-leg.md`. But this particular trade is not
evidence of its cost.

---

## 2. Risk/reward zone borders were hiding candle wicks — fixed

### The clash

`riskRewardPrimitive.ts` drew each zone as a fill plus a 1px outline. The outline colours
were byte-identical to the candle colours in `lightweightCharts/index.ts:36`:

| | zone border (before) | candle |
|---|---|---|
| green | `rgba(34, 197, 94, 0.70)` | `COLORS.up = '#22c55e'` = rgb(34,197,94) |
| red | `rgba(239, 68, 68, 0.70)` | `COLORS.down = '#ef4444'` = rgb(239,68,68) |

The primitive renders at `zOrder: 'top'`, so those borders were painted *over* the bars. Any
wick crossing the entry, target or stop level was drawn in the same colour as the line
covering it, and disappeared.

### The change

`zone()` is now fill-only — the stroke, and the now-unused `profitStroke` / `lossStroke`
entries in `RiskRewardColors` and `DEFAULT_COLORS`, are gone. Fills are unchanged at 0.16
alpha, which is enough to read the zone without an outline. The entry line, the dashed
risers between staircase rungs and the progress band are untouched.

### Verification

Typecheck clean, existing primitive tests pass unchanged (they assert on filled rects, never
on stroked ones):

```
 ✓ src/charts/adapters/lightweightCharts/__tests__/riskRewardPrimitive.test.ts (7 tests) 142ms
 Test Files  1 passed (1)
      Tests  7 passed (7)
```

Live bundle — the two border colours are absent, the fills survive:

```
$ docker compose exec dashboard-ui grep -rl "rgba(34, 197, 94, 0.70)" /usr/share/nginx/html
profitStroke absent (good)
$ docker compose exec dashboard-ui grep -rl "rgba(239, 68, 68, 0.70)" /usr/share/nginx/html
lossStroke absent (good)
$ docker compose exec dashboard-ui grep -rl "rgba(34, 197, 94, 0.16)" /usr/share/nginx/html
profitFill present (good)

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-Ch-weW5f.js
```

```
NAME                  IMAGE               COMMAND                  SERVICE        CREATED          STATUS
matp-dashboard-ui-1   matp-dashboard-ui   "/docker-entrypoint.…"   dashboard-ui   16 seconds ago   Up 6 seconds
```

---

## 3. Scope

Read-only on the SOL question — no order, position, trigger or service was modified while
investigating it. The only code change in this commit is the chart border removal in
`dashboard-ui`. No trading behaviour changed.
