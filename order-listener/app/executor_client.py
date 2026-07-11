"""
HTTP client for calling order-executor:8004/execute.

The listener calls this synchronously (awaits the result) so it can
classify the outcome as filled / route_failed / lag_failed before
returning 200 to TradingView and writing the final status to the DB.
"""
import logging
import os
import time
from decimal import Decimal
from typing import Optional

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

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=order_request)
            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            f"Executor timeout for order {order_id} after {elapsed_ms}ms: "
            f"{type(e).__name__}: {e!r}"
        )
        return {
            "success": False,
            "status": "route_failed",
            "error_msg": f"Executor timeout after {TIMEOUT_SECONDS}s",
            "exchange_order_id": None,
            "raw_response": None,
        }

    except httpx.HTTPStatusError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            f"Executor HTTP error for order {order_id} after {elapsed_ms}ms: "
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
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            f"Executor call failed for order {order_id} after {elapsed_ms}ms: "
            f"{type(e).__name__}: {e!r}"
        )
        return {
            "success": False,
            "status": "route_failed",
            "error_msg": str(e),
            "exchange_order_id": None,
            "raw_response": None,
        }


async def call_executor_get(path: str) -> dict:
    """
    GET request to the executor at the given path.
    Returns the JSON response dict or {} on any error. Never raises.
    """
    url = f"{EXECUTOR_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.warning(f"Executor GET {path} failed: {e}")
        return {}


async def get_mark_price(account_id: str, symbol: str) -> Optional[float]:
    """
    Fetch the exchange mark price for `symbol` on `account_id`.
    Returns None if unavailable (network error, no data). Never raises.
    """
    data = await call_executor_get(f"/accounts/{account_id}/mark-price/{symbol}")
    mp = data.get("mark_price")
    return float(mp) if mp is not None else None


async def get_maintenance_margin(
    account_id: str, symbol: str, notional: float, margin_mode: str = "isolated"
) -> Optional[float]:
    """
    Fetch the live, tier-aware maintenance-margin rate for `symbol` at the given
    position notional (quote currency) from the executor.
    Returns None if unavailable (network error, no data, or exchange couldn't derive
    one). Never raises. Callers MUST fall back to a conservative static value — see
    app.config.fallback_mmr — not treat None as 0.
    """
    data = await call_executor_get(
        f"/accounts/{account_id}/maintenance-margin/{symbol}"
        f"?notional={notional}&margin_mode={margin_mode}"
    )
    mmr = data.get("maintenance_margin_rate")
    return float(mmr) if mmr is not None else None


async def get_trigger_orders(account_id: str, symbol: str) -> Optional[list]:
    """
    Fetch resting TP/SL trigger orders for a symbol from the executor. This is the
    authoritative source for a position's CURRENTLY-active SL — see the executor
    route's docstring for why the DB can't be trusted for this.

    Returns:
      - list (possibly empty): a CONFIRMED read — the executor's adapters distinguish
        a genuine empty result from a read failure (see list_trigger_orders on both
        adapters), so [] here reliably means "no resting stops."
      - None: UNKNOWN — either the executor/adapter's own exchange-call failed (the
        route passes that through as JSON null), or this transport call itself failed
        (unreachable/non-200). Callers MUST NOT treat None as 'no trigger orders'.
    Never raises.
    """
    url = f"{EXECUTOR_URL}/accounts/{account_id}/trigger-orders/{symbol}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else None
    except Exception as e:
        logger.warning(f"get_trigger_orders({account_id}, {symbol}) UNKNOWN: {e}")
        return None


async def get_account_positions(account_id: str) -> Optional[list]:
    """
    Fetch live open positions for an account from the executor.

    Returns:
      - list (possibly empty): a CONFIRMED read. [] means the exchange confirmed no positions.
      - None: UNKNOWN — executor/exchange unreachable or returned an error. Callers MUST NOT
        treat None as 'no positions'.
    Never raises.
    """
    url = f"{EXECUTOR_URL}/accounts/{account_id}/positions"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.warning(
            f"get_account_positions({account_id}) UNKNOWN after {elapsed_ms}ms "
            f"(treating as unreachable, NOT as empty): {type(e).__name__}: {e!r}"
        )
        return None


