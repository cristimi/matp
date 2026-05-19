"""
Webhook reception, authentication, validation, logging, and routing.
Now supports strategy-specific endpoints: /webhook/{strategy_id}
"""

import hmac
import json
import logging
import uuid
import asyncio
from datetime import datetime, timezone, date

from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import JSONResponse

from app.database import get_pool
from app.models import WebhookPayload, OrderResponse
from app.redis_client import publish
from app.router import route_order

logger = logging.getLogger(__name__)
router = APIRouter()


async def _verify_token(token: str, secret: str) -> bool:
    """Constant-time token comparison to prevent timing attacks."""
    return hmac.compare_digest(token.encode(), secret.encode())


async def _log_webhook_call(pool, strategy_id: str, http_status: int, error_message: str | None = None) -> None:
    """Log webhook call attempt to the database."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategy_webhook_calls (strategy_id, http_status, error_message)
            VALUES ($1, $2, $3)
            """,
            strategy_id, http_status, error_message
        )


async def _get_strategy(pool, strategy_id: str):
    """Retrieve strategy configuration from the database."""
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM strategies WHERE id = $1", strategy_id)


async def _check_rate_limit(pool, strategy_id: str, max_signals: int) -> bool:
    """Check if the strategy has exceeded its daily signal limit."""
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM strategy_webhook_calls 
            WHERE strategy_id = $1 
            AND http_status = 200 
            AND received_at >= $2
            """,
            strategy_id,
            datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
        )
        return count < max_signals


async def _log_order(pool, payload: WebhookPayload, order_id: uuid.UUID, strategy_id: str) -> None:
    """Write initial order record to PostgreSQL."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO orders (
                id, received_at, symbol, side, signal, order_type, size, price,
                leverage, margin_mode, tp_price, sl_price, platform, strategy_id,
                status, raw_webhook
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13, $14,
                'received', $15
            )
            """,
            order_id,
            datetime.now(timezone.utc),
            payload.symbol,
            payload.side,
            payload.signal,
            payload.orderType,
            payload.size,
            payload.price,
            payload.leverage,
            payload.marginMode,
            payload.tpPrice,
            payload.slPrice,
            payload.platform,
            strategy_id,
            json.dumps(payload.model_dump(mode="json", exclude={"token"})),
        )


async def _update_order_status(pool, order_id: uuid.UUID, status: str, result=None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE orders SET status = $1,
                exchange_order_id = $2,
                raw_response = $3,
                error_msg = $4
            WHERE id = $5
            """,
            status,
            result.exchange_order_id if result else None,
            json.dumps(result.raw_response) if result and result.raw_response else None,
            result.error_msg if result else None,
            order_id,
        )


@router.post("/webhook/{strategy_id}", response_model=OrderResponse)
async def receive_webhook(
    strategy_id: str, 
    payload: WebhookPayload, 
    x_webhook_token: str = Header(None)
):
    pool = get_pool()
    
    # 1. Load strategy
    strategy = await _get_strategy(pool, strategy_id)
    if not strategy:
        logger.warning(f"Rejected webhook: unknown strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 404, "Strategy not found")
        raise HTTPException(status_code=404, detail="Strategy not found")

    # 2. Authenticate
    if not x_webhook_token or not await _verify_token(x_webhook_token, strategy['webhook_secret']):
        logger.warning(f"Rejected webhook: invalid token for strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 403, "Invalid token")
        raise HTTPException(status_code=403, detail="Invalid token")

    # 3. Check enabled
    if not strategy['webhook_enabled']:
        logger.warning(f"Rejected webhook: disabled strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 403, "Strategy disabled")
        raise HTTPException(status_code=403, detail="Strategy disabled")

    # 4. Rate limit check
    if not await _check_rate_limit(pool, strategy_id, strategy['max_daily_signals']):
        logger.warning(f"Rejected webhook: rate limit exceeded for strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 429, "Rate limit exceeded")
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Enrichment
    payload.strategy_id = strategy_id
    if payload.platform == "auto" and strategy['platform_override']:
        payload.platform = strategy['platform_override']

    order_id = uuid.uuid4()
    
    # Persist initial record
    await _log_webhook_call(pool, strategy_id, 200)
    await _log_order(pool, payload, order_id, strategy_id)

    # Trigger processing
    asyncio.create_task(_process_order(pool, order_id, payload))

    return OrderResponse(order_id=order_id, status="received", message="OK")


async def _process_order(pool, order_id: uuid.UUID, payload: WebhookPayload):
    try:
        # Route
        result = await route_order(payload)
        final_status = "filled" if result.success else "route_failed"
        await _update_order_status(pool, order_id, final_status, result)
        
        logger.info(f"Order {order_id} processed for strategy {payload.strategy_id}: {final_status}")
    except Exception as e:
        logger.exception(f"Error processing order {order_id}: {e}")
        await _update_order_status(pool, order_id, "route_failed", None)
