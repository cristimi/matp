"""
PostgreSQL connection pool via asyncpg.
"""

import logging
import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)
_pool: asyncpg.Pool | None = None


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    logger.info("Database pool initialized.")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    return _pool
