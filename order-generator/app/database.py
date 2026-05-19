"""
Database connection pool.
"""

import os
import asyncpg
import logging

logger = logging.getLogger(__name__)

_pool = None

async def get_pool():
    """Returns the global database pool, initializing it if necessary."""
    global _pool
    if _pool is None:
        try:
            db_url = os.getenv("DATABASE_URL", "postgresql://matp:changeme@postgres:5432/matp")
            _pool = await asyncpg.create_pool(db_url)
            logger.info("Database pool initialized.")
        except Exception as e:
            logger.error(f"Failed to create database pool: {e}")
            raise e
    return _pool

async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
