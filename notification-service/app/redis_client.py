"""
Redis client: the notification event stream (producer + consumer-group helpers).
"""

import json
import logging
import time

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from app.config import settings

logger = logging.getLogger(__name__)
_redis: aioredis.Redis | None = None


async def init_redis():
    global _redis
    # socket_timeout=None: the consumer loop issues XREADGROUP with BLOCK 5000 (see
    # read_group below) — a finite client-side socket_timeout races the server-side
    # block window and raises a spurious redis.exceptions.TimeoutError on every idle
    # poll. socket_connect_timeout still bounds how long a dead server takes to fail.
    _redis = aioredis.from_url(
        settings.redis_url, decode_responses=True,
        socket_timeout=None, socket_connect_timeout=5,
    )
    logger.info("Redis client initialized.")


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis client not initialized")
    return _redis


async def ensure_group() -> None:
    """Create the consumer group (and stream, via MKSTREAM) if it doesn't exist yet."""
    try:
        await get_redis().xgroup_create(
            settings.stream_key, settings.consumer_group, id="0", mkstream=True
        )
        logger.info(
            "Created consumer group %s on stream %s",
            settings.consumer_group, settings.stream_key,
        )
    except ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.info("Consumer group %s already exists", settings.consumer_group)
        else:
            raise


async def emit_event(event: str, payload: dict) -> str:
    """xadd an event onto the notification stream. Returns the new entry id."""
    data = {"event": event, **payload}
    entry_id = await get_redis().xadd(settings.stream_key, {"data": json.dumps(data, default=str)})
    logger.info("Emitted %s -> %s", event, entry_id)
    return entry_id


async def read_pending(count: int = 10):
    """Non-blocking read of entries already delivered to this consumer but never
    acked (e.g. the process crashed mid-processing before a previous run). Must be
    drained before switching to '>' or a crash-time entry is stuck forever."""
    resp = await get_redis().xreadgroup(
        groupname=settings.consumer_group,
        consumername=settings.consumer_name,
        streams={settings.stream_key: "0"},
        count=count,
    )
    if not resp:
        return []
    _, entries = resp[0]
    return entries


async def read_group(count: int = 10, block_ms: int = 5000):
    """Blocking read of new entries for this consumer within the group."""
    resp = await get_redis().xreadgroup(
        groupname=settings.consumer_group,
        consumername=settings.consumer_name,
        streams={settings.stream_key: ">"},
        count=count,
        block=block_ms,
    )
    if not resp:
        return []
    # resp: [(stream_key, [(entry_id, {field: value}), ...])]
    _, entries = resp[0]
    return entries


async def ack(entry_id: str) -> None:
    await get_redis().xack(settings.stream_key, settings.consumer_group, entry_id)


async def heartbeat_value(exchange: str) -> int | None:
    val = await get_redis().get(f"ingestion:heartbeat:{exchange}")
    return int(val) if val else None


def now_ms() -> int:
    return int(time.time() * 1000)
