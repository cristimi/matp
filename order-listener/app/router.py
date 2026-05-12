"""
Routing logic: determines which exchange adapter to use based on webhook platform field
and the system active_platform config (cached in Redis).
"""

import logging

from app.database import get_pool
from app.redis_client import get_redis
from app.models import WebhookPayload, OrderResult
from app.adapters.blofin import BlofinAdapter
from app.adapters.hyperliquid import HyperliquidAdapter

logger = logging.getLogger(__name__)

ADAPTERS = {
    "blofin": BlofinAdapter,
    "hyperliquid": HyperliquidAdapter,
}

_CACHE_KEY = "config:active_platform"
_CACHE_TTL = 5  # seconds


async def _get_active_platform() -> str:
    """Read active platform from Redis cache (5s TTL) or PostgreSQL."""
    redis = get_redis()
    cached = await redis.get(_CACHE_KEY)
    if cached:
        return cached

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM config WHERE key = 'active_platform'"
        )
        platform = row["value"] if row else "blofin"

    await redis.setex(_CACHE_KEY, _CACHE_TTL, platform)
    return platform


async def route_order(payload: WebhookPayload) -> OrderResult:
    """Select adapter and dispatch order."""
    platform = payload.platform

    if platform == "auto":
        platform = await _get_active_platform()

    adapter_class = ADAPTERS.get(platform)
    if adapter_class is None:
        logger.error(f"Unknown platform '{platform}' — no adapter found")
        return OrderResult(
            success=False,
            status="rejected",
            error_msg=f"Unknown platform: {platform}",
        )

    adapter = adapter_class()
    logger.info(f"Routing {payload.symbol} {payload.signal} → {platform}")

    try:
        result = await adapter.place_order(payload)
        return result
    except Exception as e:
        logger.exception(f"Adapter {platform} raised: {e}")
        return OrderResult(success=False, status="route_failed", error_msg=str(e))
