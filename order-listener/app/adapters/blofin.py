"""
Blofin Signal Bot exchange adapter.
Authenticates with API key + HMAC-SHA256 signature.
"""

import hashlib
import hmac
import logging
import time
from typing import List

import httpx

from app.adapters.base import ExchangeAdapter
from app.config import settings
from app.models import WebhookPayload, OrderResult

logger = logging.getLogger(__name__)

BLOFIN_BASE_URL = "https://openapi.blofin.com"


class BlofinAdapter(ExchangeAdapter):

    def _sign(self, method: str, path: str, body: str, timestamp: str) -> str:
        message = f"{timestamp}{method}{path}{body}"
        return hmac.new(
            settings.blofin_api_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self, method: str, path: str, body: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        signature = self._sign(method, path, body, timestamp)
        return {
            "ACCESS-KEY": settings.blofin_api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    async def place_order(self, signal: WebhookPayload) -> OrderResult:
        path = "/api/v1/trade/order"
        body_data = {
            "instId": signal.symbol,
            "marginMode": signal.marginMode or "cross",
            "side": signal.side,
            "orderType": signal.orderType,
            "size": str(signal.size),
            "lever": str(signal.leverage or 10),
        }
        if signal.price:
            body_data["price"] = str(signal.price)
        if signal.tpPrice:
            body_data["tpTriggerPrice"] = str(signal.tpPrice)
        if signal.slPrice:
            body_data["slTriggerPrice"] = str(signal.slPrice)

        import json
        body_str = json.dumps(body_data)
        headers = self._headers("POST", path, body_str)

        async with httpx.AsyncClient(base_url=BLOFIN_BASE_URL, timeout=10) as client:
            response = await client.post(path, content=body_str, headers=headers)

        data = response.json()
        logger.debug(f"Blofin response: {data}")

        if response.status_code == 200 and data.get("code") == "0":
            order_info = data.get("data", [{}])[0]
            return OrderResult(
                success=True,
                exchange_order_id=order_info.get("ordId"),
                status="filled",
                raw_response=data,
            )
        else:
            error = data.get("msg", "Unknown error")
            logger.warning(f"Blofin order failed: {error}")
            return OrderResult(
                success=False,
                status="rejected",
                error_msg=error,
                raw_response=data,
            )

    async def get_open_positions(self) -> List[dict]:
        path = "/api/v1/account/positions"
        headers = self._headers("GET", path, "")
        async with httpx.AsyncClient(base_url=BLOFIN_BASE_URL, timeout=10) as client:
            response = await client.get(path, headers=headers)
        data = response.json()
        return data.get("data", [])

    async def close_position(self, symbol: str, side: str) -> OrderResult:
        close_side = "sell" if side == "buy" else "buy"
        signal_mock = WebhookPayload(
            symbol=symbol,
            side=close_side,
            orderType="market",
            size=0,  # Blofin close uses closePosition flag
            platform="blofin",
            signal="close_long" if side == "buy" else "close_short",
            timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            token="internal",
        )
        # For a real close, Blofin needs reduceOnly or closePosition param
        # This is a placeholder — extend with Blofin's actual close API
        return await self.place_order(signal_mock)
