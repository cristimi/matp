"""
Background loop: watches ingestion heartbeats, critical-service /health endpoints
and every active exchange account's credentials, and emits edge-triggered
exchange.*/service.*/account.* events onto the same notification stream (so they
get logged + delivered like everything else).
"""

import asyncio
import logging
import time

import httpx

from app.config import settings, exchange_list
from app import redis_client
from app.db import get_pool

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


async def _active_accounts() -> list[dict]:
    rows = await get_pool().fetch(
        "SELECT id, exchange, mode, label FROM exchange_accounts WHERE is_active ORDER BY id"
    )
    return [dict(r) for r in rows]


async def _probe_account(client: httpx.AsyncClient, account_id: str) -> str | None:
    """None when the account answers, else a short reason it did not.

    order-executor's balance route never raises: a rejected key, a dead exchange or
    an unreachable executor all come back as an `error` field (or no answer at all),
    which is exactly the "this account cannot trade" signal wanted here.
    """
    try:
        resp = await client.get(
            f"{settings.executor_url}/accounts/{account_id}/balance",
            timeout=settings.account_poll_timeout_s,
        )
    except Exception as e:  # noqa: BLE001
        return f"executor unreachable: {type(e).__name__}"
    if resp.status_code != 200:
        return f"executor HTTP {resp.status_code}"
    try:
        body = resp.json()
    except ValueError:
        return "executor returned no JSON"
    err = body.get("error") if isinstance(body, dict) else None
    return str(err)[:200] if err else None


async def _check_accounts(client: httpx.AsyncClient, state: dict, fails: dict) -> None:
    """Edge-triggered account.down / account.up per active exchange account.

    `state` remembers up/down per account (missing = assumed up, so a key that is
    already dead when the service starts is reported on its first confirmed miss
    streak, not silently accepted as the baseline). `fails` counts consecutive
    misses so a single slow or flaky answer does not page anyone.
    """
    for acct in await _active_accounts():
        acct_id = acct["id"]
        reason = await _probe_account(client, acct_id)
        was_up = state.get(acct_id, True)
        if reason is None:
            fails[acct_id] = 0
            if not was_up:
                await redis_client.emit_event("account.up", {"account_id": acct_id, **acct})
                state[acct_id] = True
            continue
        fails[acct_id] = fails.get(acct_id, 0) + 1
        logger.warning("account probe failed for %s (%d/%d): %s",
                       acct_id, fails[acct_id], settings.account_fail_threshold, reason)
        if was_up and fails[acct_id] >= settings.account_fail_threshold:
            await redis_client.emit_event(
                "account.down", {"account_id": acct_id, "reason": reason, **acct}
            )
            state[acct_id] = False


async def run_health_watcher_loop() -> None:
    exchange_state: dict[str, bool] = {}
    service_state: dict[str, bool] = {}
    account_state: dict[str, bool] = {}
    account_fails: dict[str, int] = {}
    next_account_check = 0.0
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
                if time.monotonic() >= next_account_check:
                    next_account_check = time.monotonic() + settings.account_poll_interval_s
                    await _check_accounts(client, account_state, account_fails)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Health watcher iteration failed")
            await asyncio.sleep(settings.health_poll_interval_s)
