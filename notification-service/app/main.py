"""
Notification Service — FastAPI application entry point.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.db import init_db, get_pool
from app import redis_client
from app.consumer import run_consumer_loop
from app.health_watcher import run_health_watcher_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await redis_client.init_redis()
    await redis_client.ensure_group()

    consumer_task = asyncio.create_task(run_consumer_loop(), name="consumer_loop")
    watcher_task = asyncio.create_task(run_health_watcher_loop(), name="health_watcher_loop")

    app.state.consumer_task = consumer_task
    app.state.watcher_task = watcher_task

    yield

    consumer_task.cancel()
    watcher_task.cancel()
    await asyncio.gather(consumer_task, watcher_task, return_exceptions=True)
    logger.info("Notification service shutdown complete")


app = FastAPI(
    title="MATP Notification Service",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "notification-service"}


@app.get("/vapid-public-key")
async def vapid_public_key():
    return {"public_key": settings.vapid_public_key}


class KeysModel(BaseModel):
    p256dh: str
    auth: str


class SubscribeRequest(BaseModel):
    endpoint: str
    keys: KeysModel
    user_agent: str | None = None


@app.post("/subscriptions")
async def create_subscription(body: SubscribeRequest):
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent, enabled, last_seen_at)
        VALUES ($1, $2, $3, $4, true, now())
        ON CONFLICT (endpoint) DO UPDATE
        SET p256dh = EXCLUDED.p256dh,
            auth = EXCLUDED.auth,
            user_agent = EXCLUDED.user_agent,
            enabled = true,
            last_seen_at = now()
        """,
        body.endpoint, body.keys.p256dh, body.keys.auth, body.user_agent,
    )
    return {"status": "ok"}


class UnsubscribeRequest(BaseModel):
    endpoint: str


@app.delete("/subscriptions")
async def delete_subscription(body: UnsubscribeRequest):
    pool = get_pool()
    result = await pool.execute(
        "UPDATE push_subscriptions SET enabled = false WHERE endpoint = $1",
        body.endpoint,
    )
    return {"status": "ok", "result": result}


@app.post("/test")
async def test_notification():
    entry_id = await redis_client.emit_event(
        "service.up", {"service": "notification-service-test"}
    )
    return {"status": "emitted", "entry_id": entry_id}
