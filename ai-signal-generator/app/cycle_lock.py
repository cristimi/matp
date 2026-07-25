"""Per-strategy cycle mutual exclusion.

Three paths invoke the graph for a strategy and none of them used to coordinate:
the scheduler loop, the event watcher (`event_watcher._check_all_triggers`), and
the manual/dashboard trigger. The scheduler awaits its own cycles so it cannot
overlap itself, but the other two run as independent tasks.

Two concurrent cycles for one strategy both read `position_open=False`, and both
pass `node_guard` — its cooldown query reads `ai_signal_log WHERE gate_passed`,
and that row is only written by `node_dispatch` at the *end* of a cycle, so
neither cycle can see the other. Both can therefore dispatch an entry, and
nothing downstream rejects the second one.

Measured before this existed: 13 overlapping cycles in 14 days, minimum gap 14s
against a ~90-120s cycle. Every one happened to decide `hold`, which is the only
reason it never produced a double entry
(.gemini/reports/2026-07-25_ai_signal_generator_review.md).

Acquisition is **non-blocking by design**: a trigger arriving while a cycle is in
flight is dropped, not queued. Queuing would run the second cycle on market data
gathered before the first one finished — the same staleness the lock exists to
prevent — and would let triggers stack up behind a slow cycle.

Single-process assumption: the service runs one uvicorn worker (no `--workers`),
so the scheduler, the event watcher and the HTTP handlers share one event loop.
Check-and-add below happens with no await between the two statements, so no
interleaving is possible. If this service is ever run multi-worker, this must
move to a Redis lock.
"""

import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

_in_flight: set[str] = set()


def in_flight() -> set[str]:
    """Strategies with a cycle currently running (diagnostics)."""
    return set(_in_flight)


@asynccontextmanager
async def cycle_slot(strategy_id: str, trigger_reason: str = "unknown"):
    """Yield True if this caller owns the strategy's cycle slot, False if a cycle
    is already running. The caller must skip its work when False."""
    if strategy_id in _in_flight:
        logger.warning(
            "Cycle already in flight for %s — dropping %s trigger",
            strategy_id, trigger_reason,
        )
        yield False
        return

    _in_flight.add(strategy_id)
    try:
        yield True
    finally:
        _in_flight.discard(strategy_id)
