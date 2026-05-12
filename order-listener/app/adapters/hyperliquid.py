"""
Hyperliquid exchange adapter.
Authenticates with ECDSA private key (standard Hyperliquid auth).
"""

import logging
import time
from typing import List

import httpx

from app.adapters.base import ExchangeAdapter
from app.config import settings
from app.models import WebhookPayload, OrderResult

logger = logging.getLogger(__name__)

HL_BASE_URL = "https://api.hyperliquid.xyz"


def _get_private_key():
    """Load ECDSA private key from settings."""
    from eth_account import Account
    return Account.from_key(settings.hyperliquid_private_key)


class HyperliquidAdapter(ExchangeAdapter):

    async def place_order(self, signal: WebhookPayload) -> OrderResult:
        """
        Place a perp order on Hyperliquid.
        Uses the Hyperliquid L1 action signing flow.
        """
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct
            import json

            account = _get_private_key()

            # Map symbol: "BTC-USDT" → "BTC"
            coin = signal.symbol.split("-")[0]
            is_buy = signal.side == "buy"

            # Hyperliquid order action structure
            timestamp = int(time.time() * 1000)
            order_action = {
                "type": "order",
                "orders": [
                    {
                        "a": 0,  # asset index — must be looked up from HL meta
                        "b": is_buy,
                        "p": str(signal.price or "0"),
                        "s": str(signal.size),
                        "r": signal.orderType != "market",  # reduce-only
                        "t": {
                            "limit": {"tif": "Gtc"}
                        } if signal.orderType == "limit" else {
                            "market": {}
                        },
                    }
                ],
                "grouping": "na",
            }

            # NOTE: Full HL integration requires fetching the asset index from
            # GET /info (type=meta) and using the Hyperliquid SDK for proper
            # signing. This scaffold shows the pattern — complete in Phase 3.
            logger.warning(
                "Hyperliquid adapter is a scaffold — complete ECDSA signing in Phase 3"
            )
            return OrderResult(
                success=False,
                status="rejected",
                error_msg="Hyperliquid adapter not yet fully implemented (Phase 3)",
            )

        except Exception as e:
            logger.exception(f"HyperliquidAdapter.place_order error: {e}")
            return OrderResult(success=False, status="route_failed", error_msg=str(e))

    async def get_open_positions(self) -> List[dict]:
        account = _get_private_key()
        async with httpx.AsyncClient(base_url=HL_BASE_URL, timeout=10) as client:
            response = await client.post(
                "/info",
                json={"type": "clearinghouseState", "user": account.address},
            )
        data = response.json()
        return data.get("assetPositions", [])

    async def close_position(self, symbol: str, side: str) -> OrderResult:
        # Placeholder — implement in Phase 3
        return OrderResult(
            success=False,
            status="rejected",
            error_msg="Hyperliquid close position not yet implemented",
        )
