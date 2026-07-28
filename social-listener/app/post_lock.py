"""Per-post mutual exclusion for the brain.

Two independent paths call `handle()` for the same post and neither used to
coordinate: the live `_LiveBuffer` flush, and `_catchup_loop`, which sweeps
Telegram history every 60s for messages the live handler may have missed.

`handle()` opens with `already_shadow_evaluated(key_id)`, but that row is only
written at the *end*, after the orders have gone out. Between the two lies an LLM
call of several seconds, so both callers read False and both act.

That is not theoretical. On 2026-07-27 post 9793-9794 was picked up by catchup at
23:08:52 and by the live buffer at 23:08:59; both extracted it, both fired a
partial close, and the position was reduced twice — 0.000775 then 0.0003875, eight
seconds apart, against one instruction. `insert_shadow_order`'s ON CONFLICT DO
NOTHING then hid it, leaving two orders behind a single audit row.

Acquisition is **non-blocking by design**: the second caller drops the post rather
than queuing behind the first. Queuing would re-run it the moment the first
finished, which is precisely the duplicate this exists to prevent — and the first
caller's shadow row makes `already_shadow_evaluated` true for every later attempt
anyway.

Single-process assumption: one asyncio loop runs the live handler, the catchup
loop and the trim watcher. The check-and-add below has no await between the two
statements, so no interleaving is possible. Running this service with more than
one process would need a Redis lock instead.
"""

import logging
from contextlib import asynccontextmanager

log = logging.getLogger(__name__)

_in_flight: set[int] = set()


def in_flight() -> set[int]:
    """Posts currently being judged (diagnostics)."""
    return set(_in_flight)


@asynccontextmanager
async def post_slot(key_id: int, source: str = "unknown"):
    """Yield True if this caller owns the post, False if another is judging it.
    The caller must do nothing when False."""
    if key_id in _in_flight:
        log.warning("post %s already being judged — dropping the %s copy",
                    key_id, source)
        yield False
        return

    _in_flight.add(key_id)
    try:
        yield True
    finally:
        _in_flight.discard(key_id)
