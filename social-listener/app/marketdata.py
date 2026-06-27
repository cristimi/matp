import json
import logging

import redis.asyncio as aioredis

from app.config import settings

log = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


def _client() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def get_mark(asset: str) -> float | None:
    """Return the latest mark price for asset from the 1m forming candle, or None."""
    symbol = f"{asset.upper()}-USDT"
    key = f"candle:forming:{settings.ingestion_exchange}:{symbol}:1m"
    try:
        raw = await _client().get(key)
        if not raw:
            return None
        return float(json.loads(raw)["c"])
    except Exception as e:  # noqa: BLE001
        log.warning("get_mark(%s) key=%s failed: %s", asset, key, e)
        return None
