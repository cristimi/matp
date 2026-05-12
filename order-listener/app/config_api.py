"""
Config API — read/set active platform and other system config.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone

from app.database import get_pool
from app.redis_client import get_redis

router = APIRouter(prefix="/config")

ALLOWED_PLATFORMS = {"blofin", "hyperliquid"}


class PlatformUpdate(BaseModel):
    platform: str


@router.get("")
async def get_config():
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value, updated_at FROM config")
    return {r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows}


@router.put("/active_platform")
async def set_active_platform(body: PlatformUpdate):
    if body.platform not in ALLOWED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid platform. Must be one of: {ALLOWED_PLATFORMS}",
        )

    pool = get_pool()
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO config (key, value, updated_at) VALUES ('active_platform', $1, $2)
               ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = $2""",
            body.platform, now,
        )

    # Invalidate Redis cache
    await get_redis().delete("config:active_platform")

    return {"active_platform": body.platform, "updated_at": now.isoformat()}
