"""
Order Listener Service — FastAPI application entry point.
Receives webhooks, validates, logs, and routes to exchange adapters.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, get_pool
from app.redis_client import init_redis
from app.webhook_handler import router as webhook_router
from app.orders_api import router as orders_router
from app.config_api import router as config_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

RECONCILE_INTERVAL_SECONDS: int = int(os.environ.get("RECONCILE_INTERVAL_SECONDS", "60"))


async def _reconciler_loop() -> None:
    from app.reconciler import reconcile_once
    logger.info(
        f"Reconciler loop started (interval={RECONCILE_INTERVAL_SECONDS}s,"
        f" threshold={os.environ.get('RECONCILE_MISS_THRESHOLD', '3')})"
    )
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        try:
            pool = get_pool()
            await reconcile_once(pool)
            logger.info("Reconciler: automatic pass complete")
        except Exception as e:
            logger.error(f"Reconciler loop error: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Order Listener service...")
    await init_db()
    await init_redis()
    task = asyncio.create_task(_reconciler_loop())
    logger.info("Order Listener ready.")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Shutting down Order Listener...")


app = FastAPI(
    title="MATP Order Listener",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Nginx handles external access; internal only
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)
app.include_router(orders_router)
app.include_router(config_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    logger.error(f"Validation error: {exc.errors()}")
    logger.error(f"Request body received: {body.decode(errors='replace')}")
    # Serialize errors using Pydantic's built-in conversion
    errors = exc.errors()
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": [str(e) for e in errors], "body": body.decode(errors='replace') if body else ""},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "order-listener"}


@app.post("/strategies/{strategy_id}/stop")
async def stop_strategy(strategy_id: str):
    """Flatten all open legs then disable the strategy. Disables only if all closes succeed."""
    from fastapi import HTTPException
    from app.webhook_handler import _flatten_strategy_positions
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, account_id, enabled FROM strategies WHERE id = $1",
            strategy_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")
    strategy = dict(row)
    results = await _flatten_strategy_positions(pool, strategy)
    errors = [r for r in results if not r.get("success")]
    if not errors:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE strategies SET enabled = false, updated_at = NOW() WHERE id = $1",
                strategy_id,
            )
    return {
        "stopped":     strategy_id,
        "enabled":     False if not errors else bool(row["enabled"]),
        "legs_closed": len(results) - len(errors),
        "errors":      errors,
    }


@app.post("/reconcile")
async def trigger_reconcile():
    """Run one reconciliation pass on demand."""
    from app.reconciler import reconcile_once
    pool = get_pool()
    try:
        await reconcile_once(pool)
        return {"success": True, "message": "Reconcile pass complete"}
    except Exception as e:
        logger.error(f"Manual reconcile failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})
