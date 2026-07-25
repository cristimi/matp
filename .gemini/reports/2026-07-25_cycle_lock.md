# ai-signal-generator — per-strategy cycle lock (review finding 1)

**Date:** 2026-07-25
**Branch:** main
**Status:** DONE — deployed, semantics proven

Implements finding 1 of `.gemini/reports/2026-07-25_ai_signal_generator_review.md`.

---

## Problem

Three paths invoked the graph for a strategy with no mutual exclusion — the scheduler loop, the
event watcher (`event_watcher.py:57`), and the manual/dashboard trigger (`main.py`). The scheduler
awaits its own cycles so it cannot overlap itself, but the other two run as independent tasks.

Two concurrent cycles for one strategy both read `position_open=False`, and both pass `node_guard`:
its cooldown query reads `ai_signal_log WHERE gate_passed`, and that row is only written by
`node_dispatch` at the *end* of a cycle, so neither cycle can see the other. Both can dispatch an
entry, and nothing downstream rejects the second.

Observed 13 times in 14 days, minimum gap 14s against a ~90–120s cycle. Every one decided `hold`,
which is the only reason it never produced a double entry.

---

## Change

**`app/cycle_lock.py`** — `cycle_slot(strategy_id, trigger_reason)`, an async context manager
yielding `True` if the caller owns the strategy's slot and `False` if a cycle is already running.

Two deliberate design choices, both documented in the module:

- **Non-blocking, drop-don't-queue.** A trigger arriving mid-cycle is dropped and logged, not
  queued. Queuing would run the second cycle on market data gathered before the first finished —
  the same staleness the lock exists to prevent — and would let triggers stack behind a slow cycle.
- **A `set` with a synchronous check-and-add**, not an `asyncio.Lock`. There is no await between the
  membership test and the insert, so no interleaving is possible on a single event loop. This
  sidesteps the check-then-acquire window that `if lock.locked(): ... await lock.acquire()` has.

Single-process assumption, stated in the module docstring: the service runs one uvicorn worker
(`Dockerfile` CMD has no `--workers`), so schedulers, the event watcher and HTTP handlers share one
event loop. If it is ever run multi-worker this must move to a Redis lock.

**Wiring:**
- `scheduler._trigger_cycle()` — wraps the whole cycle. Covers both the scheduler loop and the
  event watcher, since the watcher calls this same method.
- `main.internal_trigger` — takes the slot for the whole handler and returns **409** if a cycle is
  already running; the body moved to `_run_manual_trigger()` unchanged.
- `GET /internal/cycles/in-flight` — diagnostics.

---

## Verification

Semantics, exercised directly:

```
Cycle already in flight for s1 — dropping volume_spike trigger
1. same strategy concurrent : [('scheduled', True), ('volume_spike', False)] -> PASS
2. different strategies     : [('a', True), ('b', True)] -> PASS
3. sequential reuse         : [('first', True), ('second', True)] -> PASS
4. released after exception : True -> PASS
5. no leaked slots          : {} -> PASS
```

Case 1 is the bug: two concurrent triggers for one strategy, exactly one proceeds and the loser is
logged. Case 2 confirms no false serialisation across strategies. Cases 3–5 confirm the slot is
always released, including when the cycle raises.

Through the real HTTP handler:

```
$ GET /internal/cycles/in-flight
{"in_flight":[]}

$ POST /internal/trigger {"strategy_id":"__no_such_strategy__"}
HTTP/1.1 404 Not Found          ← downstream error, not a guard error

$ GET /internal/cycles/in-flight
{"in_flight":[]}                ← slot released after the failure
```

The guard doesn't alter the normal path, and the slot is acquired and released through the real
handler — the `finally` works in production wiring, not just in the unit test.

Service healthy, all 6 schedulers restarted clean, no import or startup errors.

---

## Not exercised, and why

**The literal 409 response was not produced against a live strategy.** Doing so requires two
genuinely concurrent manual triggers, and every strategy is currently `dry_run = false`:

```
        strategy_id         | dry_run | enabled | open_pos
----------------------------+---------+---------+----------
 ai-btc-6f8c                | f       | t       |        0
 bnb-ai-scalper-edbb        | f       | t       |        0
 ...all 7 live
```

A manual trigger on a live strategy runs a real cycle that can dispatch a real order. Opening a
position to observe an HTTP status code is not a trade I'm willing to make on someone's account.

What that leaves unproven is one line — `raise HTTPException(status_code=409, ...)` — sitting on the
`acquired is False` branch, and that branch's contract is exactly what unit case 1 proves. The gap
is narrow and deliberate; if you want it closed, the clean way is a throwaway strategy row with
`dry_run = true` to trigger against.

**The scheduler-vs-watcher path will prove itself in production**: the next time an event trigger
lands mid-cycle, the log will carry `Cycle already in flight for … — dropping volume_spike trigger`
instead of a second cycle. Given 13 overlaps in 14 days, expect one within a few days.

---

## Note on the remaining findings

Findings 2 (entry cooldown bypassable by switching action type — 13 real bypasses, bnb re-entered
at 15min against a 120min setting) and 3 (`max_concurrent_trades` enforced in the backtester,
ignored live) are untouched.
