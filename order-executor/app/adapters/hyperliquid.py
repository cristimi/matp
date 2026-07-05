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

from app.adapters.base import ExchangeAdapter, ExchangeUnavailableError, MMR_CONSERVATISM_BUFFER
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
        self._sz_decimals_cache: Optional[dict] = None  # coin → sz_decimals int
        self._max_lev_cache: Optional[dict] = None      # coin → exchange max leverage (int)
        self._margin_table_id_cache: Optional[dict] = None  # coin → marginTableId (int)
        self._margin_tables_cache: dict = {}                # marginTableId → marginTiers list
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

            if not is_close:
                req_lev = int(order.leverage or 1)
                max_lev = await self.get_max_leverage(order.symbol)
                if max_lev and req_lev > max_lev:
                    # DECISION: reject (do not clamp)
                    msg = (f"Requested leverage {req_lev}x exceeds Hyperliquid "
                           f"max {max_lev}x for {order.symbol}")
                    logger.warning(f"HyperliquidAdapter: {msg}")
                    return OrderResult(success=False, status="rejected", error_msg=msg)
                # Push leverage to HL (persistent per-coin setting). Abort on failure.
                await self._update_leverage(asset_index, req_lev, order.margin_mode or "isolated")

            result = await self._place_order(order, asset_index, reduce_only=is_close)
            return result
        except Exception as e:
            logger.error(f"HyperliquidAdapter.submit_order failed: {e}")
            return OrderResult(success=False, status="route_failed", error_msg=str(e))

    async def close_position(self, symbol: str, side: str, size=None, margin_mode: str = "isolated") -> OrderResult:
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
            close_size = min(Decimal(str(size)), target.size) if size is not None else target.size
            close_side = "sell" if side == "long" else "buy"
            asset_index = await self._get_asset_index(symbol)
            close_order = OrderRequest(
                order_id="close-" + str(int(time.time() * 1000)),
                account_id="",
                symbol=symbol,
                side=close_side,
                signal="close",
                order_type="market",
                size=close_size,
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
                liq_px = p.get("liquidationPx")
                positions.append(Position(
                    symbol=p.get("coin", "") + "-USDT",
                    side="long" if szi > 0 else "short",
                    size=D(str(size)),
                    entry_price=D(str(p.get("entryPx", 0))),
                    leverage=p.get("leverage", {}).get("value", 1),
                    mark_price=D(str(round(mark_px, 6))) if mark_px else None,
                    unrealized_pnl=D(str(p.get("unrealizedPnl", 0))),
                    liquidation_price=D(str(liq_px)) if liq_px else None,
                ))
            return positions
        except ExchangeUnavailableError:
            raise
        except Exception as e:
            logger.error(f"HyperliquidAdapter.get_open_positions failed: {e}")
            raise ExchangeUnavailableError(f"hyperliquid positions fetch failed: {e}") from e

    async def get_max_leverage(self, symbol: str) -> int:
        """Exchange max leverage for the coin. Returns 0 if unknown."""
        try:
            await self._get_asset_index(symbol)  # ensures caches populated
            coin = symbol.replace("-USDT", "").replace("-USD", "").upper()
            return int((self._max_lev_cache or {}).get(coin, 0) or 0)
        except Exception as e:
            logger.warning(f"HyperliquidAdapter.get_max_leverage({symbol}) failed: {e}")
            return 0

    async def get_maintenance_margin_rate(
        self, symbol: str, notional: float, margin_mode: str = "isolated"
    ) -> Optional[float]:
        """
        Derive the maintenance-margin rate for `symbol` at the given position notional
        (USDC). Hyperliquid's marginTable gives, per notional tier, the max leverage
        allowed in that tier; the standard formula for a tier's MMR is
        1 / (2 * tier_maxLeverage). A fixed conservatism buffer is added on top to cover
        the fee/funding gap Hyperliquid folds into its live liquidationPx but that this
        static formula can't model (confirmed via BTC 40x: theoretical 1.25% vs.
        implied-real 1.2961% from a live position — see investigation report section D).
        Margin mode is not tier-relevant on Hyperliquid (isolated/cross use the same
        marginTable) — accepted only for interface parity with the Blofin adapter.
        Returns None on failure; callers MUST fall back to a conservative static value.
        """
        try:
            await self._get_asset_index(symbol)  # ensures caches populated
            coin = symbol.replace("-USDT", "").replace("-USD", "").upper()
            table_id = (self._margin_table_id_cache or {}).get(coin)
            if not table_id:
                return None

            tiers = self._margin_tables_cache.get(table_id)
            if tiers is None:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{self.base_url}/info",
                        json={"type": "marginTable", "id": table_id},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                tiers = data.get("marginTiers", [])
                self._margin_tables_cache[table_id] = tiers

            if not tiers:
                return None

            # Tiers are ordered by ascending lowerBound; the applicable tier is the last
            # one whose lowerBound <= notional.
            applicable = [t for t in tiers if float(t.get("lowerBound", 0)) <= notional]
            tier = applicable[-1] if applicable else tiers[0]
            tier_max_lev = float(tier.get("maxLeverage") or 0)
            if tier_max_lev <= 0:
                return None

            return (1.0 / (2.0 * tier_max_lev)) + MMR_CONSERVATISM_BUFFER
        except Exception as e:
            logger.warning(f"HyperliquidAdapter.get_maintenance_margin_rate({symbol}) failed: {e}")
            return None

    async def get_mark_price(self, symbol: str) -> float | None:
        """Return the current mark price for `symbol` via metaAndAssetCtxs. Returns None on error."""
        try:
            asset_index = await self._get_asset_index(symbol)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/info",
                    json={"type": "metaAndAssetCtxs"},
                )
                resp.raise_for_status()
                meta_and_ctx = resp.json()
            asset_ctxs = meta_and_ctx[1]
            if asset_index >= len(asset_ctxs):
                return None
            mark_px = float(asset_ctxs[asset_index].get("markPx") or 0)
            return mark_px if mark_px > 0 else None
        except Exception as e:
            logger.warning(f"HyperliquidAdapter.get_mark_price({symbol}) failed: {e}")
            return None

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

    def _round_size(self, symbol: str, size: float) -> float:
        """Quantize order size to the coin's szDecimals. HL rejects sizes with
        more decimal places than szDecimals ('Order has invalid size.')."""
        coin = symbol.replace("-USDT", "").replace("-USD", "").upper()
        sz_dec = (self._sz_decimals_cache or {}).get(coin, 4)
        return round(float(size), sz_dec)

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
            universe = meta.get("universe", [])
            self._asset_cache = {
                asset["name"]: idx
                for idx, asset in enumerate(universe)
            }
            self._sz_decimals_cache = {
                asset["name"]: int(asset.get("szDecimals", 4))
                for asset in universe
            }
            self._max_lev_cache = {
                asset["name"]: int(asset.get("maxLeverage", 0) or 0)
                for asset in universe
            }
            self._margin_table_id_cache = {
                asset["name"]: int(asset.get("marginTableId", 0) or 0)
                for asset in universe
            }
            logger.info(
                f"Loaded {len(self._asset_cache)} assets from Hyperliquid"
            )

        if coin not in self._asset_cache:
            # Refresh cache once in case asset was recently added
            self._asset_cache = None
            self._sz_decimals_cache = None
            self._max_lev_cache = None
            self._margin_table_id_cache = None
            raise ValueError(
                f"Asset '{coin}' not found in Hyperliquid universe. "
                f"Check symbol format (expected e.g. 'BTC', not 'BTC-USDT')."
            )

        return self._asset_cache[coin]

    async def get_instrument_specs(self) -> dict:
        """Return per-symbol precision descriptors for all Hyperliquid assets."""
        try:
            if self._asset_cache is None:
                await self._get_asset_index("BTC-USDT")
            specs = {}
            for coin in (self._asset_cache or {}):
                sz_dec = (self._sz_decimals_cache or {}).get(coin, 4)
                specs[f"{coin}-USDT"] = {
                    "price": {"mode": "sigfig", "sigfigs": 5},
                    "size":  {"dp": sz_dec},
                }
            return specs
        except Exception as e:
            logger.error(f"HyperliquidAdapter.get_instrument_specs failed: {e}")
            return {}

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
        price_wire   = self._float_to_wire(self._round_price(float(price_str)))
        size_rounded = self._round_size(order.symbol, float(order.size))
        if size_rounded <= 0:
            return OrderResult(
                success=False,
                status="rejected",
                error_msg=(
                    f"Order size {order.size} rounds to 0 at szDecimals precision "
                    f"for {order.symbol}; increase margin_per_trade or size."
                ),
            )
        size_wire = self._float_to_wire(size_rounded)

        entry_order = {
            "a": asset_index,
            "b": is_buy,
            "p": price_wire,
            "s": size_wire,
            "r": reduce_only,
            "t": order_type_payload,
        }

        orders_list = [entry_order]
        grouping    = "na"

        # Attach TP/SL trigger legs for opening orders (reduce_only=False)
        # trigger dict order: isMarket, triggerPx, tpsl (signature-critical)
        if not reduce_only and (order.tp_price is not None or order.sl_price is not None):
            trigger_is_buy = not is_buy  # opposite side to entry
            grouping = "normalTpsl"

            if order.tp_price is not None:
                tp_wire = self._float_to_wire(self._round_price(float(order.tp_price)))
                orders_list.append({
                    "a": asset_index,
                    "b": trigger_is_buy,
                    "p": tp_wire,
                    "s": size_wire,
                    "r": True,
                    "t": {"trigger": {"isMarket": True, "triggerPx": tp_wire, "tpsl": "tp"}},
                })

            if order.sl_price is not None:
                sl_wire = self._float_to_wire(self._round_price(float(order.sl_price)))
                orders_list.append({
                    "a": asset_index,
                    "b": trigger_is_buy,
                    "p": sl_wire,
                    "s": size_wire,
                    "r": True,
                    "t": {"trigger": {"isMarket": True, "triggerPx": sl_wire, "tpsl": "sl"}},
                })

            logger.info(
                f"HL order with TP/SL: tp={order.tp_price} sl={order.sl_price}"
                f" symbol={order.symbol} side={'buy' if is_buy else 'sell'}"
            )

        action = {
            "type": "order",
            "orders": orders_list,
            "grouping": grouping,
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

        filled  = first.get("filled", {})
        resting = first.get("resting", {})
        oid     = str(filled.get("oid", "")) or str(resting.get("oid", ""))

        avg_px = filled.get("avgPx")

        # HL already tells us whether the order filled immediately or is sitting on the
        # book — a resting order must be reported as "pending", not "filled". The ack
        # alone can't distinguish a partial-fill-then-resting from a zero-fill-then-resting,
        # so callers needing partial-fill detail must poll get_open_orders/userFills.
        if filled:
            order_status = "filled"
        elif resting:
            order_status = "pending"
        else:
            order_status = "filled"  # unexpected response shape; preserve prior behavior

        # Log TP/SL trigger order IDs and warn on any trigger-leg failure.
        # Trigger-order statuses may be plain strings ("resting") or dicts.
        for i, st in enumerate(statuses[1:], start=1):
            if isinstance(st, str):
                logger.info(f"HL TP/SL leg {i} placed (status: {st})")
            elif isinstance(st, dict):
                if "error" in st:
                    logger.warning(
                        f"HL TP/SL leg {i} placement error: {st['error']} "
                        f"(entry oid={oid})"
                    )
                else:
                    trig_oid = str(
                        st.get("resting", {}).get("oid", "")
                        or st.get("filled", {}).get("oid", "")
                    )
                    if trig_oid:
                        logger.info(f"HL TP/SL leg {i} placed: oid={trig_oid}")

        realized_pnl = None
        fee = None
        if oid and order_status == "filled":
            fill_data = await self._get_fill_data(int(oid))
            if fill_data is not None:
                fee = fill_data["fee"]
                if reduce_only:
                    realized_pnl = fill_data["pnl"]

        ts = filled.get("totalSz")
        if ts not in (None, "", "0"):
            actual_fill_size = Decimal(str(ts))
        else:
            actual_fill_size = Decimal(str(size_rounded)) if order_status == "filled" else None

        return OrderResult(
            success=True,
            status=order_status,
            exchange_order_id=oid or None,
            actual_fill_price=Decimal(str(avg_px)) if avg_px else None,
            actual_fill_size=actual_fill_size,
            realized_pnl=realized_pnl,
            fee=fee,
            raw_response=data,
        )

    async def _get_fill_data(self, oid: int) -> Optional[dict]:
        """Query userFills once and return {'pnl': Decimal, 'fee': Decimal} summed across
        every partial fill matching the given order id.
        Returns None only if no fills are found for the oid (unknown outcome).
        Returns zeros if the order generated no closed PnL (e.g. open) — fee is still summed."""
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
            return {
                "pnl": sum(Decimal(str(f.get("closedPnl", "0"))) for f in matching),
                "fee": sum(Decimal(str(f.get("fee", "0"))) for f in matching),
            }
        except Exception as e:
            logger.warning(f"HyperliquidAdapter._get_fill_data failed for oid {oid}: {e}")
            return None

    async def _update_leverage(self, asset_index: int, leverage: int, margin_mode: str) -> None:
        """Send an updateLeverage action to Hyperliquid before opening a position.
        Raises on failure so the caller can abort the order (fail-safe)."""
        import msgpack
        from eth_hash.auto import keccak

        is_cross = (margin_mode or "isolated").lower() != "isolated"
        nonce = int(time.time() * 1000)
        action = {
            "type":     "updateLeverage",
            "asset":    asset_index,
            "isCross":  is_cross,
            "leverage": int(leverage),
        }
        action_bytes  = msgpack.packb(action, use_bin_type=True)
        nonce_bytes   = nonce.to_bytes(8, "big")
        connection_id = keccak(action_bytes + nonce_bytes + b'\x00')

        source = "b" if self.base_url.endswith("testnet.xyz") else "a"
        message = {"source": source, "connectionId": connection_id}
        signed = self._account.sign_typed_data(
            domain_data=_HL_DOMAIN,
            message_types=_HL_TYPES,
            message_data=message,
        )
        payload = {
            "action":    action,
            "nonce":     nonce,
            "signature": {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v},
            "vaultAddress": None,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self.base_url}/exchange", json=payload)
            resp.raise_for_status()
            data = resp.json()

        status = data.get("status")
        if status != "ok":
            raise ValueError(f"Hyperliquid updateLeverage failed: {data}")
        logger.info(
            f"HyperliquidAdapter: leverage set to {leverage}x "
            f"(asset={asset_index}, {'cross' if is_cross else 'isolated'})"
        )

    async def list_instruments(self) -> list[str]:
        """Return all perpetual instrument symbols in BASE-USDT format."""
        try:
            if self._asset_cache is None:
                await self._get_asset_index("BTC-USDT")  # populates cache as a side effect
            return sorted(f"{coin}-USDT" for coin in self._asset_cache.keys())
        except Exception as e:
            logger.error(f"HyperliquidAdapter.list_instruments failed: {e}")
            return []

    async def get_min_order_size(self, symbol: str) -> float:
        try:
            coin = symbol.replace("-USDT", "").replace("-USD", "").upper()
            if self._sz_decimals_cache is None:
                await self._get_asset_index(symbol)  # populates both caches
            sz_dec = self._sz_decimals_cache.get(coin, 4)
            return 10 ** (-sz_dec)
        except Exception as e:
            logger.warning(f"HyperliquidAdapter.get_min_order_size({symbol}) failed: {e}")
            return 0.0

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

    async def get_closed_position_details(self, symbol: str, since_ms: int | None = None) -> dict | None:
        try:
            coin = symbol.replace("-USDT", "").replace("-USD", "").upper()
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/info",
                    json={"type": "userFills", "user": self.query_address},
                )
                resp.raise_for_status()
            fills = resp.json()

            # Keep only closing fills for this coin (dir contains "Close" or "Liq"),
            # scoped to fills at or after since_ms so PnL is not summed across the whole
            # coin history.
            close_fills = [
                f for f in fills
                if f.get("coin") == coin and (
                    "Close" in (f.get("dir") or "") or
                    "Liq"   in (f.get("dir") or "")
                )
                and (since_ms is None or int(f.get("time", 0)) >= since_ms)
            ]
            if not close_fills:
                return None

            # Most recent fill first
            close_fills.sort(key=lambda f: f.get("time", 0), reverse=True)
            latest = close_fills[0]

            is_liquidation = any("Liq" in (f.get("dir") or "") for f in close_fills)

            pnl = sum(Decimal(str(f.get("closedPnl", "0"))) for f in close_fills)
            fee = sum(Decimal(str(f.get("fee", "0"))) for f in close_fills)
            # Weighted-average closing price
            total_sz = sum(float(f.get("sz", 0)) for f in close_fills)
            if total_sz > 0:
                avg_px = sum(float(f.get("px", 0)) * float(f.get("sz", 0)) for f in close_fills) / total_sz
            else:
                avg_px = float(latest.get("px", 0))

            from datetime import datetime, timezone as tz
            closed_at = datetime.fromtimestamp(
                int(latest.get("time", 0)) / 1000, tz=tz.utc
            ) if latest.get("time") else None

            return {
                "close_reason":  "Liquidated" if is_liquidation else "Closed on exchange",
                "closing_price": Decimal(str(round(avg_px, 6))),
                "pnl_realized":  pnl,
                "fee":           fee,
                "closed_at":     closed_at,
                "raw":           close_fills,
            }
        except Exception as e:
            logger.error(f"HyperliquidAdapter.get_closed_position_details failed: {e}")
            return None

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

    async def _fetch_frontend_open_orders(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self.base_url}/info",
                json={"type": "frontendOpenOrders", "user": self.query_address},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """
        Return resting, non-trigger limit orders via frontendOpenOrders.
        TP/SL triggers (isTrigger=true) are list_trigger_orders' job.
        """
        try:
            coin_filter = symbol.replace("-USDT", "").replace("-USD", "").upper() if symbol else None
            orders = await self._fetch_frontend_open_orders()

            result = []
            for o in orders:
                if o.get("isTrigger"):
                    continue
                coin = o.get("coin")
                if coin_filter and coin != coin_filter:
                    continue
                orig_sz     = float(o.get("origSz") or 0)
                remaining   = float(o.get("sz") or 0)
                filled_size = max(orig_sz - remaining, 0.0)
                result.append({
                    "order_id":      str(o["oid"]),
                    "symbol":        f"{coin}-USDT",
                    "side":          "buy" if o.get("side") == "B" else "sell",
                    "price":         float(o.get("limitPx") or 0),
                    "size":          orig_sz,
                    "filled_size":   filled_size,
                    "status":        "partially_filled" if filled_size > 0 else "resting",
                    "created_at_ms": int(o.get("timestamp") or 0),
                })
            return result
        except Exception as e:
            logger.error(f"HyperliquidAdapter.get_open_orders failed: {e}")
            return []

    async def amend_order(
        self, symbol: str, order_id: str, new_price: float | None = None, new_size: float | None = None
    ) -> dict:
        """
        Amend a resting order's price/size via Hyperliquid's native 'modify' action.
        'modify' requires a full replacement order spec, so unset fields (side,
        reduceOnly) are preserved from the current resting order.
        """
        try:
            if new_price is None and new_size is None:
                return {"success": False, "error": "amend_order requires new_price or new_size"}

            orders = await self._fetch_frontend_open_orders()
            existing = next((o for o in orders if str(o.get("oid")) == str(order_id)), None)
            if not existing:
                return {"success": False, "error": f"order {order_id} not found in open orders"}

            import msgpack
            from eth_hash.auto import keccak

            asset_index = await self._get_asset_index(symbol)
            is_buy      = existing.get("side") == "B"
            reduce_only = bool(existing.get("reduceOnly", False))

            price = new_price if new_price is not None else float(existing.get("limitPx"))
            size  = new_size if new_size is not None else float(existing.get("sz"))

            size_rounded = self._round_size(symbol, float(size))
            if size_rounded <= 0:
                return {"success": False, "error": f"amended size {size} rounds to 0 at szDecimals precision"}

            price_wire = self._float_to_wire(self._round_price(float(price)))
            size_wire  = self._float_to_wire(size_rounded)

            order_spec = {
                "a": asset_index,
                "b": is_buy,
                "p": price_wire,
                "s": size_wire,
                "r": reduce_only,
                "t": {"limit": {"tif": "Gtc"}},
            }
            action = {"type": "modify", "oid": int(order_id), "order": order_spec}

            nonce         = int(time.time() * 1000)
            action_bytes  = msgpack.packb(action, use_bin_type=True)
            nonce_bytes   = nonce.to_bytes(8, "big")
            connection_id = keccak(action_bytes + nonce_bytes + b'\x00')

            source  = "b" if self.base_url.endswith("testnet.xyz") else "a"
            message = {"source": source, "connectionId": connection_id}
            signed  = self._account.sign_typed_data(
                domain_data=_HL_DOMAIN,
                message_types=_HL_TYPES,
                message_data=message,
            )

            payload = {
                "action":       action,
                "nonce":        nonce,
                "signature":    {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v},
                "vaultAddress": None,
            }

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.base_url}/exchange", json=payload)
                resp.raise_for_status()
                data = resp.json()

            logger.debug(f"HL amend_order({symbol}, oid={order_id}): {data}")

            if data.get("status") != "ok":
                return {"success": False, "error": str(data)}
            resp_field = data.get("response", {})
            if isinstance(resp_field, str):
                return {"success": False, "error": resp_field}
            statuses = resp_field.get("data", {}).get("statuses", [])
            first = statuses[0] if statuses else {}
            if isinstance(first, dict) and "error" in first:
                return {"success": False, "error": first["error"]}
            return {"success": True, "order_id": order_id, "raw_response": data}
        except Exception as e:
            logger.error(f"HyperliquidAdapter.amend_order({symbol}, {order_id}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def list_trigger_orders(self, symbol: str) -> Optional[list[dict]]:
        """
        Return all open TP/SL trigger orders for a symbol.
        Each entry: {oid, tpsl, triggerPx, sz, side}
        Uses frontendOpenOrders which includes trigger orders (not in openOrders).

        Returns None (not []) on a genuine exchange-call failure — callers must not
        treat None as "confirmed no trigger orders."
        """
        try:
            coin = symbol.replace("-USDT", "").replace("-USD", "").upper()
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/info",
                    json={"type": "frontendOpenOrders", "user": self.query_address},
                )
                resp.raise_for_status()
                orders = resp.json()

            result = []
            for o in orders:
                if o.get("coin") != coin:
                    continue
                order_type = o.get("orderType", "")
                # Include TP/SL trigger orders only (not resting limit orders)
                if "Trigger" not in order_type and "Profit" not in order_type and "Stop" not in order_type:
                    continue
                tpsl = o.get("tpsl") or (
                    "tp" if "Profit" in order_type else
                    "sl" if "Stop" in order_type else None
                )
                result.append({
                    "oid":       int(o["oid"]),
                    "tpsl":      tpsl,
                    "triggerPx": o.get("triggerPx"),
                    "sz":        o.get("sz"),
                    "side":      "buy" if o.get("side") == "B" else "sell",
                })
            logger.debug(f"HL list_trigger_orders({symbol}): {len(result)} trigger orders")
            return result
        except Exception as e:
            logger.error(f"HyperliquidAdapter.list_trigger_orders failed: {e}")
            return None

    async def cancel_order(self, symbol: str, oid: int) -> dict:
        """
        Cancel a single order by oid through the same msgpack/keccak signing path.
        Action format: {"type": "cancel", "cancels": [{"a": asset_index, "o": oid}]}
        Field order in cancel dict is signature-critical: a, o.
        Works for both regular resting limit orders and trigger (TP/SL) orders — HL
        uses the same oid namespace and 'cancel' action for both.
        """
        try:
            import msgpack
            from eth_hash.auto import keccak

            oid = int(oid)  # callers may pass a str oid (e.g. from a JSON request body)
            asset_index = await self._get_asset_index(symbol)
            nonce = int(time.time() * 1000)

            action = {
                "type":    "cancel",
                "cancels": [{"a": asset_index, "o": oid}],
            }

            action_bytes  = msgpack.packb(action, use_bin_type=True)
            nonce_bytes   = nonce.to_bytes(8, "big")
            connection_id = keccak(action_bytes + nonce_bytes + b'\x00')

            source = "b" if self.base_url.endswith("testnet.xyz") else "a"
            message = {"source": source, "connectionId": connection_id}

            signed = self._account.sign_typed_data(
                domain_data=_HL_DOMAIN,
                message_types=_HL_TYPES,
                message_data=message,
            )

            payload = {
                "action":    action,
                "nonce":     nonce,
                "signature": {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v},
                "vaultAddress": None,
            }

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{self.base_url}/exchange", json=payload)
                resp.raise_for_status()
                data = resp.json()

            logger.debug(f"HL cancel_order({symbol}, oid={oid}): {data}")

            if data.get("status") == "ok":
                resp_field = data.get("response", {})
                if isinstance(resp_field, dict):
                    statuses = resp_field.get("data", {}).get("statuses", [])
                    first = statuses[0] if statuses else {}
                    if "error" in first:
                        return {"success": False, "error": first["error"]}
                return {"success": True, "oid": oid}
            else:
                return {"success": False, "error": str(data)}

        except Exception as e:
            logger.error(f"HyperliquidAdapter.cancel_order({symbol}, {oid}) failed: {e}")
            return {"success": False, "error": str(e)}

    async def place_trigger_orders(
        self,
        symbol: str,
        trigger_side: str,
        size: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
    ) -> dict:
        """
        Place standalone TP and/or SL reduce-only trigger orders for an existing position.
        Uses grouping="na" so each trigger is independent (no entry order required).
        trigger_side: side of the trigger order (opposite to position side).
        """
        try:
            import msgpack
            from eth_hash.auto import keccak

            asset_index    = await self._get_asset_index(symbol)
            is_trigger_buy = (trigger_side == "buy")
            size_wire      = self._float_to_wire(self._round_size(symbol, float(size)))
            nonce          = int(time.time() * 1000)
            orders_list    = []

            if tp_price is not None:
                tp_wire = self._float_to_wire(self._round_price(tp_price))
                orders_list.append({
                    "a": asset_index,
                    "b": is_trigger_buy,
                    "p": tp_wire,
                    "s": size_wire,
                    "r": True,
                    "t": {"trigger": {"isMarket": True, "triggerPx": tp_wire, "tpsl": "tp"}},
                })

            if sl_price is not None:
                sl_wire = self._float_to_wire(self._round_price(sl_price))
                orders_list.append({
                    "a": asset_index,
                    "b": is_trigger_buy,
                    "p": sl_wire,
                    "s": size_wire,
                    "r": True,
                    "t": {"trigger": {"isMarket": True, "triggerPx": sl_wire, "tpsl": "sl"}},
                })

            if not orders_list:
                return {"success": True, "placed": []}

            action = {"type": "order", "orders": orders_list, "grouping": "na"}

            action_bytes  = msgpack.packb(action, use_bin_type=True)
            nonce_bytes   = nonce.to_bytes(8, "big")
            connection_id = keccak(action_bytes + nonce_bytes + b'\x00')

            source = "b" if self.base_url.endswith("testnet.xyz") else "a"
            message = {"source": source, "connectionId": connection_id}

            signed = self._account.sign_typed_data(
                domain_data=_HL_DOMAIN,
                message_types=_HL_TYPES,
                message_data=message,
            )

            payload = {
                "action":       action,
                "nonce":        nonce,
                "signature":    {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v},
                "vaultAddress": None,
            }

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.base_url}/exchange", json=payload)
                resp.raise_for_status()
                data = resp.json()

            logger.debug(f"HL place_trigger_orders({symbol}): {data}")

            if data.get("status") != "ok":
                return {"success": False, "error": str(data)}

            resp_field = data.get("response", {})
            if isinstance(resp_field, str):
                return {"success": False, "error": resp_field}

            statuses = resp_field.get("data", {}).get("statuses", [])
            placed = []
            for i, st in enumerate(statuses):
                leg_type = "tp" if i < (1 if tp_price else 0) else "sl"
                if isinstance(st, str):
                    placed.append({"tpsl": leg_type, "status": st})
                    logger.info(f"HL trigger leg ({leg_type}) placed (status: {st})")
                elif isinstance(st, dict):
                    if "error" in st:
                        logger.warning(f"HL trigger leg ({leg_type}) error: {st['error']}")
                        placed.append({"tpsl": leg_type, "error": st["error"]})
                    else:
                        trig_oid = str(
                            st.get("resting", {}).get("oid", "")
                            or st.get("filled", {}).get("oid", "")
                        )
                        placed.append({"tpsl": leg_type, "oid": trig_oid, "status": "placed"})
                        logger.info(f"HL trigger leg ({leg_type}) placed: oid={trig_oid}")

            requested_legs = len(orders_list)
            failed_legs = [p for p in placed if "error" in p]
            success = (len(placed) == requested_legs) and not failed_legs
            if not success:
                logger.warning(
                    f"HL place_trigger_orders({symbol}) PARTIAL/FAILED: "
                    f"requested={requested_legs} landed={len(placed) - len(failed_legs)} "
                    f"failed={len(failed_legs)}"
                )
            return {"success": success, "placed": placed}

        except Exception as e:
            logger.error(f"HyperliquidAdapter.place_trigger_orders failed: {e}")
            return {"success": False, "error": str(e)}
