"""
signal-engine entrypoint.
Connects to DB and Redis, then runs the engine loop.
No HTTP server — this is a pure background worker.
"""
import asyncio
import logging

import redis.asyncio as aioredis

from app.config import settings
from app.database import init_db, get_pool
from app.engine import run_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("signal-engine starting")
    await init_db()
    pool = get_pool()

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await run_engine(redis_client, pool)
    finally:
        await redis_client.aclose()
        logger.info("signal-engine shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
