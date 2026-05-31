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
