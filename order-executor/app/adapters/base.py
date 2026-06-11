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
    async def close_position(self, symbol: str, side: str, margin_mode: str = "isolated") -> OrderResult:
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
    async def list_instruments(self) -> list[str]:
        """
        Return all tradeable instrument symbols for this account's exchange/mode.
        Symbols must be in the canonical BASE-QUOTE format used by this adapter
        (e.g. "BTC-USDT").  Must never raise — return [] on error.
        Implementations should cache results to avoid repeated API calls.
        """
        pass

    @abstractmethod
    async def get_closed_position_details(self, symbol: str) -> dict | None:
        """
        Query the exchange for the most recent closed position for the given symbol.
        Returns a dict with keys:
            close_reason:   str   — 'Liquidated' or 'Closed on exchange'
            closing_price:  Decimal
            pnl_realized:   Decimal  (net of fees where available)
            closed_at:      datetime (tz-aware UTC)
            raw:            dict     (full exchange response for audit)
        Returns None if no closed position history is found.
        Must never raise.
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
