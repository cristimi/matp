"""
Redis client for Pub/Sub publishing.
"""

import json
import logging
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)
_redis: aioredis.Redis | None = None


async def init_redis():
    global _redis
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    logger.info("Redis client initialized.")


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis client not initialized")
    return _redis


async def publish(channel: str, data: dict):
    try:
        await get_redis().publish(channel, json.dumps(data))
        logger.info(f"Published to Redis channel {channel}: {data['event']}")
    except Exception as e:
        logger.warning(f"Redis publish failed on channel {channel}: {e}")


async def cache_get(key: str) -> dict | None:
    try:
        raw = await get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning(f"Redis cache_get failed for {key}: {e}")
        return None


async def cache_set(key: str, data: dict, ttl: int = 5) -> None:
    try:
        await get_redis().setex(key, ttl, json.dumps(data, default=str))
    except Exception as e:
        logger.warning(f"Redis cache_set failed for {key}: {e}")


async def cache_delete(key: str) -> None:
    try:
        await get_redis().delete(key)
    except Exception as e:
        logger.warning(f"Redis cache_delete failed for {key}: {e}")
