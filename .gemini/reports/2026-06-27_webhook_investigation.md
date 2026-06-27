# TV Webhook Investigation — Missing Opens & Intrabar Firing

**Date:** 2026-06-27  
**Scope:** `tv_test_harness` strategy, Jun 22–27  
**Question:** Why do some TV webhooks show `no_position_to_close` with no preceding open in the DB,
and does TV fire intrabar or at bar close?  
**Type:** Read-only investigation. No code changes.

---

## 1. Log Coverage

```
order-listener logs (--since 168h):  Jun 22 16:11 UTC → Jun 24 21:00 UTC
order-executor  logs (--since 168h):  Jun 20         → Jun 24 21:00 UTC
```

**Jun 25, 26, 27: zero log entries** despite the DB showing 39 orders in that window.
The container was created 4 days ago (Jun 23) but docker ps reports "Up 2 days" (since Jun 25).
A container recreation around Jun 24–25 wiped the stdout log buffer — the Jun 25-27 webhook
activity cannot be verified from logs.

This is itself a finding: **log retention does not cover the missing-webhook window (Jun 26).**

---

## 2. `no_position_to_close` Is Not a Failure

In the retained window **every single `POST /webhook/tv_test_harness` returned 200 OK**.
Zero 4xx / 5xx responses in 3,600+ lines of webhook-handler output.

The `no_position_to_close` status means: the webhook was received, parsed, and processed
successfully — but when the handler tried to close the specified side it found no open position.
It is a valid business outcome, not a delivery failure.

---

## 3. Full Order Table Since Cutover (Jun 22 23:00 UTC)

```
2026-06-24 02:00:17  close_short  no_position_to_close
2026-06-24 02:00:18  open_long    filled              ← paired open (1 s later)
2026-06-24 12:07:27  close_long   no_position_to_close
2026-06-24 12:07:30  open_short   filled              ← paired open (3 s later)
2026-06-24 13:01:31  close_short  filled
2026-06-24 13:08:40  close_short  filled
2026-06-25 04:56:18  open_long    filled
...
2026-06-25 13:33:39  exchange_close  filled sell      ← bracket exit
2026-06-26 09:49:36  close_short  no_position_to_close  ← LONE close, no open after
2026-06-26 09:54:32  close_short  no_position_to_close
2026-06-26 09:54:33  open_short   filled              ← paired open (1 s later)
2026-06-26 10:39:27  close_short  filled
2026-06-26 12:43:09  exchange_close  filled buy
2026-06-26 14:36:47  close_short  no_position_to_close  ← LONE close
2026-06-26 14:48:08  close_short  no_position_to_close
2026-06-26 14:48:09  open_short   filled              ← paired open (1 s later)
2026-06-26 14:55:39  close_short  filled
2026-06-26 14:57:10  close_short  filled
2026-06-26 14:57:11  open_short   filled
2026-06-26 15:12:10  exchange_close  filled buy
2026-06-26 15:26:17  close_long   no_position_to_close  ← LONE close
2026-06-26 15:28:10  close_long   no_position_to_close
2026-06-26 15:28:12  open_long    filled              ← paired open (2 s later)
2026-06-26 18:47:42  close_short  no_position_to_close  ← LONE close
2026-06-26 18:47:48  exchange_close  filled sell      ← bracket fired 6 s later
2026-06-26 19:06:54  close_short  no_position_to_close  ← LONE close
2026-06-26 19:09:51  open_long    filled              ← open 3 min later (separate signal)
2026-06-26 20:31:08  close_long   filled
2026-06-26 20:33:55  exchange_close  filled sell
2026-06-26 23:31:07  close_long   no_position_to_close
2026-06-26 23:31:09  open_short   filled              ← paired open (2 s later)
2026-06-27 02:03:53  close_short  filled
2026-06-27 02:03:55  open_long    filled
2026-06-27 02:20:53  close_long   filled
2026-06-27 03:07:03  close_long   no_position_to_close  ← LONE close (last entry)
```

---

## 4. Two Sub-patterns

### Pattern A — Flip-pair (close half of a reversal)

