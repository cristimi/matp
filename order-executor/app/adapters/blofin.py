import base64
import hashlib
import hmac
import json
import logging
import time
import asyncio
from decimal import Decimal
from typing import Dict, List, Optional

import httpx

from app.adapters.base import ExchangeAdapter
from app.models import OrderRequest, OrderResult, Position

logger = logging.getLogger(__name__)

_INSTRUMENTS_TTL = 86400  # 24 hours

class BlofinAdapter(ExchangeAdapter):
    # Class-level cache shared across all instances, keyed by base_url
    _instruments: Dict[str, Dict[str, dict]] = {}   # base_url -> instId -> spec
    _instruments_ts: Dict[str, float]        = {}   # base_url -> fetch timestamp

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

            # Blofin ignores the lever field in order placement; must set it explicitly first
            await self._set_leverage(order.symbol, leverage, margin_mode)

            path = "/api/v1/trade/order"
            body_data = {
                "instId": order.symbol,
                "marginMode": margin_mode,
                "side": order.side,
                "orderType": order.order_type,
                "size": str(order_size),
                "lever": str(leverage),
            }
            
            if order.price:
                body_data["price"] = str(order.price)
            if order.tp_price:
                body_data["tpTriggerPrice"] = str(order.tp_price)
            if order.sl_price:
                body_data["slTriggerPrice"] = str(order.sl_price)

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
                
                # For market orders, wait a bit and fetch details to get fill price and P&L
                actual_fill_price = None
                pnl = None
                if order.order_type == "market":
                    try:
                        await asyncio.sleep(2.0)
                        details = await self._get_order_details(order.symbol, exchange_order_id)
                        if details:
                            actual_fill_price = self._parse_fill_price(details)
                            pnl_raw = details.get("pnl") or details.get("realizedPnl")
                            pnl = Decimal(str(pnl_raw)) if pnl_raw is not None else None
                    except Exception as e:
                        logger.warning(f"Blofin submit_order: fill details fetch failed (order still filled): {e}")

                return OrderResult(
                    success=True,
                    status="filled",
                    exchange_order_id=exchange_order_id,
                    raw_response=data,
                    actual_fill_price=actual_fill_price,
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
            return []
            
        data = response.json()
        raw_positions = data.get("data", [])
        
        mapped_positions = []
        for p in raw_positions:
            size_val = float(p.get("positions", 0))
            if size_val == 0:
                continue
                
            mark_raw = p.get("markPrice") or p.get("last") or p.get("averagePrice", "0")
            mapped_positions.append(Position(
                symbol=p.get("instId"),
                side="long" if size_val > 0 else "short",
                size=Decimal(str(abs(size_val))),
                entry_price=Decimal(p.get("averagePrice", "0")),
                leverage=int(p.get("lever") or p.get("leverage") or 10),
                mark_price=Decimal(str(mark_raw)),
                unrealized_pnl=Decimal(p.get("unrealizedPnl", "0"))
            ))
            
        return mapped_positions

    async def close_position(self, symbol: str, side: str, margin_mode: str = "isolated") -> OrderResult:
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

    async def list_instruments(self) -> list[str]:
        """Return all SWAP instrument IDs available on this Blofin endpoint."""
        try:
            await self._get_instrument("")   # ensures cache is populated
            return sorted(BlofinAdapter._instruments.get(self.base_url, {}).keys())
        except Exception as e:
            logger.error(f"BlofinAdapter.list_instruments failed: {e}")
            return []

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
