"""
Results API: per-run read-only sub-resources.

  GET /runs/{run_id}/orders
  GET /runs/{run_id}/positions
  GET /runs/{run_id}/equity-curve
  GET /runs/{run_id}/signals
"""
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.database import get_pool

logger = logging.getLogger(__name__)
router = APIRouter()


def _row_to_dict(row) -> dict[str, Any]:
    """Convert asyncpg Record to plain dict, serialising timestamps."""
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, 'isoformat'):
            d[k] = v.isoformat()
        elif isinstance(v, list):
            d[k] = list(v)
    return d


async def _assert_run_exists(run_id: str) -> None:
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM tester.backtest_runs WHERE id = $1::uuid", run_id
            )
    except Exception:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    if not exists:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")


# ── Orders ────────────────────────────────────────────────────────────────────

@router.get("/{run_id}/orders")
async def get_orders(
    run_id: str,
    limit:  int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0,   ge=0),
):
    await _assert_run_exists(run_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, backtest_run_id, candle_timestamp, symbol, side, signal,
                   size, price, actual_fill_price, pnl, fee, status, strategy_id
            FROM tester.orders
            WHERE backtest_run_id = $1
            ORDER BY candle_timestamp, id
            LIMIT $2 OFFSET $3
            """,
            run_id, limit, offset,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM tester.orders WHERE backtest_run_id = $1", run_id
        )
    return {
        "run_id": run_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_row_to_dict(r) for r in rows],
    }


# ── Positions ─────────────────────────────────────────────────────────────────

@router.get("/{run_id}/positions")
async def get_positions(
    run_id: str,
    limit:  int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0,   ge=0),
):
    await _assert_run_exists(run_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, backtest_run_id, strategy_id, symbol, side,
                   entry_price, closing_price, current_price, size,
                   pnl_realized, fee_open, fee_close, status,
                   opening_order_id, closing_order_id,
                   close_reason, opened_at, closed_at
            FROM tester.strategy_positions
            WHERE backtest_run_id = $1
            ORDER BY opened_at, id
            LIMIT $2 OFFSET $3
            """,
            run_id, limit, offset,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM tester.strategy_positions WHERE backtest_run_id = $1",
            run_id,
        )
    return {
        "run_id": run_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_row_to_dict(r) for r in rows],
    }


# ── Equity curve ──────────────────────────────────────────────────────────────

@router.get("/{run_id}/equity-curve")
async def get_equity_curve(run_id: str):
    """
    Returns all candle-level equity rows for the run in chronological order.
    Clients should expect one row per active candle.
    """
    await _assert_run_exists(run_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT candle_ts, realized_balance, mark_balance,
                   trade_pnl, drawdown_pct
            FROM tester.equity_curve
            WHERE backtest_run_id = $1
            ORDER BY candle_ts
            """,
            run_id,
        )
    return {
        "run_id": run_id,
        "count": len(rows),
        "items": [_row_to_dict(r) for r in rows],
    }


# ── Signals ───────────────────────────────────────────────────────────────────

@router.get("/{run_id}/signals")
async def get_signals(
    run_id:      str,
    gate_passed: bool | None = Query(default=None),
    limit:       int         = Query(default=500, ge=1, le=5000),
    offset:      int         = Query(default=0,   ge=0),
):
    """
    Returns ai_signal_log rows for the run.
    Optional ?gate_passed=true/false to filter.
    """
    await _assert_run_exists(run_id)
    pool = get_pool()

    where = "WHERE backtest_run_id = $1"
    params: list = [run_id]

    if gate_passed is not None:
        params.append(gate_passed)
        where += f" AND gate_passed = ${len(params)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, strategy_id, triggered_at, trigger_reason, cycle_interval,
                   proposed_action, confidence, reasoning,
                   gate_passed, gate_rejection_reason,
                   context_tokens, order_id
            FROM tester.ai_signal_log
            {where}
            ORDER BY triggered_at, id
            LIMIT ${len(params)+1} OFFSET ${len(params)+2}
            """,
            *params, limit, offset,
        )
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM tester.ai_signal_log {where}",
            *params,
        )
    return {
        "run_id": run_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "gate_passed_filter": gate_passed,
        "items": [_row_to_dict(r) for r in rows],
    }
