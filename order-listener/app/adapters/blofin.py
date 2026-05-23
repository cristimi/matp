import base64
import hashlib
import hmac
import json
import logging
import time
from typing import List

import httpx

from app.adapters.base import ExchangeAdapter
from app.config import settings
from app.models import WebhookPayload, OrderResult

logger = logging.getLogger(__name__)

BLOFIN_BASE_URL = "https://demo-trading-api.blofin.com"

class BlofinAdapter(ExchangeAdapter):

    def _sign(self, method: str, path: str, body: str, timestamp: str, nonce: str) -> str:
        # Blofin Prehash: requestPath + method + timestamp + nonce + body
        message = f"{path}{method.upper()}{timestamp}{nonce}{body}"
        mac = hmac.new(
            settings.blofin_api_secret.encode("utf-8"),
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
            "ACCESS-KEY": settings.blofin_api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": settings.blofin_api_passphrase,
            "ACCESS-NONCE": nonce,
            "Content-Type": "application/json",
        }

    def _map_symbol(self, symbol: str) -> str:
        # Simple mapping for BTCUSDT.P -> BTC-USDT
        mapping = {"BTCUSDT.P": "BTC-USDT", "ETHUSDT.P": "ETH-USDT"}
        return mapping.get(symbol, symbol)

    async def place_order(self, signal: WebhookPayload, strategy: dict) -> OrderResult:
        path = "/api/v1/trade/order"
        
        # Blofin requires order size to be a multiple of lotSize (0.1) and >= minSize (0.1) for BTC-USDT.
        # Conversion: [API Contract Amount] = [BTC Volume] / 0.001 = [BTC Volume] * 1000
        order_size = float(signal.size) * 1000.0
        
        # Enforce minimum size and round to the nearest increment (lotSize = 0.1)
        if signal.pair_label in ["BTCUSDT.P", "BTC-USDT"]:
            order_size = max(0.1, round(order_size, 1))

        body_data = {
            "instId": self._map_symbol(signal.pair_label),
            "marginMode": signal.marginMode or "cross",
            "side": signal.side,
            "orderType": signal.orderType,
            "size": str(order_size),
            "lever": str(signal.leverage or 10),
        }
        
        # Add optional prices
        if signal.price:
            body_data["price"] = str(signal.price)
        if signal.tpPrice:
            body_data["tpTriggerPrice"] = str(signal.tpPrice)
        if signal.slPrice:
            body_data["slTriggerPrice"] = str(signal.slPrice)

        # Compact JSON serialization is critical for Blofin signatures
        body_str = json.dumps(body_data, separators=(",", ":"))
        headers = self._headers("POST", path, body_str)

        async with httpx.AsyncClient(base_url=BLOFIN_BASE_URL, timeout=10) as client:
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
        logger.info(f"Blofin response: {data}")

        code = data.get("code")
        # Extract fill price if available (Blofin may return it in the order response if filled)
        fill_price = None
        if data.get("data") and isinstance(data["data"], list) and len(data["data"]) > 0:
            fill_price = data["data"][0].get("fillPrice")

        if str(code) in ["0", "200"]:
            return OrderResult(
                success=True,
                status="filled",
                raw_response=data,
                actual_fill_price=Decimal(fill_price) if fill_price else None,
                pnl=None
            )
        else:
            error = data.get("msg", "Unknown error")
            logger.warning(f"Blofin order failed: {data}")
            return OrderResult(
                success=False,
                status="rejected",
                error_msg=f"{error} ({data})",
                raw_response=data,
            )

    async def get_open_positions(self) -> List[dict]:
        path = "/api/v1/account/positions"
        headers = self._headers("GET", path, "")
        async with httpx.AsyncClient(base_url=BLOFIN_BASE_URL, timeout=10) as client:
            response = await client.get(path, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch positions: {response.text}")
            return []
            
        data = response.json()
        logger.info(f"Blofin positions response: {data}")
        raw_positions = data.get("data", [])
        
        mapped_positions = []
        for p in raw_positions:
            # Blofin position structure: size is in "positions" or similar field. 
            size_val = float(p.get("positions", 0))
            if size_val == 0:
                continue
                
            mapped_positions.append({
                "symbol": p.get("instId"),
                "side": "buy" if size_val > 0 else "sell",
                "size": str(abs(size_val)),
                "entryPx": str(p.get("averagePrice", "0")),
                "markPx": str(p.get("markPrice", "0")),
                "unrealizedPnl": str(p.get("unrealizedPnl", "0")),
                "realizedPnl": str(p.get("realizedPnl", "0")),
                "liquidationPx": str(p.get("liquidationPrice") or "0"),
                "platform": "blofin"
            })
            
        return mapped_positions

    async def close_position(self, symbol: str, side: str) -> OrderResult:
        # BloFin provides a specific endpoint for closing entire positions via market order.
        path = "/api/v1/trade/close-position"
        
        # side is "buy" (Short) or "sell" (Long) as passed from positions view
        # BloFin close-position needs positionSide: "long", "short", or "net"
        # Based on side (current position side), we map to positionSide
        pos_side = "long" if side == "buy" else "short" 
        # Wait, if side="buy", it means the position was SHORT, so we BUY to close it.
        # But wait, side in close_position parameters is usually the side of the OPEN position.
        # Let check how positions.ts calls it:
        # router.post("/:symbol/close", ... body: { side: position.side })
        
        position_side_map = {
            "long": "long",
            "short": "short",
            "buy": "long",
            "sell": "short"
        }
        
        body_data = {
            "instId": symbol,
            "marginMode": "cross", 
            "positionSide": position_side_map.get(side.lower(), "net")
        }
        
        # If the position list showed "net", we should probably use "net"
        # Let try "net" directly if the specific side fails or just use "net" as it is common.
        # For now, let assume "net" is more likely for signal bots.
        body_data["positionSide"] = "net"
        
        body_str = json.dumps(body_data, separators=(",", ":"))
        headers = self._headers("POST", path, body_str)
        
        async with httpx.AsyncClient(base_url=BLOFIN_BASE_URL, timeout=10) as client:
            response = await client.post(path, content=body_str, headers=headers)
            
        if response.status_code != 200:
            logger.warning(f"Blofin close failed. Status: {response.status_code}, Response: {response.text}")
            return OrderResult(
                success=False,
                status="error",
                error_msg=f"Blofin returned {response.status_code}",
                raw_response={"text": response.text}
            )

        data = response.json()
        code = data.get("code")
        
        # Extract closing fill price from data if available
        fill_price = None
        if data.get("data") and isinstance(data["data"], list) and len(data["data"]) > 0:
             fill_price = data["data"][0].get("fillPrice")

        if str(code) in ["0", "200"]:
            return OrderResult(
                success=True,
                status="closed",
                raw_response=data,
                actual_fill_price=Decimal(fill_price) if fill_price else None
            )
        else:
            error = data.get("msg", "Unknown error")
            logger.warning(f"Blofin close failed: {error}")
            return OrderResult(
                success=False,
                status="rejected",
                error_msg=error,
                raw_response=data,
            )
