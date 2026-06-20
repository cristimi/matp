"""
Read closed candles from Redis streams produced by market-ingestion.
Stream key:   stream:candles:{exchange}:{symbol}:{timeframe}
Pub/sub chan:  candles:closed:{exchange}:{symbol}:{timeframe}
Fields in each entry: t, o, h, l, c, v  (strings)
"""
import logging
from typing import AsyncIterator

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)


def _stream_key(exchange: str, symbol: str, timeframe: str) -> str:
    return f"stream:candles:{exchange}:{symbol}:{timeframe}"


def _closed_channel(exchange: str, symbol: str, timeframe: str) -> str:
    return f"candles:closed:{exchange}:{symbol}:{timeframe}"


def _parse_fields(fields: dict) -> dict:
    return {
        "t": int(fields["t"]),
        "o": float(fields["o"]),
        "h": float(fields["h"]),
        "l": float(fields["l"]),
        "c": float(fields["c"]),
        "v": float(fields["v"]),
    }


async def read_stream_history(
    redis: aioredis.Redis,
    exchange: str,
    symbol: str,
    timeframe: str,
    count: int = 500,
) -> list[dict]:
    """Return up to `count` most-recent closed candles from the stream, sorted oldest-first."""
    key = _stream_key(exchange, symbol, timeframe)
    # xrevrange gives newest-first; we reverse to get chronological order
    entries = await redis.xrevrange(key, count=count)
    candles = [_parse_fields(fields) for _, fields in reversed(entries)]
    logger.info(
        "redis_reader: loaded %d historical bars %s %s %s",
        len(candles), exchange, symbol, timeframe,
    )
    return candles


async def subscribe_closed_bars(
    redis: aioredis.Redis,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> AsyncIterator[dict]:
    """Subscribe to the pub/sub channel for newly closed bars. Yields candle dicts."""
    import json
    from app.config import settings

    channel = _closed_channel(exchange, symbol, timeframe)
    # Create a dedicated connection with no socket timeout for blocking pub/sub
    pubsub_client = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=None,
        socket_connect_timeout=10,
    )
    pubsub = pubsub_client.pubsub()
    await pubsub.subscribe(channel)
    logger.info("redis_reader: subscribed to %s", channel)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
                yield {
                    "t": int(data["t"]),
                    "o": float(data["o"]),
                    "h": float(data["h"]),
                    "l": float(data["l"]),
                    "c": float(data["c"]),
                    "v": float(data["v"]),
                }
            except Exception as exc:
                logger.warning("redis_reader: failed to parse message: %s — %s", message["data"], exc)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub_client.aclose()
