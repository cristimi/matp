"""
Main execute() handler.
Resolves account_id → adapter instance → submits order.
Retries up to 3 times with exponential backoff on transient failures.
Non-retryable errors (bad account, bad credentials) fail immediately.
"""
import asyncio
import logging
import json
import uuid
from datetime import datetime, timezone
from app.models import OrderRequest, OrderResult
from app.registry import registry
from app.database import get_pool

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]   # seconds between attempts


async def _get_account_exchange(account_id: str) -> str:
    """Look up exchange name for an account. Returns 'unknown' on any error."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT exchange FROM exchange_accounts WHERE id = $1", account_id
            )
        return row["exchange"] if row else "unknown"
    except Exception:
        return "unknown"


async def execute(request: OrderRequest) -> OrderResult:
    exchange = await _get_account_exchange(request.account_id)

    for attempt in range(MAX_RETRIES):
        client_order_id = str(uuid.uuid4())
        oel_id = await _insert_execution_log(request, exchange, client_order_id)

        try:
            adapter = await registry.get(request.account_id)
            placed_at = datetime.now(timezone.utc)
            result = await adapter.submit_order(request)

            # If submission failed due to transient error, retry
            # status="route_failed" usually means connection/transient error in adapter
            if not result.success and result.status == "route_failed" and attempt < MAX_RETRIES - 1:
                await _update_execution_log(oel_id, result, placed_at)
                raise Exception(result.error_msg)

            await _update_execution_log(oel_id, result, placed_at)
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
            await _update_execution_log(oel_id, result, datetime.now(timezone.utc))
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
                await _update_execution_log(oel_id, result, datetime.now(timezone.utc))
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
                    status             = $1,
                    exchange_order_id  = $2,
                    raw_response       = $3::jsonb,
                    error_msg          = $4,
                    actual_fill_price  = $5,
                    pnl                = $6,
                    updated_at         = NOW()
                WHERE id = $7
                """,
                result.status,
                result.exchange_order_id,
                json.dumps(result.raw_response) if result.raw_response else None,
                result.error_msg,
                float(result.actual_fill_price) if result.actual_fill_price else None,
                float(result.realized_pnl) if result.realized_pnl else None,
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


async def _insert_execution_log(
    request: OrderRequest, exchange: str, client_order_id: str
) -> int | None:
    """Insert a new order_execution_log row. Returns the row id, or None on error."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            row_id = await conn.fetchval(
                """
                INSERT INTO order_execution_log (
                    signal_log_id, account_id, exchange, client_order_id,
                    symbol, side, order_type, requested_size, requested_price,
                    status, placed_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending', NOW())
                RETURNING id
                """,
                request.signal_log_id,
                request.account_id,
                exchange,
                client_order_id,
                request.symbol,
                request.side,
                request.order_type,
                float(request.size),
                float(request.price) if request.price else None,
            )
        return row_id
    except Exception as e:
        logger.error(f"Failed to insert execution log: {e}")
        return None


async def _update_execution_log(
    oel_id: int | None, result: OrderResult, placed_at: datetime
) -> None:
    """Update order_execution_log row with the final result."""
    if oel_id is None:
        return
    try:
        filled_at = datetime.now(timezone.utc) if result.success else None
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE order_execution_log SET
                    exchange_order_id = $2,
                    status            = $3,
                    error_message     = $4,
                    placed_at         = $5,
                    filled_at         = $6,
                    updated_at        = NOW()
                WHERE id = $1
                """,
                oel_id,
                result.exchange_order_id,
                result.status,
                result.error_msg,
                placed_at,
                filled_at,
            )
    except Exception as e:
        logger.error(f"Failed to update execution log {oel_id}: {e}")
