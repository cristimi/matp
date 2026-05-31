"""
Main execute() handler.
Resolves account_id → adapter instance → submits order.
Retries up to 3 times with exponential backoff on transient failures.
Non-retryable errors (bad account, bad credentials) fail immediately.
"""
import asyncio
import logging
import json
from app.models import OrderRequest, OrderResult
from app.registry import registry
from app.database import get_pool

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]   # seconds between attempts


async def execute(request: OrderRequest) -> OrderResult:
    for attempt in range(MAX_RETRIES):
        try:
            adapter = await registry.get(request.account_id)
            result = await adapter.submit_order(request)
            
            # If submission failed due to transient error, retry
            # status="route_failed" usually means connection/transient error in adapter
            if not result.success and result.status == "route_failed" and attempt < MAX_RETRIES - 1:
                 raise Exception(result.error_msg)
            
            await _update_order_record(request.order_id, result)
            return result

        except ValueError as e:
            # Non-retryable: bad account_id, inactive account, bad credentials
            logger.error(
                f"Non-retryable error for order {request.order_id}: {e}"
            )
            result = OrderResult(
                success=False,
                status="route_failed",
                error_msg=str(e),
            )
            await _update_order_record(request.order_id, result)
            return result

        except Exception as e:
            logger.warning(
                f"Attempt {attempt + 1}/{MAX_RETRIES} failed "
                f"for order {request.order_id}: {e}"
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
            else:
                result = OrderResult(
                    success=False,
                    status="route_failed",
                    error_msg=f"All {MAX_RETRIES} attempts failed. Last error: {e}",
                )
                await _update_order_record(request.order_id, result)
                await _write_dead_letter(request.order_id, str(e))
                return result


async def _update_order_record(order_id: str, result: OrderResult):
    """Write executor result back to the orders table."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE orders SET
                    status            = $1,
                    exchange_order_id = $2,
                    raw_response      = $3::jsonb,
                    error_msg         = $4,
                    updated_at        = NOW()
                WHERE id = $5
                """,
                result.status,
                result.exchange_order_id,
                json.dumps(result.raw_response) if result.raw_response else None,
                result.error_msg,
                order_id,
            )
    except Exception as e:
        # Never let a DB write failure crash the executor
        logger.error(f"Failed to update order record {order_id}: {e}")


async def _write_dead_letter(order_id: str, reason: str):
    """Record exhausted-retry orders in dead_letter_orders."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO dead_letter_orders (order_id, reason)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                order_id,
                reason,
            )
    except Exception as e:
        logger.error(f"Failed to write dead letter for order {order_id}: {e}")
