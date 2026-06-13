"""
PostgreSQL connection pool via asyncpg.
Every connection gets SET search_path = tester, public via the init hook.
"""

import logging

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)
_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.execute("SET search_path = tester, public")


async def init_db() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        init=_init_conn,
        min_size=2,
        max_size=10,
    )
    logger.info("Database pool initialized (search_path=tester,public)")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    return _pool
