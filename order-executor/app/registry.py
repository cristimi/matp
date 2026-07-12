"""
AccountRegistry: lazy-loaded, in-memory adapter instance cache.

One ExchangeAdapter instance is created per account_id on first use.
Credentials are decrypted once and held in memory for the lifetime
of the process. Call invalidate() after credential rotation.
"""
import json
import logging
from app.adapters.base import ExchangeAdapter
from app.database import fetch_account
from app.credentials import decrypt

logger = logging.getLogger(__name__)


class AccountRegistry:

    def __init__(self):
        self._instances: dict[str, ExchangeAdapter] = {}

    async def get(self, account_id: str) -> ExchangeAdapter:
        """
        Return a live adapter instance for the given account_id.
        Loads and caches on first call; returns cached on subsequent calls.
        """
        if account_id not in self._instances:
            logger.info(f"Loading adapter for account: {account_id}")
            self._instances[account_id] = await self._load(account_id)
        return self._instances[account_id]

    async def _load(self, account_id: str) -> ExchangeAdapter:
        record = await fetch_account(account_id)
        if not record:
            raise ValueError(f"Account not found or inactive: {account_id}")

        # asyncpg returns bytea as a memoryview — convert to bytes first
        cred_bytes = bytes(record["credentials"])
        cred_str = decrypt(cred_bytes)
        credentials = json.loads(cred_str)

        exchange = record["exchange"]
        mode = record["mode"]

        if exchange == "blofin":
            from app.adapters.blofin import BlofinAdapter
            return BlofinAdapter(credentials, mode)

        elif exchange == "hyperliquid":
            from app.adapters.hyperliquid import HyperliquidAdapter
            return HyperliquidAdapter(credentials, mode)

        else:
            raise ValueError(
                f"Unknown exchange '{exchange}' for account {account_id}. "
                f"Supported: blofin, hyperliquid"
            )

    async def invalidate(self, account_id: str):
        """
        Evict the cached adapter instance for an account, closing its pooled HTTP
        client so the connection isn't leaked. Call this after credentials are
        rotated via the Dashboard.
        """
        removed = self._instances.pop(account_id, None)
        if removed:
            await removed.close()
            logger.info(f"Evicted cached adapter for account: {account_id}")
        else:
            logger.debug(f"invalidate() called for uncached account: {account_id}")

    async def close_all(self):
        """Close every cached adapter's pooled client. Call on app shutdown."""
        for adapter in self._instances.values():
            await adapter.close()
        self._instances.clear()


# Module-level singleton — imported by executor.py and main.py
registry = AccountRegistry()
