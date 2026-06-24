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


async def resolve_exchange_id(conn, account_id: str) -> str:
    """Return the CCXT exchange id for the given account_id.

    Raises ValueError if account_id is missing, has no exchange_accounts row,
    or the exchange column is null/empty. Never silently defaults.
    """
    if not account_id:
        raise ValueError("resolve_exchange_id: account_id is missing or empty")
    row = await conn.fetchrow(
        "SELECT exchange FROM exchange_accounts WHERE id = $1",
        account_id,
    )
    if row is None:
        raise ValueError(
            f"resolve_exchange_id: no exchange_accounts row for account_id={account_id!r}"
        )
    exchange = row["exchange"]
    if not exchange:
        raise ValueError(
            f"resolve_exchange_id: exchange is null/empty for account_id={account_id!r}"
        )
    return exchange
