"""
Tiny process-wide TTL cache for symbol-agnostic fetchers.

node_ingest fans out one fetch per enabled data source *per strategy per
cycle* — fine for per-symbol data (OHLCV, orderbook, funding rate: genuinely
different per strategy), wasteful for market-wide data that's identical
across every strategy regardless of symbol (fear/greed index, BTC dominance,
DXY/US10Y, economic calendar, general crypto news headlines). Those were
each being refetched independently by every strategy, every cycle, hitting
the same external API with a fresh connection every time — a real
contributor to the contention that shows up as ai_signal_log.missing_inputs.

Only apply this to fetchers whose result doesn't vary by call arguments
(this cache ignores args/kwargs entirely — one shared slot per decorated
function). Success and failure get separate TTLs so a transient blip
doesn't sideline every concurrent caller for the full success window, and
concurrent callers on a miss share one in-flight fetch via a lock rather
than each firing their own request.
"""

import asyncio
import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar('T')


def ttl_cached(success_ttl: float, failure_ttl: float) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        state = {'expires': 0.0, 'value': None}
        lock = asyncio.Lock()

        async def wrapper(*args, **kwargs) -> T:
            now = time.monotonic()
            if state['expires'] > now:
                return state['value']
            async with lock:
                now = time.monotonic()
                if state['expires'] > now:
                    return state['value']
                value = await fn(*args, **kwargs)
                state['value']   = value
                state['expires'] = now + (success_ttl if value is not None else failure_ttl)
                return value

        wrapper.__name__ = fn.__name__
        wrapper.__doc__  = fn.__doc__
        return wrapper
    return decorator
