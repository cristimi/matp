"""
Order Generator Service — strategy engine.
Runs configurable trading strategies and emits webhook signals to the Order Listener.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.scheduler import strategy_scheduler
from app.strategies_api import router as strategies_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Order Generator service...")
    await strategy_scheduler.start()
    logger.info("Order Generator ready — strategies loaded.")
    yield
    logger.info("Shutting down Order Generator...")
    strategy_scheduler.shutdown()


app = FastAPI(
    title="MATP Order Generator",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(strategies_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "order-generator"}
