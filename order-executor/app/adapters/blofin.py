import base64
import hashlib
import hmac
import json
import logging
import math
import time
import asyncio
from decimal import Decimal
from typing import Dict, List, Optional

import httpx

from app.adapters.base import ExchangeAdapter, ExchangeUnavailableError, MMR_CONSERVATISM_BUFFER
from app.models import OrderRequest, OrderResult, Position

logger = logging.getLogger(__name__)

_INSTRUMENTS_TTL = 86400  # 24 hours

class BlofinAdapter(ExchangeAdapter):
    # Class-level cache shared across all instances, keyed by base_url
    _instruments: Dict[str, Dict[str, dict]] = {}   # base_url -> instId -> spec
    _instruments_ts: Dict[str, float]        = {}   # base_url -> fetch timestamp
    # base_url -> "instId:marginMode" -> tier list (each: minSize/maxSize/maintenanceMarginRate/maxLeverage)
    _position_tiers: Dict[str, Dict[str, list]] = {}
    _position_tiers_ts: Dict[str, Dict[str, float]] = {}

    def __init__(self, credentials: dict, mode: str):
        super().__init__(credentials, mode)
        self.api_key        = credentials["api_key"]
        self.api_secret     = credentials["api_secret"]
        self.api_passphrase = credentials["api_passphrase"]
        self.base_url = (
            "https://demo-trading-api.blofin.com"
            if mode == "demo"
            else "https://openapi.blofin.com"
        )

    async def _refresh_instruments(self) -> None:
        """Fetch all SWAP instrument specs and populate the class-level cache."""
        path = "/api/v1/market/instruments?instType=SWAP"
        headers = self._headers("GET", path, "")
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                resp = await client.get(path, headers=headers)
            items = resp.json().get("data", [])
            if items:
                BlofinAdapter._instruments[self.base_url] = {
                    inst["instId"]: inst for inst in items
                }
                BlofinAdapter._instruments_ts[self.base_url] = time.time()
                logger.info(f"BlofinAdapter: cached {len(items)} instrument specs from {self.base_url}")
        except Exception as e:
            logger.warning(f"BlofinAdapter: failed to refresh instruments: {e}")

    async def _get_instrument(self, inst_id: str) -> Optional[dict]:
        """Return instrument spec for inst_id, refreshing if cache is stale or missing."""
        age = time.time() - BlofinAdapter._instruments_ts.get(self.base_url, 0)
        if age > _INSTRUMENTS_TTL or self.base_url not in BlofinAdapter._instruments:
            await self._refresh_instruments()
        return BlofinAdapter._instruments.get(self.base_url, {}).get(inst_id)

    async def _to_contracts(self, inst_id: str, base_volume: Decimal) -> str:
        """Convert base-asset volume to Blofin contract count string."""
        inst = await self._get_instrument(inst_id)
        if inst:
            contract_val = float(inst.get("contractValue") or "0.001")
            lot_size     = float(inst.get("lotSize")       or "0.1")
            min_size     = float(inst.get("minSize")       or "0.1")
        else:
            # Fallback: assume BTC-style 0.001 contract value
            logger.warning(f"BlofinAdapter: no instrument spec for {inst_id}, using defaults")
            contract_val, lot_size, min_size = 0.001, 0.1, 0.1

        raw      = float(base_volume) / contract_val
        rounded  = round(round(raw / lot_size) * lot_size, 8)
        enforced = max(min_size, rounded)
        return f"{enforced:g}"

    async def _round_to_tick(self, inst_id: str, price) -> float:
        """Round a price to the instrument's tick size (same fallback as
        get_instrument_specs()) — Blofin rejects prices that aren't an exact tick
        multiple with code 102016 'Precision does not match'."""
        inst = await self._get_instrument(inst_id)
        tick = float((inst or {}).get("tickSize") or "0.1")
        raw = float(price)
        return round(round(raw / tick) * tick, 10)

    async def _to_base(self, inst_id: str, contracts: Decimal) -> Decimal:
        """Convert a Blofin contract count to base-asset volume (inverse of _to_contracts).
        base_coins = contracts * contractValue. Uses the cached instrument spec."""
        inst = await self._get_instrument(inst_id)
        if not inst:
            logger.warning(
                f"BlofinAdapter: no instrument spec for {inst_id}, using default contractValue 0.001"
            )
        contract_val = (inst or {}).get("contractValue") or "0.001"
        return contracts * Decimal(str(contract_val))

    def _sign(self, method: str, path: str, body: str, timestamp: str, nonce: str) -> str:
        # Blofin Prehash: requestPath + method + timestamp + nonce + body
        message = f"{path}{method.upper()}{timestamp}{nonce}{body}"
        mac = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        # Blofin requires Base64 encoding of the hexadecimal digest
        hex_digest = mac.hexdigest()
        return base64.b64encode(hex_digest.encode("utf-8")).decode("utf-8")

    def _headers(self, method: str, path: str, body: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        nonce = str(int(time.time() * 1000))
        signature = self._sign(method, path, body, timestamp, nonce)
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "ACCESS-NONCE": nonce,
            "Content-Type": "application/json",
        }

    async def _get_order_state(self, symbol: str, order_id: str) -> dict:
        """Return the live order dict for order_id. Checks orders-pending first (still
        resting/partially_filled — has a 'state' field); falls back to order history
        (fully filled/canceled) if it's no longer pending."""
        try:
            path = f"/api/v1/trade/orders-pending?instId={symbol}"
            headers = self._headers("GET", path, "")
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                resp = await client.get(path, headers=headers)
            items = resp.json().get("data") or []
            for item in items:
                if str(item.get("orderId")) == str(order_id):
                    return item
        except Exception as e:
            logger.warning(f"Blofin _get_order_state: pending lookup failed for {order_id}: {e}")
        return await self._get_order_details(symbol, order_id)

    async def _get_order_details(self, symbol: str, order_id: str) -> dict:
        # Try orders-history first (completed/filled orders), fall back to fills
        for path_tpl in [
            "/api/v1/trade/orders-history?instId={inst}&orderId={oid}",
            "/api/v1/trade/fills-history?instId={inst}&orderId={oid}",
        ]:
            try:
                full_path = path_tpl.format(inst=symbol, oid=order_id)
                headers = self._headers("GET", full_path, "")
                async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                    response = await client.get(full_path, headers=headers)
                if response.status_code != 200:
                    continue
                data = response.json()
                code = str(data.get("code", "0"))
                if code not in ("0", "200"):
                    logger.debug(f"Blofin {full_path} returned code {code}: {data.get('msg')}")
                    continue
                items = data.get("data", [])
                if isinstance(items, list) and items:
                    return items[0]
            except Exception as e:
                logger.warning(f"Blofin: error fetching order details from {path_tpl}: {e}")
        logger.warning(f"Blofin: could not fetch order details for {order_id}")
        return {}

    def _parse_fill_price(self, details: dict):
        """Extract fill price from order details, trying all field name variants."""
        raw = (details.get("avgPrice") or details.get("averagePrice")
               or details.get("fillPrice") or details.get("avgFillPrice"))
        try:
            v = Decimal(str(raw)) if raw else None
            return v if v and v > 0 else None
        except Exception:
            return None

    async def _parse_fill_size(self, inst_id: str, details: dict, fallback_contracts: str) -> Optional[Decimal]:
        """Extract filled contract count from order details and convert to base coins.
        BloFin field priority: filledSize → accFillSz → fillSz → fallback (submitted size)."""
        raw = (details.get("filledSize") or details.get("accFillSz")
               or details.get("fillSz") or fallback_contracts)
        try:
            contracts = Decimal(str(raw)) if raw else None
            if not contracts or contracts <= 0:
                return None
            return await self._to_base(inst_id, contracts)
        except Exception:
            return None

    async def get_max_leverage(self, symbol: str) -> int:
        """Exchange max leverage for the instrument. Returns 0 if unknown."""
        try:
            inst = await self._get_instrument(symbol)
            if not inst:
                return 0
            return int(float(inst.get("maxLeverage") or 0))
        except Exception as e:
            logger.warning(f"BlofinAdapter.get_max_leverage({symbol}) failed: {e}")
            return 0

    async def _get_position_tiers(self, inst_id: str, margin_mode: str) -> list:
        """Return Blofin's maintenance-margin tier ladder for inst_id/margin_mode,
        refreshing the class-level cache if stale or missing. Public endpoint —
        confirmed live at /api/v1/market/position-tiers, no auth required."""
        key = f"{inst_id}:{margin_mode}"
        cache    = BlofinAdapter._position_tiers.setdefault(self.base_url, {})
        ts_cache = BlofinAdapter._position_tiers_ts.setdefault(self.base_url, {})
        age = time.time() - ts_cache.get(key, 0)
        if key not in cache or age > _INSTRUMENTS_TTL:
            path = f"/api/v1/market/position-tiers?instId={inst_id}&marginMode={margin_mode}"
            try:
                async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                    resp = await client.get(path)
                data = resp.json().get("data", [])
                if data:
                    cache[key] = data
                    ts_cache[key] = time.time()
            except Exception as e:
                logger.warning(f"BlofinAdapter: failed to refresh position tiers for {inst_id}: {e}")
        return cache.get(key, [])

    async def get_maintenance_margin_rate(
        self, symbol: str, notional: float, margin_mode: str = "isolated"
    ) -> Optional[float]:
        """
        Return the real maintenance-margin rate for `symbol` at the given position
        notional (USDT), sourced from Blofin's own tiered position-tiers table (tier
        boundaries are USDT notional; verified live for BTC-USDT and HYPE-USDT — see
        investigation report section D). A fixed conservatism buffer is added on top for
        consistency with the Hyperliquid path and to cover any residual gap versus the
        exchange's live liquidation price. Returns None on failure/no data; callers MUST
        fall back to a conservative static value.
        """
        try:
            tiers = await self._get_position_tiers(symbol, margin_mode)
            if not tiers:
                return None
            # Tiers are ordered by ascending minSize; the applicable tier is the last
            # one whose minSize <= notional.
            applicable = [t for t in tiers if float(t.get("minSize", 0)) <= notional]
            tier = applicable[-1] if applicable else tiers[0]
            mmr = float(tier.get("maintenanceMarginRate") or 0)
            if mmr <= 0:
                return None
            return mmr + MMR_CONSERVATISM_BUFFER
        except Exception as e:
            logger.warning(f"BlofinAdapter.get_maintenance_margin_rate({symbol}) failed: {e}")
            return None

    async def get_mark_price(self, symbol: str) -> float | None:
        """Return the current mark price for `symbol`. Returns None on error."""
        try:
            path = f"/api/v1/market/mark-price?instId={symbol}"
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                resp = await client.get(path)
                resp.raise_for_status()
            data = resp.json().get("data") or []
            if not data:
                return None
            mark_px = float(data[0].get("markPrice") or 0)
            return mark_px if mark_px > 0 else None
        except Exception as e:
            logger.warning(f"BlofinAdapter.get_mark_price({symbol}) failed: {e}")
            return None

    async def _set_leverage(self, inst_id: str, leverage: int, margin_mode: str) -> None:
        """Set leverage for an instrument before placing an order."""
        path = "/api/v1/account/set-leverage"
        body_data = {
            "instId":       inst_id,
            "leverage":     str(leverage),
            "marginMode":   margin_mode,
            "positionSide": "net",
        }
        body_str = json.dumps(body_data, separators=(",", ":"))
        headers  = self._headers("POST", path, body_str)
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                resp = await client.post(path, content=body_str, headers=headers)
            data = resp.json()
            if str(data.get("code")) not in ("0", "200"):
                logger.warning(f"BlofinAdapter: set-leverage failed for {inst_id}: {data.get('msg')}")
            else:
                logger.info(f"BlofinAdapter: leverage set to {leverage}x for {inst_id} ({margin_mode})")
        except Exception as e:
            logger.warning(f"BlofinAdapter: set-leverage error for {inst_id}: {e}")

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """
        Implements ExchangeAdapter.submit_order.
        Wraps the existing place_order / create_order logic.
        """
        try:
            order_size   = await self._to_contracts(order.symbol, order.size)
            margin_mode  = order.margin_mode or "isolated"
            leverage     = order.leverage or 10

            max_lev = await self.get_max_leverage(order.symbol)
            if max_lev and leverage > max_lev:
                # DECISION: reject (do not clamp)
                msg = (f"Requested leverage {leverage}x exceeds Blofin "
                       f"max {max_lev}x for {order.symbol}")
                logger.warning(f"BlofinAdapter: {msg}")
                return OrderResult(success=False, status="rejected", error_msg=msg)

            # Blofin ignores the lever field in order placement; must set it explicitly first
            await self._set_leverage(order.symbol, leverage, margin_mode)

            path = "/api/v1/trade/order"
            is_close = order.signal in ("close_long", "close_short")
            body_data = {
                "instId": order.symbol,
                "marginMode": margin_mode,
                "side": order.side,
                "orderType": order.order_type,
                "size": str(order_size),
                "lever": str(leverage),
            }
            if is_close:
                # Belt-and-suspenders: close signals must never open a net position.
                # Without reduceOnly, an oversized close flips the position on BloFin.
                body_data["reduceOnly"] = "true"
                body_data["positionSide"] = "net"

            if order.price:
                body_data["price"] = str(await self._round_to_tick(order.symbol, order.price))
            if order.tp_price:
                body_data["tpTriggerPrice"] = str(await self._round_to_tick(order.symbol, order.tp_price))
                body_data["tpOrderPrice"] = "-1"  # market execution when triggered
            if order.sl_price:
                body_data["slTriggerPrice"] = str(await self._round_to_tick(order.symbol, order.sl_price))
                body_data["slOrderPrice"] = "-1"  # market execution when triggered

            body_str = json.dumps(body_data, separators=(",", ":"))
            headers = self._headers("POST", path, body_str)

            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                response = await client.post(path, content=body_str, headers=headers)

            if response.status_code != 200:
                logger.warning(f"Blofin order failed. Status: {response.status_code}, Response: {response.text}")
                return OrderResult(
                    success=False,
                    status="rejected",
                    error_msg=f"Blofin returned {response.status_code}: {response.text}",
                    raw_response={"text": response.text}
                )

            data = response.json()
            code = data.get("code")
            if str(code) in ["0", "200"] and data.get("data") and isinstance(data["data"], list) and len(data["data"]) > 0:
                order_info = data["data"][0]
                exchange_order_id = order_info.get("orderId")
                
                # For market orders, wait a bit and fetch details to get fill price, size and P&L
                actual_fill_price = None
                actual_fill_size  = None
                pnl = None
                order_status = "filled"
                if order.order_type == "market":
                    try:
                        await asyncio.sleep(2.0)
                        details = await self._get_order_details(order.symbol, exchange_order_id)
                        if details:
                            actual_fill_price = self._parse_fill_price(details)
                            actual_fill_size  = await self._parse_fill_size(
                                order.symbol, details, order_size
                            )
                            pnl_raw = details.get("pnl") or details.get("realizedPnl")
                            pnl = Decimal(str(pnl_raw)) if pnl_raw is not None else None
                    except Exception as e:
                        logger.warning(f"Blofin submit_order: fill details fetch failed (order still filled): {e}")
                else:
                    # Limit/resting: determine the actual state rather than assuming filled.
                    try:
                        await asyncio.sleep(1.0)
                        state_info = await self._get_order_state(order.symbol, exchange_order_id)
                        state = state_info.get("state")
                        if state in ("live", "partially_filled"):
                            order_status = "pending"
                            actual_fill_price = self._parse_fill_price(state_info)
                            if state == "partially_filled":
                                actual_fill_size = await self._parse_fill_size(
                                    order.symbol, state_info, order_size
                                )
                        else:
                            # Not in the pending book anymore -> fully filled (or, rarely,
                            # already canceled by the exchange before we could check).
                            order_status = "filled"
                            actual_fill_price = self._parse_fill_price(state_info)
                            actual_fill_size = await self._parse_fill_size(
                                order.symbol, state_info, order_size
                            )
                            pnl_raw = state_info.get("pnl") or state_info.get("realizedPnl")
                            pnl = Decimal(str(pnl_raw)) if pnl_raw is not None else None
                    except Exception as e:
                        logger.warning(
                            f"Blofin submit_order: limit state lookup failed, assuming pending: {e}"
                        )
                        order_status = "pending"
                        actual_fill_size = await self._to_base(
                            order.symbol, Decimal(str(order_size))
                        )

                return OrderResult(
                    success=True,
                    status=order_status,
                    exchange_order_id=exchange_order_id,
                    raw_response=data,
                    actual_fill_price=actual_fill_price,
                    actual_fill_size=actual_fill_size,
                    realized_pnl=pnl,
                )
            else:
                error = data.get("msg", "Unknown error")
                return OrderResult(
                    success=False,
                    status="rejected",
                    error_msg=f"{error} ({data})",
                    raw_response=data,
                )
        except Exception as e:
            logger.error(f"BlofinAdapter.submit_order failed: {e}")
            return OrderResult(
                success=False,
                status="route_failed",
                error_msg=str(e),
            )

    async def get_open_positions(self) -> List[Position]:
        path = "/api/v1/account/positions"
        headers = self._headers("GET", path, "")
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            response = await client.get(path, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch positions: {response.text}")
            raise ExchangeUnavailableError(
                f"blofin positions HTTP {response.status_code}: {response.text[:200]}"
            )
            
        data = response.json()
        raw_positions = data.get("data", [])
        
        mapped_positions = []
        for p in raw_positions:
            size_val = float(p.get("positions", 0))
            if size_val == 0:
                continue

            inst_id   = p.get("instId")
            # BloFin reports quantity in contracts; convert to base coins so every consumer
            # (reconciler, dashboard, modify-stops) gets the same unit the DB stores.
            base_size = await self._to_base(inst_id, Decimal(str(abs(size_val))))

            mark_raw = p.get("markPrice") or p.get("last") or p.get("averagePrice", "0")
            liq_px = p.get("liquidationPrice")
            mapped_positions.append(Position(
                symbol=inst_id,
                side="long" if size_val > 0 else "short",
                size=base_size,
                entry_price=Decimal(p.get("averagePrice", "0")),
                leverage=int(p.get("lever") or p.get("leverage") or 10),
                mark_price=Decimal(str(mark_raw)),
                unrealized_pnl=Decimal(p.get("unrealizedPnl", "0")),
                liquidation_price=Decimal(str(liq_px)) if liq_px else None,
            ))

        return mapped_positions

    async def close_position(self, symbol: str, side: str, size=None, margin_mode: str = "isolated") -> OrderResult:
        # Partial close: place a reduce-only market order of opposite side
        if size is not None:
            return await self._partial_close(symbol, side, Decimal(str(size)), margin_mode)

        # Full close: use the dedicated close-position endpoint
        path = "/api/v1/trade/close-position"

        body_data = {
            "instId": symbol,
            "marginMode": margin_mode,
            "positionSide": "net",
        }

        body_str = json.dumps(body_data, separators=(",", ":"))
        headers = self._headers("POST", path, body_str)

        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            response = await client.post(path, content=body_str, headers=headers)

        if response.status_code != 200:
            logger.warning(f"Blofin close failed. Status: {response.status_code}, Response: {response.text}")
            return OrderResult(
                success=False,
                status="rejected",
                error_msg=f"Blofin returned {response.status_code}",
                raw_response={"text": response.text}
            )

        data = response.json()
        code = data.get("code")

        if str(code) in ["0", "200"]:
            # Blofin close-position returns data as dict or list depending on version
            data_payload = data.get("data")
            if isinstance(data_payload, list) and data_payload:
                order_info = data_payload[0]
            elif isinstance(data_payload, dict):
                order_info = data_payload
            else:
                order_info = {}
            exchange_order_id = order_info.get("orderId")

            fill_price = None
            pnl = None
            try:
                await asyncio.sleep(2.0)
                details = await self._get_order_details(symbol, exchange_order_id)
                if details:
                    fill_price = self._parse_fill_price(details)
                    pnl_raw = details.get("pnl") or details.get("realizedPnl")
                    pnl = Decimal(str(pnl_raw)) if pnl_raw is not None else None
            except Exception as e:
                logger.warning(f"Blofin close_position: fill details fetch failed (close still succeeded): {e}")

            return OrderResult(
                success=True,
                status="filled",
                exchange_order_id=exchange_order_id,
                raw_response=data,
                actual_fill_price=fill_price,
                realized_pnl=pnl,
            )
        else:
            error = data.get("msg", "Unknown error")
            return OrderResult(
                success=False,
                status="rejected",
                error_msg=error,
                raw_response=data,
            )

    async def _partial_close(self, symbol: str, side: str, size: Decimal, margin_mode: str) -> OrderResult:
        """Place a reduce-only market order to partially close a position."""
        reduce_side = "sell" if side == "long" else "buy"
        order_size = await self._to_contracts(symbol, size)

        path = "/api/v1/trade/order"
        body_data = {
            "instId":       symbol,
            "marginMode":   margin_mode,
            "side":         reduce_side,
            "orderType":    "market",
            "size":         str(order_size),
            "reduceOnly":   "true",
            "positionSide": "net",
        }
        body_str = json.dumps(body_data, separators=(",", ":"))
        headers  = self._headers("POST", path, body_str)

        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            response = await client.post(path, content=body_str, headers=headers)

        if response.status_code != 200:
            logger.warning(f"Blofin partial close failed. Status: {response.status_code}, Response: {response.text}")
            return OrderResult(
                success=False,
                status="rejected",
                error_msg=f"Blofin returned {response.status_code}",
                raw_response={"text": response.text},
            )

        data = response.json()
        code = data.get("code")

        if str(code) not in ["0", "200"]:
            return OrderResult(
                success=False,
                status="rejected",
                error_msg=data.get("msg", "Unknown error"),
                raw_response=data,
            )

        order_info = (data.get("data") or [{}])
        if isinstance(order_info, list):
            order_info = order_info[0] if order_info else {}
        exchange_order_id = order_info.get("orderId")

        fill_price = None
        pnl = None
        try:
            await asyncio.sleep(2.0)
            details = await self._get_order_details(symbol, exchange_order_id)
            if details:
                fill_price = self._parse_fill_price(details)
                pnl_raw = details.get("pnl") or details.get("realizedPnl")
                pnl = Decimal(str(pnl_raw)) if pnl_raw is not None else None
        except Exception as e:
            logger.warning(f"Blofin _partial_close: fill details fetch failed (order still filled): {e}")

        return OrderResult(
            success=True,
            status="filled",
            exchange_order_id=exchange_order_id,
            raw_response=data,
            actual_fill_price=fill_price,
            realized_pnl=pnl,
        )

    async def get_balance(self) -> dict:
        """Fetch USDT perpetual futures account balance from Blofin."""
        try:
            # Blofin balance endpoint for futures
            # GET /api/v1/account/balance?accountType=futures
            endpoint = "/api/v1/account/balance"
            params   = "accountType=futures"
            full_path = f"{endpoint}?{params}"

            # Use the existing signing pattern already in the adapter
            # to make an authenticated GET request
            headers  = self._headers("GET", full_path, "")
            url      = f"{self.base_url}{full_path}"

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            code = str(data.get("code", "0"))
            if code not in ("0", "200"):
                msg = data.get("msg", "unknown error")
                return {"total_balance": 0.0, "available_balance": 0.0,
                        "used_margin": 0.0, "currency": "USDT",
                        "error": f"Blofin API error {code}: {msg}"}

            # Blofin balance structure:
            # data[0].totalEquity       — account-level equity
            # data[0].details[0].available — per-currency available margin
            data_item = data.get("data", [{}])
            if isinstance(data_item, list):
                data_item = data_item[0] if data_item else {}

            total = float(data_item.get("totalEquity", 0))

            per_ccy = data_item.get("details", [{}])
            if isinstance(per_ccy, list):
                per_ccy = per_ccy[0] if per_ccy else {}
            available = float(per_ccy.get("available", per_ccy.get("availableEquity", 0)))
            used      = total - available

            return {
                "total_balance":     total,
                "available_balance": available,
                "used_margin":       max(used, 0),
                "currency":          "USDT",
            }
        except Exception as e:
            logger.error(f"BlofinAdapter.get_balance failed: {e}")
            return {
                "total_balance":     0.0,
                "available_balance": 0.0,
                "used_margin":       0.0,
                "currency":          "USDT",
                "error":             str(e),
            }

    async def get_instrument_specs(self) -> dict:
        """Return per-symbol precision descriptors for all Blofin instruments."""
        try:
            if not BlofinAdapter._instruments.get(self.base_url):
                await self._refresh_instruments()
            specs = {}
            for inst_id, inst in BlofinAdapter._instruments.get(self.base_url, {}).items():
                tick = float(inst.get("tickSize") or "1")
                lot  = float(inst.get("lotSize") or "1")
                cv   = float(inst.get("contractValue") or "1")
                min_base = lot * cv
                if min_base > 0 and min_base < 1:
                    size_dp = max(0, -int(math.floor(math.log10(min_base + 1e-15))))
                else:
                    size_dp = 0
                specs[inst_id] = {
                    "price": {"mode": "tick", "tick": tick},
                    "size":  {"dp": size_dp},
                }
            return specs
        except Exception as e:
            logger.error(f"BlofinAdapter.get_instrument_specs failed: {e}")
            return {}

    async def list_instruments(self) -> list[str]:
        """Return all SWAP instrument IDs available on this Blofin endpoint."""
        try:
            await self._get_instrument("")   # ensures cache is populated
            return sorted(BlofinAdapter._instruments.get(self.base_url, {}).keys())
        except Exception as e:
            logger.error(f"BlofinAdapter.list_instruments failed: {e}")
            return []

    async def get_min_order_size(self, symbol: str) -> float:
        try:
            inst = await self._get_instrument(symbol)
            if not inst:
                return 0.0
            contract_val = float(inst.get("contractValue") or "0.001")
            min_size     = float(inst.get("minSize")       or "0.1")
            return min_size * contract_val
        except Exception as e:
            logger.warning(f"BlofinAdapter.get_min_order_size({symbol}) failed: {e}")
            return 0.0

    async def get_closed_position_details(self, symbol: str, since_ms: int | None = None) -> dict | None:
        try:
            path = f"/api/v1/account/positions-history?instId={symbol}&limit=5"
            headers = self._headers("GET", path, "")
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                resp = await client.get(path, headers=headers)
            data = resp.json()
            entries = data.get("data") or []
            if not entries:
                return None
            if since_ms is not None:
                entries = [e for e in entries if int(e.get("updateTime") or 0) >= since_ms]
                if not entries:
                    return None
            entry = entries[0]
            is_liquidation = int(entry.get("liquidationPositions") or 0) > 0
            close_ts = int(entry.get("updateTime") or 0)
            from datetime import datetime, timezone as tz
            closed_at = datetime.fromtimestamp(close_ts / 1000, tz=tz.utc) if close_ts else None
            pnl = Decimal(str(entry.get("realizedPnl") or "0"))
            fee = Decimal(str(entry.get("fee") or "0"))
            return {
                "close_reason":  "Liquidated" if is_liquidation else "Closed on exchange",
                "closing_price": Decimal(str(entry.get("closeAveragePrice") or "0")),
                "pnl_realized":  pnl + fee,
                "closed_at":     closed_at,
                "raw":           entry,
            }
        except Exception as e:
            logger.error(f"BlofinAdapter.get_closed_position_details failed: {e}")
            return None

    async def get_account_meta(self) -> dict:
        """Return api_key — non-sensitive without the secret/passphrase."""
        try:
            return {
                "api_key":      self.api_key,
                "account_type": "futures",
                "exchange":     "blofin",
            }
        except Exception as e:
            logger.error(f"BlofinAdapter.get_account_meta failed: {e}")
            return {}

    async def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """
        Return resting, non-trigger limit orders via /api/v1/trade/orders-pending.
        This endpoint only lists regular orders (TP/SL triggers live under
        orders-tpsl-pending / list_trigger_orders), so no extra filtering is needed.
        """
        try:
            path = "/api/v1/trade/orders-pending"
            if symbol:
                path += f"?instId={symbol}"
            headers = self._headers("GET", path, "")
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                resp = await client.get(path, headers=headers)
            data = resp.json()
            entries = data.get("data") or []

            result = []
            for o in entries:
                inst_id = o.get("instId")
                contracts = Decimal(str(o.get("size") or "0"))
                filled_contracts = Decimal(str(o.get("filledSize") or "0"))
                base_size = await self._to_base(inst_id, contracts)
                filled_base = await self._to_base(inst_id, filled_contracts)
                state = o.get("state")
                status = "partially_filled" if state == "partially_filled" else "resting"
                result.append({
                    "order_id":      o.get("orderId"),
                    "symbol":        inst_id,
                    "side":          o.get("side"),
                    "price":         float(o.get("price") or 0),
                    "size":          float(base_size),
                    "filled_size":   float(filled_base),
                    "status":        status,
                    "created_at_ms": int(o.get("createTime") or 0),
                })
            return result
        except Exception as e:
            logger.error(f"BlofinAdapter.get_open_orders failed: {e}")
            return []

    async def amend_order(
        self, symbol: str, order_id: str, new_price: float | None = None, new_size: float | None = None
    ) -> dict:
        """
        Amend a resting order's price/size.

        Blofin has NO native amend-order endpoint for perpetual (SWAP) orders — confirmed
        live: /api/v1/trade/amend-order, /modify-order, and a deliberately-bogus path all
        return the identical {"code":"152404","msg":"This operation is not supported"}, which
        indicates that's Blofin's generic "no such route" response, not a real amend rejection.
        Implemented as cancel-then-replace.

        FAILURE SEMANTICS: if the cancel succeeds but placing the replacement then fails
        (e.g. a transient API error), the original order is GONE — there is no restore path.
        In that case this returns {"success": False, "original_cancelled": True, ...} so the
        caller can detect a dropped order and re-place it explicitly. This is unlike a plain
        cancel_order failure, which never destroys state.
        """
        try:
            if new_price is None and new_size is None:
                return {"success": False, "error": "amend_order requires new_price or new_size"}

            existing = await self._get_order_state(symbol, order_id)
            if not existing:
                return {"success": False, "error": f"order {order_id} not found"}

            side = existing.get("side")
            leverage = int(float(existing.get("leverage") or 10))
            margin_mode = existing.get("marginMode") or "isolated"
            price = new_price if new_price is not None else float(existing.get("price") or 0)
            if new_size is not None:
                base_size = new_size
            else:
                contracts = Decimal(str(existing.get("size") or "0"))
                base_size = float(await self._to_base(symbol, contracts))

            cancel_result = await self.cancel_order(symbol, order_id)
            if not cancel_result.get("success"):
                return {
                    "success": False,
                    "error": f"cancel failed, original order unchanged: {cancel_result.get('error')}",
                }

            replacement = OrderRequest(
                order_id=f"amend-{order_id}",
                account_id="",
                symbol=symbol,
                side=side,
                signal="amend",
                order_type="limit",
                size=Decimal(str(base_size)),
                price=Decimal(str(price)),
                leverage=leverage,
                margin_mode=margin_mode,
            )
            result = await self.submit_order(replacement)
            if not result.success:
                return {
                    "success": False,
                    "original_cancelled": True,
                    "error": f"cancel succeeded but replacement failed — order is GONE: {result.error_msg}",
                    "raw_response": result.raw_response,
                }
            return {
                "success": True,
                "order_id": result.exchange_order_id,
                "cancelled_order_id": order_id,
                "raw_response": result.raw_response,
            }
        except Exception as e:
            logger.error(f"BlofinAdapter.amend_order failed: {e}")
            return {"success": False, "error": str(e)}

    async def list_trigger_orders(self, symbol: str) -> Optional[list[dict]]:
        """
        Return pending TP/SL orders for a symbol.
        Each entry: {oid, tpsl, triggerPx, sz}

        Returns None (not []) on a genuine exchange-call failure — callers must not
        treat None as "confirmed no trigger orders."
        """
        try:
            path = f"/api/v1/trade/orders-tpsl-pending?instId={symbol}"
            headers = self._headers("GET", path, "")
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                resp = await client.get(path, headers=headers)
            data = resp.json()
            entries = data.get("data") or []
            result = []
            for o in entries:
                tp_px = o.get("tpTriggerPrice") or o.get("tpTriggerPx")
                sl_px = o.get("slTriggerPrice") or o.get("slTriggerPx")
                order_id = o.get("tpslId")
                if tp_px:
                    result.append({"oid": order_id, "tpsl": "tp", "triggerPx": tp_px, "sz": o.get("size")})
                if sl_px:
                    result.append({"oid": order_id, "tpsl": "sl", "triggerPx": sl_px, "sz": o.get("size")})
            return result
        except Exception as e:
            logger.error(f"BlofinAdapter.list_trigger_orders failed: {e}")
            return None

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancel a pending TP/SL or regular order by its order_id."""
        try:
            # Try cancel-tpsl first (for TPSL orders placed via order-tpsl endpoint)
            path = "/api/v1/trade/cancel-tpsl"
            body_data = [{"instId": symbol, "tpslId": str(order_id)}]
            body_str = json.dumps(body_data, separators=(",", ":"))
            headers = self._headers("POST", path, body_str)
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                resp = await client.post(path, content=body_str, headers=headers)
            data = resp.json()
            code = str(data.get("code", "0"))
            if code in ("0", "200"):
                return {"success": True, "oid": order_id}
            # Fall back to cancel-order for regular orders
            path2 = "/api/v1/trade/cancel-order"
            body_data2 = {"instId": symbol, "orderId": str(order_id)}
            body_str2 = json.dumps(body_data2, separators=(",", ":"))
            headers2 = self._headers("POST", path2, body_str2)
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                resp2 = await client.post(path2, content=body_str2, headers=headers2)
            data2 = resp2.json()
            code2 = str(data2.get("code", "0"))
            if code2 in ("0", "200"):
                return {"success": True, "oid": order_id}
            return {"success": False, "error": data2.get("msg", "cancel failed")}
        except Exception as e:
            logger.error(f"BlofinAdapter.cancel_order failed: {e}")
            return {"success": False, "error": str(e)}

    async def place_trigger_orders(
        self,
        symbol: str,
        trigger_side: str,
        size: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
    ) -> dict:
        """Place standalone TP/SL orders for an existing position via order-tpsl endpoint."""
        try:
            contract_size = await self._to_contracts(symbol, Decimal(str(size)))
            placed = []

            for tpsl_type, price in [("tp", tp_price), ("sl", sl_price)]:
                if price is None:
                    continue
                price = await self._round_to_tick(symbol, price)
                body_data: dict = {
                    "instId":     symbol,
                    "marginMode": "isolated",
                    "side":       trigger_side,
                    "size":       str(contract_size),
                    "reduceOnly": "true",
                }
                # Blofin's order-tpsl endpoint is position-aware:
                # sl_price → slTriggerPrice fires on adverse move for either side
                # tp_price → tpTriggerPrice fires on favorable move for either side
                if tpsl_type == "sl":
                    body_data["slTriggerPrice"] = str(price)
                    body_data["slOrderPrice"]   = "-1"
                else:
                    body_data["tpTriggerPrice"] = str(price)
                    body_data["tpOrderPrice"]   = "-1"

                path = "/api/v1/trade/order-tpsl"
                body_str = json.dumps(body_data, separators=(",", ":"))
                headers  = self._headers("POST", path, body_str)
                async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
                    resp = await client.post(path, content=body_str, headers=headers)
                data = resp.json()
                code = str(data.get("code", "0"))
                if code in ("0", "200"):
                    order_info = data.get("data") or {}
                    if isinstance(order_info, list):
                        order_info = order_info[0] if order_info else {}
                    tpsl_id = order_info.get("tpslId")
                    placed.append({"tpsl": tpsl_type, "oid": tpsl_id, "status": "placed"})
                    logger.info(f"Blofin trigger ({tpsl_type}) placed at {price} for {symbol}, tpslId={tpsl_id}")
                else:
                    placed.append({"tpsl": tpsl_type, "error": data.get("msg")})
                    logger.warning(f"Blofin trigger ({tpsl_type}) failed: {data.get('msg')}")

            success = not any("error" in p for p in placed)
            if not success:
                logger.warning(
                    f"Blofin place_trigger_orders({symbol}) PARTIAL/FAILED: {placed}"
                )
            return {"success": success, "placed": placed}
        except Exception as e:
            logger.error(f"BlofinAdapter.place_trigger_orders failed: {e}")
            return {"success": False, "error": str(e)}
