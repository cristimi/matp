"""
Order Listener Service — FastAPI application entry point.
Receives webhooks, validates, logs, and routes to exchange adapters.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.redis_client import init_redis
from app.webhook_handler import router as webhook_router
from app.orders_api import router as orders_router
from app.config_api import router as config_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Order Listener service...")
    await init_db()
    await init_redis()
    logger.info("Order Listener ready.")
    yield
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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "order-listener"}
