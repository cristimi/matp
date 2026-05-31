"""
Order Executor Service — fully wired from Session 4 onward.
Receives OrderRequest from order-listener.
Routes to correct exchange adapter via AccountRegistry.
Returns OrderResult.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.models import OrderRequest
from app.registry import registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Order Executor ready — AccountRegistry active")
    yield
    logger.info("Order Executor shutting down")


app = FastAPI(
    title="MATP Order Executor",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/execute")
async def execute_order(request: OrderRequest):
    from app.executor import execute
    result = await execute(request)
    return result


@app.post("/accounts/{account_id}/invalidate")
async def invalidate_account(account_id: str):
    registry.invalidate(account_id)
    return {"invalidated": account_id}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "order-executor", "version": "1.0.0"}