TV's Pine Script fires `strategy.close("X")` + `strategy.entry("Y", ...)` as back-to-back alerts,
arriving at the engine ≤3 seconds apart. The close fails because the engine is already flat
(a bracket exit fired between TV's previous open and this close). The open succeeds.
**The matching open IS in the DB.**

| Orphaned close | Paired open (delay) |
|---|---|
| Jun 24 02:00:17 close_short | 02:00:18 open_long (1 s) |
| Jun 24 12:07:27 close_long  | 12:07:30 open_short (3 s) |
| Jun 26 09:54:32 close_short | 09:54:33 open_short (1 s) |
| Jun 26 14:48:08 close_short | 14:48:09 open_short (1 s) |
| Jun 26 15:28:10 close_long  | 15:28:12 open_long (2 s) |
| Jun 26 23:31:07 close_long  | 23:31:09 open_short (2 s) |

### Pattern B — Lone exit signal (TV ↔ engine state divergence)

TV fires a pure close alert with no immediate open following. This happens because TV's internal
position tracker diverges from the engine's actual state whenever a **bracket exit** (`exchange_close`)
fires. TV does not receive any callback when the exchange's TP/SL bracket order fills — it still
believes the position is live. When TV's own strategy condition later fires a close, the engine is
already flat → `no_position_to_close`.

| Orphaned close | Context |
|---|---|
| Jun 26 09:49:36 close_short | ~20h after last bracket exit (Jun 25 13:33 exchange_close); TV thought a short was live, engine was flat |
| Jun 26 14:36:47 close_short | 12 min before paired flip at 14:48; likely a pure exit condition |
| Jun 26 15:26:17 close_long  | 2 min before paired flip at 15:28 |
| Jun 26 18:47:42 close_short | bracket exit fires 6 s later (18:47:48); TV and bracket near-simultaneous |
| Jun 26 19:06:54 close_short | open_long arrives 3 min later (separate signal, not same webhook pair) |
| Jun 27 03:07:03 close_long  | last DB entry; no follow-up in window |

---

## 5. Root Cause

**TV and the engine share no runtime feedback channel.**

When a bracket order (TP/SL) fires and the engine records an `exchange_close`, TV's Pine Script
strategy never learns about it. TV continues tracking its own internal position state. On the next
RSI signal, TV fires `strategy.close()` — arriving at the engine when the position was already gone.

This is not a network or delivery problem. It is a **design gap**: unidirectional signal flow
(TV → engine) with no acknowledgement path.

---

## 6. Intrabar vs Bar-Close: Definitively Intrabar

TV fires `calc_on_every_tick=true`. Entry offsets (seconds from the top of the hour) for the
15 TV open entries since the both-flat cutover:

```
19, 81, 235, 451, 592, 682, 1692, 1827, 1870, 2307, 2497, 2889, 3274, 3378, 3431
```

Min = 19 s, Max = 3431 s (~57 min). Spread uniformly across the full hour.
**Not clustered near zero (bar open) or 3600 (bar close).**

Engine fires at bar close (signal_bar_time = the closed 1h candle). TV fires whenever RSI crosses
the threshold during the bar — any tick between second 1 and second 3599 of the hour.

---

## 7. Per-case Verdict Summary

| Date/time (UTC) | Signal | Status | Verdict |
|---|---|---|---|
| Jun 24 02:00 | close_short | no_position_to_close | Pattern A — flip pair; open_long filled 1 s later |
| Jun 24 12:07 | close_long | no_position_to_close | Pattern A — flip pair; open_short filled 3 s later |
| Jun 26 09:49 | close_short | no_position_to_close | Pattern B — lone exit; TV/engine diverged after Jun 25 13:33 bracket exit |
| Jun 26 09:54 | close_short | no_position_to_close | Pattern A — flip pair; open_short filled 1 s later |
| Jun 26 14:36 | close_short | no_position_to_close | Pattern B — lone exit, 12 min before next flip |
| Jun 26 14:48 | close_short | no_position_to_close | Pattern A — flip pair; open_short filled 1 s later |
| Jun 26 15:26 | close_long | no_position_to_close | Pattern B — lone exit, 2 min before next flip |
| Jun 26 15:28 | close_long | no_position_to_close | Pattern A — flip pair; open_long filled 2 s later |
| Jun 26 18:47 | close_short | no_position_to_close | Pattern B — bracket exit fired 6 s later |
| Jun 26 19:06 | close_short | no_position_to_close | Pattern B — open_long 3 min later (separate signal) |
| Jun 26 23:31 | close_long | no_position_to_close | Pattern A — flip pair; open_short filled 2 s later |
| Jun 27 03:07 | close_long | no_position_to_close | Pattern B — lone exit; no follow-up in window |

Log coverage for Jun 26-27: **not retained** — all 200 OK verdicts are inferred from
the consistent Jun 22-24 pattern and DB evidence (paired opens landing in DB for Pattern A cases).

---

## 8. Recommendations (Informational)

1. **Persist order-listener logs to the volume mount** (`logs:/app/logs`) with rotation — the
   current stdout-only approach loses history on container recreate.

2. **TV ↔ engine feedback gap** (deferred): if TV state divergence causes unwanted double-opens,
   the fix is a callback webhook from the engine to TV on bracket exits. Tracked in roadmap.

3. **`no_position_to_close` is not a bug** — it is the expected outcome whenever TV sends a close
   for a side that the engine does not hold. No code change needed for this status.
