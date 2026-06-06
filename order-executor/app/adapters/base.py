"""
Abstract Base Class for all exchange adapters.
"""

from abc import ABC, abstractmethod
from app.models import OrderRequest, OrderResult


class ExchangeAdapter(ABC):
    def __init__(self, credentials: dict, mode: str):
        """
        credentials: decrypted dict (keys vary by exchange)
        mode: "live" | "demo"
        """
        self.credentials = credentials
        self.mode = mode

    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """Submit a new order to the exchange."""
        pass

    @abstractmethod
    async def close_position(self, symbol: str, side: str) -> OrderResult:
        """Close an existing position (market close)."""
        pass

    @abstractmethod
    async def get_open_positions(self) -> list[dict]:
        """Fetch all currently open positions."""
        pass

    @abstractmethod
    async def get_balance(self) -> dict:
        """
        Return account balance information.
        Must never raise — return empty dict on error.

        Required keys in returned dict:
            total_balance:     float   (total equity in quote currency)
            available_balance: float   (available for new positions)
            used_margin:       float   (currently used as margin)
            currency:          str     (e.g. "USDT")
        """
        pass

    @abstractmethod
    async def get_account_meta(self) -> dict:
        """
        Return safe public metadata about the account.
        Must never raise — return empty dict on error.
        Must NOT return private keys or secrets.

        Blofin: { "api_key_preview": "abc...xyz" }
                  (first 3 + last 4 chars of api_key)
        Hyperliquid: { "wallet_address": "0x1234...abcd" }
                      (full address — this is public)
        """
        pass
