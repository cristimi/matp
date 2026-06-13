"""
Backtest run API: POST /runs, GET /runs/{id}, POST /runs/{id}/cancel, DELETE /runs/{id}.
"""
import asyncio
import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_pool
from app.engine.backtest_engine import run_backtest, get_running_tasks

logger = logging.getLogger(__name__)
router = APIRouter()


class RunCreate(BaseModel):
    strategy_id:       str
    date_from:         date
    date_to:           date
    timeframe:         str   = "1h"
    initial_balance:   float = 1000.0
    slippage_pct:      float = 0.05
    fee_pct:           float = 0.02
    lookback_days:     int   = 90
    dry_signal:        bool  = False    # R3: use True for all verification runs
    llm_model_override: str | None = None


@router.post("", status_code=202)
async def create_run(body: RunCreate):
    if body.date_from >= body.date_to:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")

    pool = get_pool()

    # Verify strategy exists
    async with pool.acquire() as conn:
        strat = await conn.fetchrow(
            "SELECT id, symbol, interval FROM tester.strategies WHERE id=$1 AND is_deleted=false",
            body.strategy_id,
        )
        if strat is None:
            raise HTTPException(status_code=404, detail=f"Strategy not found: {body.strategy_id}")

        # Resolve timeframe: use body override, else strategy default
        timeframe = body.timeframe or strat['interval'] or '1h'

        # Create backtest_runs row (status=pending)
        run_id = await conn.fetchval(
            """
            INSERT INTO tester.backtest_runs (
                strategy_id, timeframe, date_from, date_to,
                lookback_days, initial_balance, slippage_pct, fee_pct,
                dry_signal_mode, status
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'pending')
            RETURNING id::text
            """,
            body.strategy_id, timeframe, body.date_from, body.date_to,
            body.lookback_days, body.initial_balance, body.slippage_pct, body.fee_pct,
            body.dry_signal,
        )

    # Kick off the engine as a background task (non-blocking)
    task = asyncio.create_task(run_backtest(run_id), name=f"backtest-{run_id}")
    get_running_tasks()[run_id] = task

    logger.info("run=%s created (dry=%s), task dispatched", run_id, body.dry_signal)
    return {"run_id": run_id, "status": "pending", "dry_signal": body.dry_signal}


@router.get("/{run_id}")
async def get_run(run_id: str):
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tester.backtest_runs WHERE id=$1", run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    r = dict(row)
    # Compute queue position for pending runs
    if r['status'] == 'pending':
        async with pool.acquire() as conn:
            pos = await conn.fetchval(
                """
                SELECT COUNT(*) FROM tester.backtest_runs
                WHERE status IN ('running','pending')
                  AND created_at < $1
                """,
                r['created_at'],
            )
        r['queue_position'] = int(pos)

    # Attach progress for running runs
    if r['status'] == 'running':
        total = r.get('total_candles') or 0
        proc  = r.get('candles_processed') or 0
        r['progress'] = {
            'candles_processed':  proc,
            'total_candles':      total,
            'pct':                round(proc / total * 100, 1) if total else 0,
            'llm_failures_so_far': r.get('llm_failures') or 0,
        }

    # Convert non-serialisable types
    for k, v in list(r.items()):
        if hasattr(v, 'isoformat'):
            r[k] = v.isoformat()

    return r


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str):
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM tester.backtest_runs WHERE id=$1", run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    if row['status'] not in ('pending', 'running'):
        raise HTTPException(
            status_code=409,
            detail=f"Run is already in terminal state: {row['status']}",
        )

    tasks = get_running_tasks()
    task  = tasks.get(run_id)
    if task and not task.done():
        task.cancel()
        logger.info("run=%s cancel requested", run_id)
    else:
        # Task not tracked (e.g. service restarted) — set status directly
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tester.backtest_runs SET status='cancelled', completed_at=NOW(), updated_at=NOW() WHERE id=$1",
                run_id,
            )

    return {"run_id": run_id, "status": "cancelled"}


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str):
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM tester.backtest_runs WHERE id=$1", run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    if row['status'] in ('pending', 'running'):
        raise HTTPException(
            status_code=409,
            detail="Cancel the run before deleting it",
        )

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tester.backtest_runs WHERE id=$1", run_id)
    logger.info("run=%s deleted (cascade)", run_id)
