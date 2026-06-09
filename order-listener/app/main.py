"""
Order Listener Service — FastAPI application entry point.
Receives webhooks, validates, logs, and routes to exchange adapters.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
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
