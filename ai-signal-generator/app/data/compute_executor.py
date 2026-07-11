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
"""

from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='compute')
