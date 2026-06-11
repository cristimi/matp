"""
Historical OHLCV fetcher with pagination.
Designed for backtest preloading, not live data fetch.
"""
import asyncio
import logging
from datetime import datetime, timezone

import ccxt.async_support as ccxt_async

logger = logging.getLogger(__name__)

_TIMEFRAME_MS = {
    '1m':   60_000, '3m':   180_000,  '5m':   300_000,
    '15m':  900_000, '30m':  1_800_000,
    '1h':   3_600_000, '2h':   7_200_000, '4h':  14_400_000,
    '8h':  28_800_000, '1d':  86_400_000,
}


async def fetch_ohlcv_range(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    ts_from_ms: int,
    ts_to_ms: int,
    batch_limit: int = 1000,
    pause_between_batches: float = 0.2,
) -> list[dict]:
    """
    Fetch all candles in [ts_from_ms, ts_to_ms] inclusive by paginating
    forward with `since`. Returns a deduplicated, sorted list of candle dicts.
    """
    tf_ms = _TIMEFRAME_MS.get(timeframe)
    if tf_ms is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    cls = getattr(ccxt_async, exchange_id, None)
    if cls is None:
        raise ValueError(f"Unknown ccxt exchange: {exchange_id}")
    exchange = cls({'enableRateLimit': True})

    candles: list[dict] = []
    seen_ts: set[int] = set()
    since = ts_from_ms

    try:
        await exchange.load_markets()
        while since < ts_to_ms:
            try:
                batch = await exchange.fetch_ohlcv(
                    symbol, timeframe=timeframe,
                    since=since, limit=batch_limit,
                )
            except Exception as exc:
                logger.error("fetch_ohlcv_range batch failed at since=%d: %s", since, exc)
                raise

            if not batch:
                break

            new_count = 0
            for c in batch:
                ts = c[0]
                if ts in seen_ts or ts > ts_to_ms:
                    continue
                seen_ts.add(ts)
                candles.append({
                    'timestamp': ts,
                    'open': c[1], 'high': c[2], 'low': c[3],
                    'close': c[4], 'volume': c[5],
                })
                new_count += 1

            if new_count == 0:
                # exchange returned only data we've already seen; stop
                break

            # Advance past the last received candle
            since = batch[-1][0] + tf_ms

            # If batch returned fewer than limit, we've likely reached the end
            if len(batch) < batch_limit:
                break

            await asyncio.sleep(pause_between_batches)

    finally:
        try:
            await exchange.close()
        except Exception:
            pass

    candles.sort(key=lambda c: c['timestamp'])
    return candles
