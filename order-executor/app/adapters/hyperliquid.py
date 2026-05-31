"""
Hyperliquid exchange adapter.
STUB — full ECDSA signing implementation in Session 7.
"""
import logging
from app.adapters.base import ExchangeAdapter
from app.models import OrderRequest, OrderResult, Position

logger = logging.getLogger(__name__)


class HyperliquidAdapter(ExchangeAdapter):
    """
    Hyperliquid perpetuals adapter.
    Expected credentials dict: { "private_key": "0x..." }
    """

    def __init__(self, credentials: dict, mode: str):
        super().__init__(credentials, mode)
        self.private_key = credentials.get("private_key", "")
        self.base_url = (
            "https://api.hyperliquid-testnet.xyz"
            if mode == "demo"
            else "https://api.hyperliquid.xyz"
        )
        logger.info(f"HyperliquidAdapter initialised (mode={mode}, stub)")

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        logger.warning("HyperliquidAdapter.submit_order: not yet implemented")
        return OrderResult(
            success=False,
            status="route_failed",
            error_msg="Hyperliquid adapter not yet implemented — Session 7",
        )

    async def close_position(self, symbol: str, side: str) -> OrderResult:
        return OrderResult(
            success=False, status="route_failed",
            error_msg="Hyperliquid adapter not yet implemented",
        )

    async def get_open_positions(self) -> list[Position]:
        return []
