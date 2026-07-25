# ai-signal-generator — bug review

**Date:** 2026-07-25
**Scope:** `ai-signal-generator` (9053 lines), decision path prioritised: scheduler → ingest →
guard → dispatch → webhook, plus the risk/sizing config surface.
**Status:** Review only — no code changed. Three confirmed bugs, all evidenced against live data.

---

## 1. No per-strategy cycle lock — concurrent cycles are real (highest severity)

Three code paths invoke the graph for a strategy, and **none of them take a lock**:

| Path | Site |
|---|---|
| Scheduler loop | `scheduler._loop()` → `_trigger_cycle()` |
| Event watcher | `event_watcher.py:57` → `scheduler._trigger_cycle()` |
| Manual / dashboard trigger | `main.py:388` → `graph.ainvoke()` |

`grep -rn "Lock\|lock\|semaphore"` across `scheduler.py`, `event_watcher.py` and `main.py` returns
nothing but an unrelated `self._wakeup.set()`. The scheduler's own loop awaits each cycle so it
cannot overlap *itself* — but the event watcher and the manual endpoint run as independent tasks
and can start a cycle while one is already in flight.

**This is not theoretical. It has happened 13 times in the last 14 days:**

```
        strategy_id         |    triggered_at     | gap_s |               reasons                |   actions
----------------------------+---------------------+-------+--------------------------------------+--------------
 bnb-ai-scalper-edbb        | 2026-07-12 18:16:59 |    14 | manual_dashboard -> manual_dashboard | hold -> hold
 bnb-ai-scalper-edbb        | 2026-07-12 18:17:49 |    19 | manual_dashboard -> manual_dashboard | hold -> hold
 bnb-ai-scalper-edbb        | 2026-07-24 09:45:30 |    20 | scheduled -> volume_spike            | hold -> hold
 bnb-ai-scalper-edbb        | 2026-07-12 18:17:31 |    31 | manual_dashboard -> manual_dashboard | hold -> hold
 tao-ai-range-rotation-d257 | 2026-07-25 18:00:50 |    40 | scheduled -> volume_spike            | hold -> hold
 sol-ai-6486                | 2026-07-24 14:00:41 |    58 | volume_spike -> scheduled            | hold -> hold
 ... 13 rows total, min gap 14s
```

