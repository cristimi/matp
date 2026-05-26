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
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import JSONResponse

from app.database import get_pool
from app.models import WebhookPayload, OrderResponse, OrderResult
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


async def _log_order(pool, payload: WebhookPayload, order_id: uuid.UUID, strategy_id: str, pair_id: int, symbol: str) -> None:
    """Write initial order record to PostgreSQL."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO orders (
                id, received_at, pair_id, symbol, side, signal, order_type, size, price,
                leverage, margin_mode, tp_price, sl_price, platform, strategy_id,
                status, raw_webhook, signal_source, signal_metadata, indicator_price
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13, $14, $15,
                'received', $16, $17, $18, $19
            )
            """,
            order_id,
            datetime.now(timezone.utc),
            pair_id,
            symbol,
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
            payload.signal_source,
            json.dumps(payload.signal_metadata),
            payload.indicator_price
        )

async def _update_order_status(pool, order_id: uuid.UUID, status: str, payload: WebhookPayload, result: OrderResult = None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE orders SET status = $1,
                exchange_order_id = $2,
                raw_response = $3,
                error_msg = $4,
                actual_fill_price = $6,
                pnl = $7
            WHERE id = $5
            """,
            status,
            result.exchange_order_id if result else None,
            json.dumps(result.raw_response) if result and result.raw_response else None,
            result.error_msg if result else None,
            order_id,
            result.actual_fill_price if result else None,
            result.pnl if hasattr(result, 'pnl') else None,
        )

    # Publish status update to Redis
    await publish(f"orders:{status}", {
        "event": f"orders:{status}",
        "order_id": str(order_id),
        "status": status,
        "pair": payload.pair_label,
        "platform": payload.platform,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@router.post("/webhook/{strategy_id}", response_model=OrderResponse)
async def receive_webhook(
    strategy_id: str, 
    request: Request,
    x_webhook_token: str = Header(None)
):
    body = await request.body()
    payload = WebhookPayload(**json.loads(body))
    pool = get_pool()
    
    # 1. Load strategy
    strategy = await _get_strategy(pool, strategy_id)
    if not strategy:
        logger.warning(f"Rejected webhook: unknown strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 404, "Strategy not found")
        raise HTTPException(status_code=404, detail="Strategy not found")

    # 2. Authenticate (Check Header, signalToken, or old token)
    token_to_verify = x_webhook_token or payload.signalToken or payload.token
    if not token_to_verify or not await _verify_token(token_to_verify, strategy['webhook_secret']):
        logger.warning(f"Rejected webhook: invalid token for strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 403, "Invalid token")
        raise HTTPException(status_code=403, detail="Invalid token")

    # 3. Lag Check
    lag_exceeded = False
    if payload.maxLag:
        now = datetime.now(timezone.utc)
        ts = payload.timestamp if payload.timestamp.tzinfo else payload.timestamp.replace(tzinfo=timezone.utc)
        if (now - ts).total_seconds() > payload.maxLag:
            logger.warning(f"Webhook signal lag exceeded for strategy={strategy_id}")
            lag_exceeded = True

    # 4. Check enabled
    if not strategy['webhook_enabled']:
        logger.warning(f"Rejected webhook: disabled strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 403, "Strategy disabled")
        raise HTTPException(status_code=403, detail="Strategy disabled")

    # 5. Rate limit check
    if not await _check_rate_limit(pool, strategy_id, strategy['max_daily_signals']):
        logger.warning(f"Rejected webhook: rate limit exceeded for strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 429, "Rate limit exceeded")
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    payload.strategy_id = strategy_id
    
    # Resolve platform immediately (never persist 'auto')
    if payload.platform == "auto":
        from app.router import _get_active_platform
        payload.platform = strategy.get('platform_override') or await _get_active_platform()
    
    # Map TV Action/Signal to MATP Side/Signal
    if payload.action and not payload.side:
        payload.side = payload.action.lower()
    if not payload.signal and payload.action:
        payload.signal = payload.action
    
    # Map TV Instrument/Amount or internal symbol to MATP Base/Quote and Size
    from app.symbol_factory import SymbolFactory
    
    target_symbol = payload.symbol or payload.instrument
    if target_symbol and not payload.base_asset:
        try:
            payload.base_asset, payload.quote_asset = SymbolFactory.split(target_symbol)
        except Exception as e:
            logger.warning(f"Failed to split symbol {target_symbol}: {e}")

    if payload.amount and payload.size is None:
        payload.size = payload.amount
    
    order_id = uuid.uuid4()
    
    # Persist initial record
    await _log_webhook_call(pool, strategy_id, 200)
    
    # Lookup pair_id
    async with pool.acquire() as conn:
        pair = await conn.fetchrow(
            "SELECT tp.id FROM trading_pairs tp JOIN assets b ON tp.base_asset_id = b.id JOIN assets q ON tp.quote_asset_id = q.id WHERE b.symbol = $1 AND q.symbol = $2",
            payload.base_asset, payload.quote_asset
        )
    pair_id = pair['id'] if pair else None

    
    symbol_str = f"{payload.base_asset}-{payload.quote_asset}"
    
    await _log_order(pool, payload, order_id, strategy_id, pair_id, symbol_str)

    # Publish initial 'received' status to Redis
    await publish("orders:received", {
        "event": "orders:received",
        "order_id": str(order_id),
        "status": "received",
        "pair": payload.pair_label,
        "platform": payload.platform,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    if lag_exceeded:
        # Mark as failed due to lag and stop
        await _update_order_status(pool, order_id, "lag_failed", payload, type('obj', (object,), {'error_msg': 'Max lag exceeded', 'exchange_order_id': None, 'raw_response': None, 'actual_fill_price': None}))
        return OrderResponse(order_id=order_id, status="received", message="Lag exceeded, signal logged as failed")

    # Trigger processing
    asyncio.create_task(_process_order(pool, order_id, payload, strategy))

    return OrderResponse(order_id=order_id, status="received", message="OK")


async def _create_strategy_position(pool, payload: WebhookPayload, strategy: dict, opening_order_id: uuid.UUID, result: OrderResult) -> None:
    """Create a new strategy position record in the database."""
    async with pool.acquire() as conn:
        entry_price = result.actual_fill_price or payload.price or payload.indicator_price or 0
        
        # Look up pair_id
        pair = await conn.fetchrow(
            "SELECT tp.id FROM trading_pairs tp JOIN assets b ON tp.base_asset_id = b.id JOIN assets q ON tp.quote_asset_id = q.id WHERE b.symbol = $1 AND q.symbol = $2",
            payload.base_asset, payload.quote_asset
        )
        pair_id = pair['id'] if pair else None

        await conn.execute(
            """
            INSERT INTO strategy_positions (
                strategy_id, exchange, symbol, pair_id, side, entry_price, current_price, size,
                leverage, margin_mode, opening_order_id, status, opened_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $6, $7,
                $8, $9, $10, 'open', NOW()
            )
            """,
            payload.strategy_id,
            payload.platform,
            payload.pair_label,
            pair_id,
            payload.side,
            entry_price,
            payload.size,
            payload.leverage,
            payload.marginMode,
            opening_order_id,
        )


async def _process_order(pool, order_id: uuid.UUID, payload: WebhookPayload, strategy: dict):
    try:
        # Route
        result = await route_order(payload, strategy)
        final_status = "filled" if result.success else "route_failed"
        await _update_order_status(pool, order_id, final_status, payload, result)

        logger.info(f"Order {order_id} processed for strategy {payload.strategy_id}: {final_status}")

        if result.success and payload.base_asset and payload.quote_asset and payload.side and payload.size:
            # Ensure strategy_id is correctly set on payload
            payload.strategy_id = strategy['id']
            
            async with pool.acquire() as conn:
                # Lookup pair_id
                pair = await conn.fetchrow(
                    "SELECT tp.id FROM trading_pairs tp JOIN assets b ON tp.base_asset_id = b.id JOIN assets q ON tp.quote_asset_id = q.id WHERE b.symbol = $1 AND q.symbol = $2",
                    payload.base_asset, payload.quote_asset
                )
                pair_id = pair['id'] if pair else None

                # Check for existing open position
                existing = await conn.fetchrow(
                    "SELECT sp.id, sp.size FROM strategy_positions sp WHERE strategy_id = $1 AND pair_id = $2 AND status = 'open'",
                    payload.strategy_id, pair_id
                )
                
                if existing:
                    current_pos_size = float(existing['size'])
                    order_size = float(payload.size)
                    
                    if order_size >= current_pos_size:
                        # Update position status to closed
                        await conn.execute(
                            "UPDATE strategy_positions SET status = 'closed', closed_at = NOW(), closing_order_id = $1 WHERE id = $2",
                            order_id, existing['id']
                        )
                        logger.info(f"Position {existing['id']} closed for order {order_id}")
                    else:
                        # Reduce position size
                        new_size = current_pos_size - order_size
                        await conn.execute(
                            "UPDATE strategy_positions SET size = $1 WHERE id = $2",
                            new_size, existing['id']
                        )
                        logger.info(f"Position {existing['id']} reduced by {order_size} for order {order_id}. New size: {new_size}")
                else:
                    # Only create a strategy position if the order was successful
                    await _create_strategy_position(pool, payload, strategy, order_id, result)

    except Exception as e:
        logger.exception(f"Error processing order {order_id}: {e}")
        await _update_order_status(pool, order_id, "route_failed", payload, None)
