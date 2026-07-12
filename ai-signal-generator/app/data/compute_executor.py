"""
Shared thread pool for CPU-bound, synchronous market-data computations
(pandas_ta indicators, geometry/volume-profile/divergence/volatility math).

These are plain `def` functions called directly from async node_ingest code.
With no executor, each call blocks the *entire process* event loop for its
duration — freezing every other strategy's in-flight HTTP requests and the
stream collector's websocket ping/pong keepalive. Measured cold-process cost
of `import pandas_ta` + first `ta.bbands()` call alone: ~14s — comfortably
past ccxt.pro's 10s default keepalive window (bybit/okx "ping-pong keepalive
missing on time" disconnects traced back to this). Route CPU-bound compute
calls through here instead of calling them directly.

max_workers=1, deliberately: the host has a single CPU core, so >1 worker
buys zero parallelism — it only multiplies GIL contention against the event
loop thread. Observed live (2026-07-12 19:00 candle-close wake, 4 workers):
all strategies waking in the same second queued dozens of GIL-heavy computes,
the loop's timers fired 39s late, every 10s HTTP timeout blew (technical /
sentiment inputs missing from prompts across all strategies), and every
collector websocket dropped on keepalive. One worker + the main thread keeps
GIL hand-off fair; computations queue instead of stampeding.

warmup() pays the one-time pandas_ta import cost at service startup instead
of at the first candle-close wake.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='compute')


def _warmup_sync() -> None:
    import time
    t0 = time.monotonic()
    import pandas as pd
    import pandas_ta as ta
    close = pd.Series(range(1, 61), dtype=float)
    ta.bbands(close, length=20, std=2)
    logger.info("compute_executor warmup done in %.1fs (pandas_ta imported)", time.monotonic() - t0)


def warmup() -> None:
    """Fire-and-forget: pre-import pandas_ta in the worker thread so the ~14s
    cold cost lands at startup, not inside the first scheduled cycle."""
    logger.info("compute_executor warmup scheduled")
    future = executor.submit(_warmup_sync)

    def _report(fut):
        exc = fut.exception()
        if exc is not None:
            logger.error("compute_executor warmup failed: %s: %s", type(exc).__name__, exc)

    future.add_done_callback(_report)
