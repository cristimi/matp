"""
HTTP client for calling order-executor:8004/execute.

The listener calls this synchronously (awaits the result) so it can
classify the outcome as filled / route_failed / lag_failed before
returning 200 to TradingView and writing the final status to the DB.
"""
import logging
import os
import httpx

logger = logging.getLogger(__name__)

EXECUTOR_URL = os.environ.get("EXECUTOR_URL", "http://order-executor:8004")
TIMEOUT_SECONDS = 30  # exchange calls can be slow; TradingView waits up to 60s


async def call_executor(order_request: dict) -> dict:
    """
    POST the OrderRequest payload to /execute.
    Returns the OrderResult as a dict.

    Never raises — catches all network and HTTP errors and returns a
    route_failed result so the caller always gets a usable dict back.
    """
    url = f"{EXECUTOR_URL}/execute"
    order_id = order_request.get("order_id", "unknown")
    logger.info(f"Calling executor for order {order_id}: {url}")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=order_request)
            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException as e:
        logger.error(f"Executor timeout for order {order_id}: {e}")
        return {
            "success": False,
            "status": "route_failed",
            "error_msg": f"Executor timeout after {TIMEOUT_SECONDS}s",
            "exchange_order_id": None,
            "raw_response": None,
        }

    except httpx.HTTPStatusError as e:
        logger.error(
            f"Executor HTTP error for order {order_id}: "
            f"{e.response.status_code} {e.response.text}"
        )
        return {
            "success": False,
            "status": "route_failed",
            "error_msg": f"Executor returned HTTP {e.response.status_code}",
            "exchange_order_id": None,
            "raw_response": None,
        }

    except Exception as e:
        logger.error(f"Executor call failed for order {order_id}: {e}")
        return {
            "success": False,
            "status": "route_failed",
            "error_msg": str(e),
            "exchange_order_id": None,
            "raw_response": None,
        }


async def call_executor_close_position(
    account_id: str,
    symbol:     str,
    side:       str,
    size=None,
) -> dict:
    """
    POST to /close-position to close or partially close an open position.
    size=None means full close; Decimal/str size means partial reduce-only.
    Returns the OrderResult dict. Never raises.
    """
    url = f"{EXECUTOR_URL}/close-position"
    logger.info(
        f"Calling executor close-position: account={account_id} "
        f"symbol={symbol} side={side} size={size}"
    )

    body: dict = {"account_id": account_id, "symbol": symbol, "side": side}
    if size is not None:
        body["size"] = str(size)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException as e:
        logger.error(f"Executor close-position timeout: {e}")
        return {
            "success": False,
            "status":  "route_failed",
            "error_msg": f"Executor timeout after {TIMEOUT_SECONDS}s",
        }

    except Exception as e:
        logger.error(f"Executor close-position failed: {e}")
        return {
            "success": False,
            "status":  "route_failed",
            "error_msg": str(e),
        }
