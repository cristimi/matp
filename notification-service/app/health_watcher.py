"""
Background loop: watches ingestion heartbeats + critical-service /health endpoints
and emits edge-triggered exchange.*/service.* events onto the same notification
stream (so they get logged + delivered like everything else).
"""

import asyncio
import logging

import httpx

from app.config import settings, exchange_list
from app import redis_client

logger = logging.getLogger(__name__)


async def _check_exchange(client: httpx.AsyncClient, exchange: str, state: dict) -> None:
    value = await redis_client.heartbeat_value(exchange)
    now = redis_client.now_ms()
    stale = value is None or (now - value) > settings.heartbeat_stale_ms
    was_up = state.get(exchange, True)

    if stale and was_up:
        await redis_client.emit_event("exchange.down", {"exchange": exchange})
        state[exchange] = False
    elif not stale and not was_up:
        await redis_client.emit_event("exchange.up", {"exchange": exchange})
        state[exchange] = True


async def _check_service(client: httpx.AsyncClient, service: str, url: str, state: dict) -> None:
    was_up = state.get(service, True)
    healthy = False
    try:
        resp = await client.get(f"{url}/health", timeout=5.0)
        healthy = resp.status_code == 200
    except Exception as e:
        logger.debug("Health check failed for %s: %s", service, e)
        healthy = False

    if not healthy and was_up:
        await redis_client.emit_event("service.down", {"service": service})
        state[service] = False
    elif healthy and not was_up:
        await redis_client.emit_event("service.up", {"service": service})
        state[service] = True


async def run_health_watcher_loop() -> None:
    exchange_state: dict[str, bool] = {}
    service_state: dict[str, bool] = {}
    services = {
        "order-executor": settings.executor_url,
        "order-listener": settings.listener_url,
    }

    async with httpx.AsyncClient() as client:
        while True:
            try:
                for exchange in exchange_list():
                    await _check_exchange(client, exchange, exchange_state)
                for service, url in services.items():
                    await _check_service(client, service, url, service_state)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Health watcher iteration failed")
            await asyncio.sleep(settings.health_poll_interval_s)
