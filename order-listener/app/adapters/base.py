"""
Abstract base class for exchange adapters.
"""

from abc import ABC, abstractmethod
from typing import List

from app.models import WebhookPayload, OrderResult


class ExchangeAdapter(ABC):

    @abstractmethod
    async def place_order(self, signal: WebhookPayload) -> OrderResult:
        """Place an order on the exchange."""
        ...

    @abstractmethod
    async def get_open_positions(self) -> List[dict]:
        """Return list of open positions."""
        ...

    @abstractmethod
    async def close_position(self, symbol: str, side: str) -> OrderResult:
        """Close an open position."""
        ...
