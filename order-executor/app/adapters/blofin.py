import base64
import hashlib
import hmac
import json
import logging
import time
import asyncio
from decimal import Decimal
from typing import List

import httpx

from app.adapters.base import ExchangeAdapter
from app.models import OrderRequest, OrderResult, Position

logger = logging.getLogger(__name__)

class BlofinAdapter(ExchangeAdapter):
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

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """
        Implements ExchangeAdapter.submit_order.
        Wraps the existing place_order / create_order logic.
        """
        try:
            # Blofin requires order size to be a multiple of lotSize (0.1) and >= minSize (0.1) for BTC-USDT.
            # Conversion: [API Contract Amount] = [BTC Volume] / 0.001 = [BTC Volume] * 1000
            # For now keeping the 1000x multiplier as it was in the listener's adapter
            order_size = float(order.size) * 1000.0
            
            # Simple normalization for BTC
            if "BTC" in order.symbol:
                order_size = max(0.1, round(order_size, 1))

            path = "/api/v1/trade/order"
            body_data = {
                "instId": order.symbol,
                "marginMode": order.margin_mode or "cross",
                "side": order.side,
                "orderType": order.order_type,
                "size": str(order_size),
                "lever": str(order.leverage or 10),
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
                    await asyncio.sleep(0.5)
                    details = await self._get_order_details(order.symbol, exchange_order_id)
                    if details:
                        actual_fill_price = self._parse_fill_price(details)
                        pnl_raw = details.get("realizedPnl") or details.get("pnl")
                        pnl = Decimal(str(pnl_raw)) if pnl_raw else None

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
                leverage=int(p.get("leverage", 10)),
                mark_price=Decimal(str(mark_raw)),
                unrealized_pnl=Decimal(p.get("unrealizedPnl", "0"))
            ))
            
        return mapped_positions

    async def close_position(self, symbol: str, side: str) -> OrderResult:
        path = "/api/v1/trade/close-position"
        
        body_data = {
            "instId": symbol,
            "marginMode": "cross", 
            "positionSide": "net"
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
        
        if str(code) in ["0", "200"] and data.get("data") and isinstance(data["data"], list) and len(data["data"]) > 0:
            order_info = data["data"][0]
            exchange_order_id = order_info.get("orderId")
            
            await asyncio.sleep(0.5)
            details = await self._get_order_details(symbol, exchange_order_id)
            fill_price = None
            pnl = None
            if details:
                fill_price = self._parse_fill_price(details)
                pnl_raw = details.get("realizedPnl") or details.get("pnl")
                pnl = Decimal(str(pnl_raw)) if pnl_raw else None

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
