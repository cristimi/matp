"""
Webhook reception, authentication, validation, logging, and routing.
"""

import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import get_pool
from app.models import WebhookPayload, OrderResponse
from app.redis_client import publish
from app.router import route_order

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_token(token: str) -> bool:
    """Constant-time token comparison to prevent timing attacks."""
    return hmac.compare_digest(token.encode(), settings.webhook_secret.encode())


async def _log_order(pool, payload: WebhookPayload, order_id: uuid.UUID) -> None:
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
            payload.strategyId,
            json.dumps(payload.model_dump(mode="json", exclude={"token"})),
        )
        await conn.execute(
            """
            INSERT INTO order_events (order_id, from_status, to_status, message)
            VALUES ($1, NULL, 'received', 'Webhook received and validated')
            """,
            order_id,
        )


async def _update_order_status(
    pool, order_id: uuid.UUID, status: str, result=None
) -> None:
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
        await conn.execute(
            """
            INSERT INTO order_events (order_id, from_status, to_status)
            VALUES ($1, 'routing', $2)
            """,
            order_id,
            status,
        )


@router.post("/webhook", response_model=OrderResponse)
async def receive_webhook(payload: WebhookPayload):
    # 1. Authenticate
    if not _verify_token(payload.token):
        logger.warning(f"Rejected webhook: invalid token for symbol={payload.symbol}")
        raise HTTPException(status_code=403, detail="Invalid token")

    order_id = uuid.uuid4()
    pool = get_pool()

    # 2. Persist initial record
    await _log_order(pool, payload, order_id)

    # 3. Publish received event
    await publish("orders:received", {
        "event": "order:received",
        "order_id": str(order_id),
        "symbol": payload.symbol,
        "side": payload.side,
        "signal": payload.signal,
        "platform": payload.platform,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    logger.info(f"Order {order_id} received: {payload.symbol} {payload.signal}")

    # 4. Route asynchronously (fire and forget — status updates happen inside)
    import asyncio
    asyncio.create_task(_process_order(pool, order_id, payload))

    return OrderResponse(order_id=order_id, status="received", message="OK")


async def _process_order(pool, order_id: uuid.UUID, payload: WebhookPayload):
    """Route the order to the appropriate exchange adapter and update status."""
    try:
        # Update to routing
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE orders SET status = 'routing' WHERE id = $1", order_id
            )
            await conn.execute(
                """INSERT INTO order_events (order_id, from_status, to_status)
                   VALUES ($1, 'received', 'routing')""",
                order_id,
            )

        await publish("orders:routed", {
            "event": "order:routing",
            "order_id": str(order_id),
            "symbol": payload.symbol,
            "platform": payload.platform,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        result = await route_order(payload)

        final_status = "filled" if result.success else "route_failed"
        await _update_order_status(pool, order_id, final_status, result)

        channel = "orders:filled" if result.success else "orders:failed"
        await publish(channel, {
            "event": f"order:{final_status}",
            "order_id": str(order_id),
            "status": final_status,
            "symbol": payload.symbol,
            "platform": payload.platform,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if not result.success:
            await _write_dead_letter(pool, order_id, result.error_msg)

        logger.info(f"Order {order_id} → {final_status}")

    except Exception as e:
        logger.exception(f"Unexpected error processing order {order_id}: {e}")
        await _update_order_status(pool, order_id, "route_failed", None)
        await _write_dead_letter(pool, order_id, str(e))


async def _write_dead_letter(pool, order_id: uuid.UUID, reason: str | None):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO dead_letter_orders (order_id, reason)
               VALUES ($1, $2)
               ON CONFLICT DO NOTHING""",
            order_id,
            reason,
        )
