"""
Database connection pool and account retrieval.
"""

import os
import asyncpg
from typing import Optional

_pool: Optional[asyncpg.Pool] = None


async def init_db():
    """Initialize the database connection pool."""
    global _pool
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    
    _pool = await asyncpg.create_pool(
        dsn,
        min_size=2,
        max_size=10
    )


def get_pool() -> asyncpg.Pool:
    """Get the active database connection pool."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_db() first.")
    return _pool


async def fetch_account(account_id: str):
    """
    Fetch account details from the database.
    Returns the raw asyncpg Record.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, exchange, mode, label, credentials, is_active
            FROM exchange_accounts
            WHERE id = $1
            """,
            account_id
        )
        if row is None:
            raise ValueError(f"Account not found: {account_id}")
        if not row["is_active"]:
            raise ValueError(f"Account is inactive: {account_id}")
        return row
