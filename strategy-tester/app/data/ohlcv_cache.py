"""
Cache read/write helpers for tester.ohlcv_cache.
Cache key is (symbol, timeframe, exchange, candle_ts).
Symbol is stored in normalised dash format: BTC-USDT.
"""
import logging
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)

_TIMEFRAME_MS = {
    '1m':   60_000, '3m':   180_000,  '5m':   300_000,
    '15m':  900_000, '30m':  1_800_000,
    '1h':   3_600_000, '2h':   7_200_000, '4h':  14_400_000,
    '8h':  28_800_000, '1d':  86_400_000,
}


def _ms_to_dt(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def _row_to_candle(r: asyncpg.Record) -> dict:
    return {
        'timestamp': int(r['candle_ts'].timestamp() * 1000),
        'open':   float(r['open']),
        'high':   float(r['high']),
        'low':    float(r['low']),
        'close':  float(r['close']),
        'volume': float(r['volume']),
    }


async def get_cached_candles(
    pool,
    symbol: str,
    timeframe: str,
    exchange: str,
    ts_from_ms: int,
    ts_to_ms: int,
) -> list[dict]:
    """Return candles in [ts_from_ms, ts_to_ms] from the cache, sorted ascending."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT candle_ts, open, high, low, close, volume
            FROM tester.ohlcv_cache
            WHERE symbol    = $1
              AND timeframe = $2
              AND exchange  = $3
              AND candle_ts >= $4
              AND candle_ts <= $5
            ORDER BY candle_ts
            """,
            symbol, timeframe, exchange,
            _ms_to_dt(ts_from_ms),
            _ms_to_dt(ts_to_ms),
        )
    return [_row_to_candle(r) for r in rows]


def is_cache_sufficient(
    cached: list[dict],
    ts_from_ms: int,
    ts_to_ms: int,
    timeframe: str,
) -> bool:
    """Return True when cached candles cover the full requested range."""
    if not cached:
        return False
    tf_ms = _TIMEFRAME_MS.get(timeframe)
    if tf_ms is None:
        return False
    # Expected candle count for the range; allow 2-candle slop for exchange gaps
    expected = max(1, (ts_to_ms - ts_from_ms) // tf_ms)
    return len(cached) >= expected - 2


async def upsert_candles(
    pool,
    symbol: str,
    timeframe: str,
    exchange: str,
    candles: list[dict],
) -> int:
    """Bulk-upsert candles into tester.ohlcv_cache. Returns number of rows processed."""
    if not candles:
        return 0
    records = [
        (
            symbol, timeframe, exchange,
            _ms_to_dt(c['timestamp']),
            c['open'], c['high'], c['low'], c['close'], c['volume'],
        )
        for c in candles
    ]
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO tester.ohlcv_cache
                (symbol, timeframe, exchange, candle_ts, open, high, low, close, volume)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (symbol, timeframe, exchange, candle_ts) DO NOTHING
            """,
            records,
        )
    logger.debug("upsert_candles: %d rows for %s %s %s", len(candles), symbol, timeframe, exchange)
    return len(candles)
