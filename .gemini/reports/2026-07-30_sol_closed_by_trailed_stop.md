# SOL trade closed "without hitting SL or TP" — what actually happened

**Date:** 2026-07-30
**Strategy:** `sol-ai-6486` (BloFin demo account `blofin-blofin-demo-v5vr`)
**Position:** `c2a2a927-697a-4bce-b472-439d46b26808` — SOL-USDT short, opened 03:01, closed 09:09 UTC
**Investigation only — no code was changed.**

---

## Short version

The trade **did** hit its stop-loss. But not the stop-loss you can see.

The AI moved the stop three times while the trade was open. The last move put it at
**73.88** — which is *above* the entry price of 73.79, so it locked in a loss instead of
protecting a profit. Price touched 73.93 and the stop fired.

The database still stores the **original** stop (74.5011) on the opening order, and the
dashboard reads exactly that field. So the UI showed 74.50 while the real stop on the
exchange was 73.88. That mismatch is why the close looks unexplained.

---

## Evidence

### 1. The position and its recorded stops

```
id                      | c2a2a927-697a-4bce-b472-439d46b26808
symbol / side           | SOL-USDT short
entry_price             | 73.79
size                    | 2.682968
opened_at               | 2026-07-30 03:01:03+00
closed_at               | 2026-07-30 09:09:33+00
closing_price           | 73.87756457564576
pnl_realized            | -0.4774074600
close_reason            | Closed on exchange
mfe_price               | 73.29     (best it ever got)
mae_price               | 73.93     (worst it ever got)
liquidation_price       | 77.048
```

Opening order `f9a46079-f62a-4484-b951-c9094d0103fc` as stored in `orders`:

```
   signal   | tp_price | sl_price
------------+----------+----------
 open_short |  72.1764 |  74.5011
```

Price never reached 74.5011 (worst was 73.93) and never reached 72.1764. So on the
recorded numbers the close is impossible — that is the illusion.

### 2. The stop was moved four times by the AI

`order-listener` log, all four `adjust-stops` calls for this position:

```
04:01:04  adjust-stops pos=c2a2a927 (SOL-USDT short) tp=72.2 sl=74.2  cancelled=2 placed=2
06:15:19  adjust-stops pos=c2a2a927 (SOL-USDT short) tp=72.2 sl=74.2  cancelled=2 placed=2
06:45:20  adjust-stops pos=c2a2a927 (SOL-USDT short) tp=None sl=74.0  cancelled=2 placed=2 preserved=tp@72.2
08:30:32  adjust-stops pos=c2a2a927 (SOL-USDT short) tp=None sl=73.88 cancelled=2 placed=2 preserved=tp@72.2
```

So the live stop walked **74.50 → 74.20 → 74.00 → 73.88**.

### 3. The 73.88 stop was really placed on the exchange

`order-executor` log at 08:30:

```
08:30:30,531 modify-stops blofin-blofin-demo-v5vr/SOL-USDT: found 2 trigger orders
08:30:30,534 modify-stops ...: preserving tp=72.2 (not priced by caller, carried forward instead of dropped)
08:30:30,827 Cancelled trigger oid=10003027202 (sl) for SOL-USDT
08:30:31,124 Cancelled trigger oid=10003027201 (tp) for SOL-USDT
08:30:31,449 Blofin trigger (tp) placed at 72.2  for SOL-USDT, tpslId=10003028823
08:30:31,771 Blofin trigger (sl) placed at 73.88 for SOL-USDT, tpslId=10003028824
```

### 4. The AI's own reasoning for that move (`ai_signal_log` id 5476)

```
triggered_at    | 2026-07-30 08:30:20.181074+00
proposed_action | adjust_stops
confidence      | 0.680
gate_passed     | t
llm             | cerebras / gpt-oss-120b
reasoning       | ... The position is up 0.136% (price 73.69 vs entry 73.79). To protect
                  gains, the stop is moved up to the recent 4h resistance level around
                  73.88, tightening risk while keeping the original TP near 72.2. ...
```

The stated goal was "protect gains" on a **+0.136%** position. For a short entered at
73.79, a stop at 73.88 is 0.12% on the *wrong* side of entry — it cannot protect anything,
it can only book a loss. It was also only 0.26% away from the live price (73.69) at the
moment it was placed.

### 5. The stop fired, then the reconciler noticed

The exchange filled the stop between 09:06 and 09:07. Our reconciler needs 3 consecutive
misses before it declares the position closed:

