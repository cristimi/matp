"""
Hyperliquid exchange adapter.
Authenticates with ECDSA private key (standard Hyperliquid auth).
"""

import logging
import time
from typing import Dict, List, Optional # Added Dict and Optional
import httpx

from eth_account import Account
# from eth_account.messages import encode_defunct # Not used directly in place_order if using HL SDK or specific L1 action structures
import json # Used for raw_response, also needed for _get_private_key's Account.from_key if it expects a JSON key file

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
    _asset_indices: Dict[str, int] = {} # Class-level cache for asset indices

    def __init__(self):
        # It's good practice to ensure the private key is available early
        if not settings.hyperliquid_private_key:
            logger.warning("Hyperliquid private key is not set. Hyperliquid trading will not function.")
            # Depending on strictness, might raise an error or just log a warning

    # Removed _get_private_key as it's not directly used for signing here yet,
    # and Account.from_key might be called directly where needed with proper error handling.

    async def _fetch_asset_meta(self):
        """Fetches asset metadata from Hyperliquid and populates the cache."""
        try:
            async with httpx.AsyncClient(base_url=HL_BASE_URL, timeout=10) as client:
                response = await client.post("/info", json={"type": "meta"})
                response.raise_for_status() # Raise an exception for bad status codes
                data = response.json()
                
                # The 'universe' key contains a list of asset objects
                if "universe" in data:
                    for asset_info in data["universe"]:
                        # Assuming 'name' is like "BTC" and 'i' is the asset index
                        if "name" in asset_info and "i" in asset_info:
                            # Map full symbol "BTC-USDT" to its asset index "i"
                            # This assumes a standard "BASE-USDT" naming convention for now
                            self._asset_indices[f"{asset_info['name']}-USDT"] = asset_info["i"]
                            logger.info(f"Cached Hyperliquid asset: {asset_info['name']}-USDT -> index {asset_info['i']}")
                else:
                    logger.error("Hyperliquid /info meta response missing 'universe' key.")
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching Hyperliquid asset meta: {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            logger.error(f"Network error fetching Hyperliquid asset meta: {e}")
        except Exception as e:
            logger.error(f"Unexpected error fetching Hyperliquid asset meta: {e}")

    async def _get_asset_index(self, symbol: str) -> Optional[int]:
        """Retrieves asset index for a given symbol, fetching and caching if necessary."""
        # If cache is empty, try to fetch
        if not self._asset_indices:
            await self._fetch_asset_meta()
            # If still empty after fetch, it means fetching failed
            if not self._asset_indices:
                logger.error("Failed to populate Hyperliquid asset metadata cache.")
                return None
        
        # Return from cache
        return self._asset_indices.get(symbol)

    async def place_order(self, signal: WebhookPayload, strategy: dict) -> OrderResult:
        logger.warning("Hyperliquid place_order not yet implemented")
        return OrderResult(success=False, status="rejected", error_msg="Hyperliquid adapter incomplete")

    async def get_open_positions(self) -> List[dict]:
        if not settings.hyperliquid_private_key:
            return []
        
        try:
            account = _get_private_key()
            address = account.address
            
            async with httpx.AsyncClient(base_url=HL_BASE_URL, timeout=10) as client:
                response = await client.post("/info", json={
                    "type": "clearinghouseState",
                    "user": address
                })
                response.raise_for_status()
                data = response.json()
                
                # HL returns positions in assetPositions
                raw_positions = data.get("assetPositions", [])
                mapped_positions = []
                
                for p_wrap in raw_positions:
                    p = p_wrap.get("position", {})
                    size = float(p.get("s", "0"))
                    if size == 0:
                        continue
                        
                    mapped_positions.append({
                        "symbol": f"{p.get('coin')}-USDT",
                        "side": "buy" if size > 0 else "sell",
                        "size": str(abs(size)),
                        "entryPx": p.get("entryPx"),
                        "markPx": p.get("markPx") or "0", 
                        "unrealizedPnl": p.get("unrealizedPnl"),
                        "liquidationPx": p.get("liquidationPx"),
                        "platform": "hyperliquid"
                    })
                return mapped_positions
        except Exception as e:
            logger.error(f"Failed to fetch HL positions: {e}")
            return []

    async def close_position(self, symbol: str, side: str) -> OrderResult:
        logger.warning("Hyperliquid close_position not yet implemented")
        return OrderResult(success=False, status="rejected", error_msg="Hyperliquid adapter incomplete")
