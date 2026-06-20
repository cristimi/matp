import json
import logging
import time

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

STREAM_MAXLEN = 2000


def _stream_key(exchange: str, symbol: str, timeframe: str) -> str:
    return f"stream:candles:{exchange}:{symbol}:{timeframe}"


def _forming_key(exchange: str, symbol: str, timeframe: str) -> str:
    return f"candle:forming:{exchange}:{symbol}:{timeframe}"


def _closed_channel(exchange: str, symbol: str, timeframe: str) -> str:
    return f"candles:closed:{exchange}:{symbol}:{timeframe}"


def _heartbeat_key(exchange: str) -> str:
    return f"ingestion:heartbeat:{exchange}"


class RedisStore:
    def __init__(self, redis_client: aioredis.Redis, exchange: str):
        self._r = redis_client
        self.exchange = exchange

    async def add_closed_candle(self, symbol: str, timeframe: str, candle: dict) -> None:
        key = _stream_key(self.exchange, symbol, timeframe)
        fields = {
            "t": str(candle["t"]),
            "o": str(candle["o"]),
            "h": str(candle["h"]),
            "l": str(candle["l"]),
            "c": str(candle["c"]),
            "v": str(candle["v"]),
        }
        await self._r.xadd(key, fields, maxlen=STREAM_MAXLEN, approximate=True)
        channel = _closed_channel(self.exchange, symbol, timeframe)
        await self._r.publish(channel, json.dumps(candle))

    async def set_forming_candle(self, symbol: str, timeframe: str, candle: dict) -> None:
        key = _forming_key(self.exchange, symbol, timeframe)
        await self._r.set(key, json.dumps(candle))

    async def update_heartbeat(self) -> None:
        key = _heartbeat_key(self.exchange)
        await self._r.set(key, str(int(time.time() * 1000)))

    async def get_last_closed_ts(self, symbol: str, timeframe: str) -> int | None:
        """Return open-time (ms) of the last closed bar in the stream, or None."""
        key = _stream_key(self.exchange, symbol, timeframe)
        entries = await self._r.xrevrange(key, count=1)
        if not entries:
            return None
        _, fields = entries[0]
        return int(fields["t"])

    async def get_stall_until(self, exchange_id: str) -> int | None:
        """Return stall-until epoch-ms if the simulate-gap flag is active."""
        val = await self._r.get(f"ingestion:stall_until:{exchange_id}")
        return int(val) if val else None