async def get_account_open_orders(account_id: str, symbol: Optional[str] = None) -> Optional[list]:
    """
    Fetch resting, non-trigger limit orders for an account from the executor.

    Returns:
      - list (possibly empty): a CONFIRMED read. [] means the exchange confirmed no resting
        orders.
      - None: UNKNOWN — executor/exchange unreachable or returned an error shape. Callers
        MUST NOT treat None as 'no orders' (mirrors get_account_positions).
    Never raises.
    """
    url = f"{EXECUTOR_URL}/accounts/{account_id}/orders"
    params = {"symbol": symbol} if symbol else None
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else None
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.warning(
            f"get_account_open_orders({account_id}) UNKNOWN after {elapsed_ms}ms "
            f"(treating as unreachable, NOT as empty): {type(e).__name__}: {e!r}"
        )
        return None


async def call_executor_cancel_order(account_id: str, symbol: str, order_id: str) -> dict:
    """
    POST to /accounts/{account_id}/orders/cancel. Returns result dict. Never raises.
    """
    url = f"{EXECUTOR_URL}/accounts/{account_id}/orders/cancel"
    body = {"symbol": symbol, "order_id": str(order_id)}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Executor cancel-order failed: {e}")
        return {"success": False, "error": str(e)}


async def call_executor_amend_order(
    account_id: str, symbol: str, order_id: str,
    new_price: Optional[float] = None, new_size: Optional[float] = None,
) -> dict:
    """
    POST to /accounts/{account_id}/orders/amend. Returns result dict. Never raises.
    """
    url = f"{EXECUTOR_URL}/accounts/{account_id}/orders/amend"
    body: dict = {"symbol": symbol, "order_id": str(order_id)}
    if new_price is not None:
        body["new_price"] = float(new_price)
    if new_size is not None:
        body["new_size"] = float(new_size)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Executor amend-order failed: {e}")
        return {"success": False, "error": str(e)}


async def get_position_history(account_id: str, symbol: str, opened_at=None) -> dict:
    """
    Fetch closed-position history for a symbol from the executor.
    When opened_at is provided, the lookup is scoped (via ?since=) to this position's lifetime
    so PnL is not summed across the coin's entire close history.
    Returns the history dict or {} on any error. Never raises.
    """
    path = f"/accounts/{account_id}/positions/history?symbol={symbol}"
    if opened_at is not None:
        try:
            since_ms = int(opened_at.timestamp() * 1000)
        except AttributeError:
            since_ms = int(opened_at)
        path += f"&since={since_ms}"
    return await call_executor_get(path)


async def get_order_fill_fee(account_id: str, symbol: str, order_id: str) -> Optional[Decimal]:
    """
    Fetch the exchange fee for an already-filled order id from the executor.
    Used by the reconciler for fills it detects asynchronously (a resting order that
    fills between polls never gets a synchronous fee lookup at placement time).
    Returns None on any error or if unknown. Never raises.
    """
    path = f"/accounts/{account_id}/orders/{order_id}/fee?symbol={symbol}"
    result = await call_executor_get(path)
    fee = result.get("fee")
    return Decimal(str(fee)) if fee is not None else None


async def call_executor_modify_stops(
    account_id: str,
    symbol:     str,
    side:       str,
    tp_price=None,
    sl_price=None,
) -> dict:
    """
    POST to /accounts/{account_id}/positions/modify-stops.
    Returns result dict. Never raises.
    """
    url = f"{EXECUTOR_URL}/accounts/{account_id}/positions/modify-stops"
    body: dict = {"symbol": symbol, "side": side}
    if tp_price is not None:
        body["tp_price"] = float(tp_price)
    if sl_price is not None:
        body["sl_price"] = float(sl_price)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Executor modify-stops failed: {e}")
        return {"success": False, "error_msg": str(e)}


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

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            f"Executor close-position timeout after {elapsed_ms}ms: "
            f"{type(e).__name__}: {e!r}"
        )
        return {
            "success": False,
            "status":  "route_failed",
            "error_msg": f"Executor timeout after {TIMEOUT_SECONDS}s",
        }

    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            f"Executor close-position failed after {elapsed_ms}ms: "
            f"{type(e).__name__}: {e!r}"
        )
        return {
            "success": False,
            "status":  "route_failed",
            "error_msg": str(e),
        }