```
09:07:30  reconciler: position c2a2a927 (SOL-USDT short) miss 1/3 db=2.682968 exchange=0
09:08:31  reconciler: position c2a2a927 (SOL-USDT short) miss 2/3 db=2.682968 exchange=0
09:09:32  reconciler: position c2a2a927 (SOL-USDT short) miss 3/3 db=2.682968 exchange=0
09:09:33  Closed position c2a2a927 ... close_size=2.682968, fill=73.87756457564576,
          pnl_gross=-0.2421, pnl_net=-0.48132486
09:09:33  reconciler: closed position c2a2a927 reason=Closed on exchange pnl=-0.2373
```

**Fill 73.87756 vs stop trigger 73.88.** That is the stop, filling with normal slippage.
The arithmetic also matches exactly: `2.682968 × (73.79 − 73.87756) = −0.2350`, reported
gross −0.2421; net −0.4813 after the 0.1192 exit fee and the entry fee.

Because the fill came from an exchange trigger rather than one of our own orders, the
close was booked as a synthetic `exchange_close` order with `signal_source = reconciler`
and `close_reason = "Closed on exchange"` — not as "stop-loss hit".

---

## Why the UI showed the wrong stop

`order-listener/app/webhook_handler.py:494` (`adjust_stops_for_strategy`) calls the
executor to cancel and re-place the exchange triggers, logs the result, and returns. It
never writes the new stop back to the `orders` row, and never appends a row to
`order_price_history`.

The dashboard reads the opening order's stored value —
`dashboard-api/src/routes/strategies.ts:1000` selects `o_open.sl_price` — so it can only
ever show 74.5011, the number from 03:01. Every later stop move is invisible.

Contrast: the separate `modify-order` route (`webhook_handler.py:739`) *does* persist
`sl_price` and write `order_price_history`. `adjust-stops` does not.

---

## This is not a one-off

The SOL position before it died the same way:

```
01:01  open_short @ 73.22, sl 73.9193, tp 71.9413
01:15  adjust-stops pos=bfcef765 (SOL-USDT short) tp=71.86 sl=73.84 cancelled=2 placed=2
01:34  Closed position bfcef765 ... fill=73.84923076923077, pnl_gross=-1.7136
```

Stop moved 73.92 → 73.84 (again above the 73.22 entry after price ran against it), filled
at 73.849. The stored `sl_price` on that opening order is still 73.9193, so it too looks
like "closed without hitting SL".

Both of today's SOL losses have the same cause.

---

## Why the guard let it through

`ai-signal-generator/app/graph/nodes/node_guard.py:212-235` validates `adjust_stops`, but
only checks the new stop is on the correct side of the **current price**:

```python
else:  # short
    wrong = ((new_sl is not None and float(new_sl) <= current_price)
             or (new_tp is not None and float(new_tp) >= current_price))
```

With current price 73.69 and `new_sl` 73.88, the stop is above the price, so it is a
structurally valid short stop and the gate passes (`gate_passed = t`).

Nothing checks:

- that the new stop is on the **profitable** side of the entry price when the AI claims to
  be locking in gains (73.88 vs entry 73.79 is not),
- that the stop is not absurdly tight relative to volatility (0.26% away, versus the
  0.96% the original ATR-derived stop used),
- that a trail only ever moves in the favourable direction *relative to entry*, not just
  relative to the last stop.

## Related context worth noting

Between 04:45 and 07:01 the AI also fired two `partial_close` actions that closed
**0.013482** and **0.013550** SOL out of a 2.68 SOL position — about 0.5% each, i.e. ~1 USD
of notional, while the reasoning text says "partial close of 50% to lock profit". The
trades were effectively no-ops but each one paid a fee. That is a separate issue from the
stop, but it is visible in the same position's history and is worth a look.

---

## Summary of findings

1. **The trade did hit a stop** — the exchange stop at 73.88, placed by the AI at 08:30,
   filled at 73.87756 at ~09:06. Not a mystery close, not a liquidation, not manual.
2. **The stop was set on the losing side of entry.** Entry 73.79 short, stop 73.88. The
   AI described this as "protecting gains" on a +0.136% position; it guaranteed a small
   loss instead.
3. **The stored/displayed stop is stale.** `adjust-stops` changes the exchange but does
   not update `orders.sl_price` or `order_price_history`, and the dashboard reads exactly
   that stale field. This is why the close looks like it happened at neither SL nor TP.
4. **The safety gate cannot catch this.** It only checks the stop is on the right side of
   the *current price*, never of the *entry price*, and has no minimum-distance rule.
5. **The pattern repeated** on the previous SOL position the same night (−1.71 gross), and
   is the likely explanation for other "closed on exchange" SOL trades.

No fixes were applied — this report is diagnosis only, as requested.