A cycle takes ~90–120s end to end (the project's own measurement in the 2026-07-10 report: "~120s
→ ~93s"). A 14-second gap means both cycles were unambiguously in flight together. The
`scheduled -> volume_spike` and `volume_spike -> scheduled` pairs prove scheduler/event-watcher
overlap; the `manual_dashboard -> manual_dashboard` pairs prove the endpoint has no guard at all.

**Why it hasn't caused damage yet: all 13 pairs decided `hold`.** Pure luck.

**The failure mode when it isn't `hold`.** Two concurrent cycles each:
1. read `position_open` — both see `False` (neither has dispatched);
2. call the LLM — both may return `open_long`;
3. pass `node_guard` — `position_already_open` is False for both, and the cooldown query reads
   `ai_signal_log WHERE gate_passed = TRUE`, but that row is only written in `node_dispatch` at the
   *end* of the cycle, so neither sees the other;
4. both dispatch → **two entries**.

Nothing downstream stops it: `grep` for a duplicate-entry / existing-position check in
`order-listener` finds only an unrelated reconciler comment, and see finding 3 — the one risk
setting that sounds like it would cap this is not enforced.

**Suggested fix:** an `asyncio.Lock` per strategy, acquired in `_trigger_cycle()` and by the manual
endpoint, with a non-blocking `try`/skip so a queued trigger is dropped (and logged) rather than
serialised behind a 2-minute cycle.

---

## 2. The entry cooldown is per-action-type, so switching entry action bypasses it

`node_guard.py:128-141`:

```sql
SELECT triggered_at FROM ai_signal_log
WHERE strategy_id = $1
  AND proposed_action = $2        -- ← the bug
  AND gate_passed = TRUE
  AND triggered_at >= $3
```

`_ACTION_COOLDOWN` maps all four entry actions (`open_long`, `open_short`, `place_limit_long`,
`place_limit_short`) to the **same config key** `cooldown_entry_minutes` — but the lookup filters on
the exact action, so each gets its own independent cooldown. A setting that reads as "minutes
between entries" is enforced as "minutes between entries *of this exact action type*", giving four
parallel cooldowns instead of one.

**13 real bypasses in 30 days:**

```
     strategy_id     |               sequence                | gap_min | cooldown_min
---------------------+---------------------------------------+---------+--------------
 bnb-ai-scalper-edbb | open_long -> open_short               |      15 |          120
 eth-ai-34d2         | place_limit_long -> open_long         |      59 |          240
 eth-ai-34d2         | place_limit_short -> place_limit_long |      59 |          240
 eth-ai-34d2         | place_limit_long -> place_limit_short |      60 |          240
 bnb-ai-scalper-edbb | open_long -> open_short               |      75 |          120
 eth-ai-34d2         | open_short -> place_limit_long        |      79 |          240
 bnb-ai-scalper-edbb | open_short -> open_long               |      89 |          120
```

`bnb-ai-scalper-edbb` is configured with a **120-minute** entry cooldown and took a new entry **15
minutes** after the previous one, purely because it was the opposite direction. `eth-ai-34d2` has a
**240-minute** cooldown and re-entered at **59 minutes** by switching from a limit entry to a market
entry. Both strategies actively use several entry actions, so this fires regularly rather than as
an edge case.

**Suggested fix:** group the cooldown by its config key, not the action — for
`cooldown_entry_minutes`, match `proposed_action = ANY($2)` over all four entry actions. One line
of intent, one query change.

---

## 3. `max_concurrent_trades` is enforced in the backtester and ignored live

Every reference in the repo:

```
dashboard-api/src/routes/ai.ts:31,35,602,621-624,646   ← validated (1..5), stored, displayed
ai-signal-generator/app/scheduler.py:132               ← SELECTed into the state row
ai-signal-generator/app/main.py:339                    ← hardcoded 1 in a dict literal
strategy-tester/…/backtest_engine.py:553, estimate.py  ← ACTUALLY ENFORCED
db/init.sql, migrations                                ← schema
order-listener / order-executor                        ← zero references
```

The live decision path loads the value and never reads it again. `node_guard` has no reference to
it at all. Meanwhile `strategy-tester` honours it in backtests.

So a user who sets `max_concurrent_trades = 3` gets: a backtest that models three concurrent
positions, and a live engine that ignores the setting entirely — capped at one position by
`node_guard`'s unrelated `position_already_open` check. **Backtest and live silently disagree about
a risk control**, which is worse than the setting simply not existing.

**Suggested fix:** either enforce it in `node_guard` (replace the boolean `position_open` check with
a count against the limit), or remove it from the live config surface and mark it tester-only. The
current half-wired state is the worst of the three options.

---

## Checked and found sound

Worth recording so these aren't re-investigated. Each looked like a bug and is correctly defended:

| Suspicion | Why it's fine |
|---|---|
| `resolved_size = position_size or 0.01` (`node_guard.py:384`) — fabricated 0.01 size on a full close | The Blofin adapter sets `reduceOnly: "true"` on closes (`blofin.py:467`, comment: *"Without reduceOnly, an oversized close flips the position"*), so an oversized close cannot flip. |
| `position_side or 'long'` (`dispatcher.py:29`) — a missing side would sell to "close" a short | `strategy_positions.side` is only ever `long`/`short`, 0 nulls and 0 zero-sizes across 153 rows. |
| `qty = round(notional / entry_price, 4)` — asset-agnostic precision | The executor re-rounds to the instrument's `lotSize` and enforces `minSize` (`blofin.py:92`). |
| `adjust_stops` dispatching during dry-run (`node_dispatch.py:165`) | `dispatch_adjust_stops` forwards `dry_run` to the listener (`dispatcher.py:131`); `cancel/amend` are unreachable in dry-run because §3b returns first. |
| `sl_frac` division in risk-mode sizing | Both call sites validate `sl_pct ∈ [0.05, 50]` before calling. |

---

## One latent risk (not currently reachable)

`blofin.py:93` — `enforced = max(min_size, rounded)` silently rounds an undersized order **up** to
the exchange minimum instead of rejecting it. With today's configs the smallest possible notional is
~$10 (risk $5 at the 50% max stop) → 0.0002 BTC, comfortably above zero, so it can't fire. But if
`margin_per_trade`/`risk_per_trade` were lowered, or a higher-priced instrument added, a size that
rounds toward zero in `node_guard` would be silently inflated to `minSize` and placed rather than
refused. Worth a explicit floor check on the signal side.

---

## Recommended order

1. **Per-strategy cycle lock** — the only one that can produce an unintended live position, and it
   has already occurred 13 times without firing.
2. **Cooldown grouping** — actively bypassed today; 15 minutes against a configured 120.
3. **`max_concurrent_trades`** — decide enforce-or-remove; the divergence from the backtester is the
   real problem.
