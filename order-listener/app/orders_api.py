"""
REST API for querying orders (used by Dashboard API).
"""

import logging
from typing import Optional
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from app.database import get_pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders")


class OrderItem(BaseModel):
    id:                str
    received_at:       datetime
    symbol:            str
    side:              str
    signal:            str
    size:              float
    platform:          str
    status:            str
    strategy_id:       Optional[str] = None
    exchange_order_id: Optional[str] = None
    pnl:               Optional[float] = None
    error_msg:         Optional[str] = None


@router.get("")
async def list_orders(
    page:        int = Query(1, ge=1),
    limit:       int = Query(50, ge=1, le=200),
    symbol:      Optional[str] = None,
    platform:    Optional[str] = None,
    status:      Optional[str] = None,
    strategy_id: Optional[str] = None,
    from_dt:     Optional[datetime] = Query(None, alias="from"),
    to_dt:       Optional[datetime] = Query(None, alias="to"),
):
    pool = get_pool()
    offset = (page - 1) * limit

    filters = []
    params = []
    idx = 1

    if symbol:
        filters.append(f"symbol = ${idx}")
        params.append(symbol)
        idx += 1
    if platform:
        filters.append(f"platform = ${idx}")
        params.append(platform)
        idx += 1
    if status:
        filters.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    if strategy_id:
        filters.append(f"strategy_id = ${idx}")
        params.append(strategy_id)
        idx += 1
    if from_dt:
        filters.append(f"received_at >= ${idx}")
        params.append(from_dt)
        idx += 1
    if to_dt:
        filters.append(f"received_at <= ${idx}")
        params.append(to_dt)
        idx += 1

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM orders {where}", *params)
        rows = await conn.fetch(
            f"""SELECT id, received_at, symbol, side, signal, size, platform,
                       status, strategy_id, exchange_order_id, pnl, error_msg
                FROM orders {where}
                ORDER BY received_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}""",
            *params, limit, offset,
        )

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": [dict(r) for r in rows],
    }


@router.get("/{order_id}")
async def get_order(order_id: UUID):
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return dict(row)


@router.post("/{order_id}/retry")
async def retry_order(order_id: UUID):
    """Re-dispatch a dead-letter order through the executor client."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT raw_webhook, strategy_id, symbol FROM orders WHERE id = $1", order_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")
        
        strategy_row = await conn.fetchrow(
            "SELECT * FROM strategies WHERE id = $1", row["strategy_id"]
        )
        strategy = dict(strategy_row) if strategy_row else {}

    import json
    from app.models import WebhookPayload
    from app.executor_client import call_executor

    payload_data = json.loads(row["raw_webhook"])
    # Note: token is not needed for internal retry since we already authenticated the original order
    payload = WebhookPayload(**payload_data, token="internal_retry")
    
    # Build OrderRequest for executor
    account_id = strategy.get("account_id") or "acc_blofin_demo_default"
    order_request = {
        "order_id":    str(order_id),
        "account_id":  account_id,
        "symbol":      row["symbol"],
        "side":        payload.side,
        "signal":      payload.signal,
        "order_type":  payload.order_type,
        "size":        str(payload.size),
        "price":       str(payload.price)    if payload.price    else None,
        "leverage":    payload.leverage,
        "margin_mode": payload.margin_mode,
        "tp_price":    str(payload.tp_price) if payload.tp_price else None,
        "sl_price":    str(payload.sl_price) if payload.sl_price else None,
    }

    result_dict = await call_executor(order_request)
    
    # Update the original order status and dead letter log
    status = result_dict.get("status", "route_failed")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE orders 
            SET status = $1, 
                exchange_order_id = $2, 
                error_msg = $3,
                actual_fill_price = $4,
                raw_response = $5
            WHERE id = $6
            """,
            status, 
            result_dict.get("exchange_order_id"), 
            result_dict.get("error_msg"), 
            result_dict.get("actual_fill_price"),
            json.dumps(result_dict.get("raw_response")),
            order_id
        )
        await conn.execute(
            "UPDATE dead_letter_orders SET retry_count = retry_count + 1, last_retry = NOW() WHERE order_id = $1",
            order_id,
        )

    return {"order_id": str(order_id), "status": status, "retry_result": result_dict}
