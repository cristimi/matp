"""
Hyperliquid perpetuals adapter.
Uses EIP-712 structured data signing via eth-account.

Expected credentials dict:
    { "private_key": "0x..." }

The wallet address is derived from the private key — it does not need
to be stored separately.
"""
import asyncio
import json
import time
import logging
import hashlib
from decimal import Decimal
from typing import Optional

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data

from app.adapters.base import ExchangeAdapter
from app.models import OrderRequest, OrderResult

logger = logging.getLogger(__name__)


# ── EIP-712 domain for Hyperliquid ───────────────────────────────────
# VERIFY: These are the known Hyperliquid mainnet domain parameters.
# If orders are rejected with signature errors, confirm at:
# https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/signing
_HL_DOMAIN = {
    "name": "Exchange",
    "version": "1",
    "chainId": 1337,
    "verifyingContract": "0x0000000000000000000000000000000000000000",
}

_HL_TYPES = {
    "Agent": [
        {"name": "source", "type": "string"},
        {"name": "connectionId", "type": "bytes32"},
    ]
}


class HyperliquidAdapter(ExchangeAdapter):

    def __init__(self, credentials: dict, mode: str):
        super().__init__(credentials, mode)
        private_key = credentials.get("private_key", "")
        if not private_key:
            raise ValueError("Hyperliquid credentials must include 'private_key'")

        self._account = Account.from_key(private_key)
        self.wallet_address = self._account.address  # signs orders (API/agent wallet)

        # If credentials include a main_wallet, use it for state queries
        # (agent wallets place orders on behalf of a main wallet; positions/balance
        # live under the main wallet's clearinghouse state, not the agent's)
        main_wallet = credentials.get("main_wallet", "").strip()
        self.query_address = main_wallet if main_wallet else self.wallet_address

        self.base_url = (
            "https://api.hyperliquid-testnet.xyz"
            if mode == "demo"
            else "https://api.hyperliquid.xyz"
        )
        self._asset_cache: Optional[dict] = None
        logger.info(
            f"HyperliquidAdapter initialised "
            f"(wallet={self.wallet_address[:8]}..., "
            f"query={self.query_address[:8]}..., mode={mode})"
        )

    # ── Public interface ─────────────────────────────────────────────

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        try:
            asset_index = await self._get_asset_index(order.symbol)
            is_close = order.signal in ("close_long", "close_short")
            result = await self._place_order(order, asset_index, reduce_only=is_close)
            return result
        except Exception as e:
            logger.error(f"HyperliquidAdapter.submit_order failed: {e}")
            return OrderResult(
                success=False,
                status="route_failed",
                error_msg=str(e),
            )

    async def close_position(self, symbol: str, side: str, margin_mode: str = "isolated") -> OrderResult:
        try:
            positions = await self.get_open_positions()
            target = next(
                (p for p in positions
                 if p.symbol == symbol and p.side == side),
                None
            )
            if not target:
                return OrderResult(
                    success=False, status="route_failed",
                    error_msg=f"No open {side} position found for {symbol}"
                )
            # Build a reduce-only order to close
            close_side = "sell" if side == "long" else "buy"
            asset_index = await self._get_asset_index(symbol)
            close_order = OrderRequest(
                order_id="close-" + str(int(time.time() * 1000)),
                account_id="",
                symbol=symbol,
                side=close_side,
                signal="close",
                order_type="market",
                size=target.size,
                leverage=None,
                margin_mode=None,
            )
            return await self._place_order(close_order, asset_index,
                                           reduce_only=True)
        except Exception as e:
            logger.error(f"HyperliquidAdapter.close_position failed: {e}")
            return OrderResult(
                success=False, status="route_failed", error_msg=str(e)
            )

    async def get_open_positions(self) -> list:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/info",
                    json={"type": "clearinghouseState", "user": self.query_address},
                )
                resp.raise_for_status()
                data = resp.json()

            from app.models import Position
            from decimal import Decimal as D
            positions = []
            for pos in data.get("assetPositions", []):
                p = pos.get("position", {})
                szi = float(p.get("szi", 0))
                if szi == 0:
                    continue
                size = abs(szi)
                pos_value = float(p.get("positionValue", 0))
                mark_px = (pos_value / size) if size > 0 else None
                positions.append(Position(
                    symbol=p.get("coin", "") + "-USDT",
                    side="long" if szi > 0 else "short",
                    size=D(str(size)),
                    entry_price=D(str(p.get("entryPx", 0))),
                    leverage=p.get("leverage", {}).get("value", 1),
                    mark_price=D(str(round(mark_px, 6))) if mark_px else None,
                    unrealized_pnl=D(str(p.get("unrealizedPnl", 0))),
                ))
            return positions
        except Exception as e:
            logger.error(f"HyperliquidAdapter.get_open_positions failed: {e}")
            return []

    # ── Private helpers ──────────────────────────────────────────────

    def _round_price(self, price: float) -> float:
        """Round price to Hyperliquid's 5 significant figures rule."""
        if price == 0:
            return 0
        from math import log10, floor
        # Calculate number of decimals to keep to have 5 significant figures
        decimals = 4 - int(floor(log10(abs(price))))
        # Ensure we don't use negative decimals unless we really have to
        # (Hyperliquid accepts integers)
        return round(price, max(0, decimals))

    def _float_to_wire(self, x: float) -> str:
        """Hyperliquid-style float to string: 8 decimals, no trailing zeros."""
        if x == 0:
            return "0"
        return "{:.8f}".format(x).rstrip("0").rstrip(".")

    async def _get_asset_index(self, symbol: str) -> int:
        """
        Look up the integer asset index for a symbol.
        Hyperliquid uses 'BTC' not 'BTC-USDT' — strip the '-USDT' suffix.
        Caches the full asset list for the lifetime of the adapter instance.
        """
        coin = symbol.replace("-USDT", "").replace("-USD", "").upper()

        if self._asset_cache is None:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/info",
                    json={"type": "meta"},
                )
                resp.raise_for_status()
                meta = resp.json()
            self._asset_cache = {
                asset["name"]: idx
                for idx, asset in enumerate(meta.get("universe", []))
            }
            
            logger.info(
                f"Loaded {len(self._asset_cache)} assets from Hyperliquid"
            )

        if coin not in self._asset_cache:
            # Refresh cache once in case asset was recently added
            self._asset_cache = None
            raise ValueError(
                f"Asset '{coin}' not found in Hyperliquid universe. "
                f"Check symbol format (expected e.g. 'BTC', not 'BTC-USDT')."
            )

        return self._asset_cache[coin]

    async def _place_order(
        self,
        order: OrderRequest,
        asset_index: int,
        reduce_only: bool = False,
    ) -> OrderResult:
        """
        Build, sign, and submit an order to Hyperliquid.
        """
        is_buy = order.side == "buy"
        nonce = int(time.time() * 1000)

        # Market orders use a slippage-based price cap with Ioc time-in-force
        # Limit orders use the specified price
        if order.order_type == "market":
            # Fetch mark price for slippage calculation
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/info",
                    json={"type": "metaAndAssetCtxs"},
                )
                resp.raise_for_status()
                meta_and_ctx = resp.json()
            
            # The response is [meta, [asset_ctx1, asset_ctx2, ...]]
            asset_ctxs = meta_and_ctx[1]
            if asset_index >= len(asset_ctxs):
                raise ValueError(f"Asset index {asset_index} out of bounds for context list")
            
            ctx = asset_ctxs[asset_index]
            mark_px = float(ctx.get("markPx", 0))
            if mark_px == 0:
                raise ValueError(f"Could not fetch mark price for asset index {asset_index}")
            
            # Apply configurable slippage buffer (default 1%)
            slippage = (order.config or {}).get("slippage_pct", 1.0) / 100
            if is_buy:
                price_val = mark_px * (1 + slippage)
            else:
                price_val = mark_px * (1 - slippage)
            
            price_str = str(price_val)
                
            order_type_payload = {"limit": {"tif": "Ioc"}}
        else:
            if order.price is None:
                raise ValueError("Limit orders require a price")
            price_str = str(order.price)
            order_type_payload = {"limit": {"tif": "Gtc"}}

        # ── Build action dict with SDK-compliant insertion order
        # orders order: a, b, p, s, r, t
        # action order: type, orders, grouping
        price_wire = self._float_to_wire(self._round_price(float(price_str)))
        size_wire  = self._float_to_wire(float(order.size))

        action = {
            "type": "order",
            "orders": [{
                "a": asset_index,
                "b": is_buy,
                "p": price_wire,
                "s": size_wire,
                "r": reduce_only,
                "t": order_type_payload,
            }],
            "grouping": "na",
        }

        # ── EIP-712 signing
        # connection_id = keccak256(msgpack_bytes + nonce_bytes + vault_bytes)
        # Vault bytes is b'\x00' for no vault.
        import msgpack
        from eth_hash.auto import keccak
        action_bytes  = msgpack.packb(action, use_bin_type=True)
        nonce_bytes   = nonce.to_bytes(8, "big")
        connection_id = keccak(action_bytes + nonce_bytes + b'\x00')

        # connection_id from keccak is already exactly 32 bytes
        connection_id_bytes32 = connection_id

        source = "b" if self.base_url.endswith("testnet.xyz") else "a"
        message = {
            "source":       source,
            "connectionId": connection_id_bytes32,
        }

        signed = self._account.sign_typed_data(
            domain_data=_HL_DOMAIN,
            message_types=_HL_TYPES,
            message_data=message,
        )

        payload = {
            "action":       action,
            "nonce":        nonce,
            "signature": {
                "r": hex(signed.r),
                "s": hex(signed.s),
                "v": signed.v,
            },
            "vaultAddress": None,
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.base_url}/exchange",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        # ── Parse response
        logger.debug(f"Hyperliquid response: {data}")

        # The API sometimes returns `response` as a plain string
        # (e.g. "Insufficient margin") instead of the expected
        # nested dict. Guard against this before calling .get().
        response_field = data.get("response", {})
        if isinstance(response_field, str):
            return OrderResult(
                success=False,
                status="rejected",
                error_msg=f"Hyperliquid rejected order: {response_field}",
                raw_response=data,
            )

        status_data = response_field.get("data", {})
        statuses    = status_data.get("statuses", [{}]) if isinstance(status_data, dict) else [{}]
        first       = statuses[0] if statuses else {}

        if "error" in first:
            return OrderResult(
                success=False,
                status="rejected",
                error_msg=first["error"],
                raw_response=data,
            )

        filled = first.get("filled", {})
        oid    = str(filled.get("oid", "")) or \
                 str(first.get("resting", {}).get("oid", ""))

        avg_px = filled.get("avgPx")

        realized_pnl = None
        if reduce_only and oid:
            realized_pnl = await self._get_fill_pnl(int(oid))

        return OrderResult(
            success=True,
            status="filled",
            exchange_order_id=oid or None,
            actual_fill_price=Decimal(str(avg_px)) if avg_px else None,
            realized_pnl=realized_pnl,
            raw_response=data,
        )

    async def _get_fill_pnl(self, oid: int) -> Optional[Decimal]:
        """Query userFills and return sum of closedPnl for the given order id.
        Returns None only if no fills are found for the oid (unknown outcome).
        Returns Decimal('0') if the order generated no closed PnL (e.g. open)."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/info",
                    json={"type": "userFills", "user": self.query_address},
                )
                resp.raise_for_status()
            fills = resp.json()
            matching = [f for f in fills if f.get("oid") == oid]
            if not matching:
                logger.warning(f"No fills found for oid {oid} in userFills")
                return None
            return sum(Decimal(str(f.get("closedPnl", "0"))) for f in matching)
        except Exception as e:
            logger.warning(f"HyperliquidAdapter._get_fill_pnl failed for oid {oid}: {e}")
            return None

    async def get_balance(self) -> dict:
        """Fetch balance from Hyperliquid.

        Unified Account Mode (common on testnet faucet accounts) holds all
        funds in spotClearinghouseState; clearinghouseState returns 0 in that
        case. We query both and sum so either mode works correctly.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                perp_resp, spot_resp = await asyncio.gather(
                    client.post(
                        f"{self.base_url}/info",
                        json={"type": "clearinghouseState", "user": self.query_address},
                    ),
                    client.post(
                        f"{self.base_url}/info",
                        json={"type": "spotClearinghouseState", "user": self.query_address},
                    ),
                )
                perp_resp.raise_for_status()
                spot_resp.raise_for_status()
                perp_data = perp_resp.json()
                spot_data = spot_resp.json()

            # Perp / clearinghouse balance
            margin_summary = perp_data.get("marginSummary", {})
            perp_total = float(margin_summary.get("accountValue",    0))
            perp_avail = float(margin_summary.get("withdrawable",    0))
            perp_used  = float(margin_summary.get("totalMarginUsed", 0))

            # Spot / unified balance — find USDC entry (token index 0)
            spot_total = 0.0
            spot_avail = 0.0
            for entry in spot_data.get("balances", []):
                if entry.get("coin") == "USDC" or entry.get("token") == 0:
                    t = float(entry.get("total", 0))
                    h = float(entry.get("hold",  0))
                    spot_total = t
                    spot_avail = t - h
                    break

            return {
                "total_balance":     perp_total + spot_total,
                "available_balance": perp_avail + spot_avail,
                "used_margin":       perp_used,
                "currency":          "USDC",
            }
        except Exception as e:
            logger.error(f"HyperliquidAdapter.get_balance failed: {e}")
            return {
                "total_balance":     0.0,
                "available_balance": 0.0,
                "used_margin":       0.0,
                "currency":          "USDC",
                "error":             str(e),
            }

    async def get_account_meta(self) -> dict:
        """Return wallet addresses — both are public information."""
        try:
            meta = {
                "wallet_address": self.wallet_address,   # API/agent wallet (signs orders)
                "exchange":       "hyperliquid",
            }
            if self.query_address != self.wallet_address:
                meta["main_wallet"] = self.query_address
            return meta
        except Exception as e:
            logger.error(f"HyperliquidAdapter.get_account_meta failed: {e}")
            return {}
