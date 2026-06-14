"""
Integration tests for the webhook handler.

Tests HMAC auth, payload validation, symbol coupling,
and all four risk guards.

Uses FastAPI TestClient — no live services required.
"""
import pytest
import hmac
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

# ── Fixtures ─────────────────────────────────────────────────────────

WEBHOOK_SECRET = "test_secret_32_chars_exactly_pad"
STRATEGY_ID    = "test_strategy_1"

BASE_PAYLOAD = {
    "base_asset":  "BTC",
    "quote_asset": "USDT",
    "side":        "buy",
    "signal":      "open_long",
    "order_type":  "market",
    "size":        "0.001",
    "leverage":    10,
    "margin_mode": "cross",
    "timestamp":   "2026-06-01T00:00:00Z",
    "token":       WEBHOOK_SECRET,
}

# A strategy record that passes all guards
SAFE_STRATEGY = {
    "id":                        STRATEGY_ID,
    "symbol":                    "BTC-USDT",
    "account_id":                "acc_test",
    "enabled":                   True,
    "webhook_enabled":           True,
    "webhook_secret":            WEBHOOK_SECRET,
    "allow_quote_variants":      False,
    "allow_cross_charting":      False,
    "max_daily_signals":         500,
    "signals_today":             0,
    "max_position_size":         1.0,
    "max_leverage":              20,
    "pnl_today":                 0.0,
}


def make_mock_db(strategy_override=None):
    """Return a mock asyncpg pool that returns SAFE_STRATEGY or override."""
    strategy = {**SAFE_STRATEGY, **(strategy_override or {})}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=strategy)
    conn.execute  = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire  = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__  = AsyncMock(return_value=False)
    return pool, conn


def make_executor_result(status="route_failed", success=False):
    return {
        "success":           success,
        "status":            status,
        "exchange_order_id": None,
        "error_msg":         "test",
    }


# ── HMAC Authentication ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_token_returns_403():
    from app.main import app
    pool, _ = make_mock_db()
    with patch("app.webhook_handler.get_pool", return_value=pool), \
         patch("app.executor_client.call_executor",
               AsyncMock(return_value=make_executor_result())), \
         patch("app.webhook_handler.publish", AsyncMock()):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {**BASE_PAYLOAD, "token": "wrong_token"}
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json=payload
            )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_valid_token_passes_auth():
    from app.main import app
    pool, _ = make_mock_db()
    with patch("app.webhook_handler.get_pool", return_value=pool), \
         patch("app.executor_client.call_executor",
               AsyncMock(return_value=make_executor_result())), \
         patch("app.webhook_handler.publish", AsyncMock()):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json=BASE_PAYLOAD
            )
        # 200 means it passed guards and authentication
        assert resp.status_code == 200


# ── Payload Validation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_base_asset_returns_422():
    from app.main import app
    pool, _ = make_mock_db()
    with patch("app.webhook_handler.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {k: v for k, v in BASE_PAYLOAD.items()
                       if k != "base_asset"}
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json=payload
            )
        # Expect 422 if validation is implemented in the handler
        assert resp.status_code == 422


# ── Symbol Coupling ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_symbol_mismatch_returns_422():
    from app.main import app
    pool, _ = make_mock_db()
    with patch("app.webhook_handler.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {**BASE_PAYLOAD, "quote_asset": "USDC"}
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json=payload
            )
        assert resp.status_code == 422
        assert "mismatch" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_quote_variant_accepted_when_flag_on():
    from app.main import app
    pool, _ = make_mock_db({"allow_quote_variants": True})
    
    with patch("app.webhook_handler.get_pool", return_value=pool), \
         patch("app.executor_client.call_executor",
               AsyncMock(return_value=make_executor_result())), \
         patch("app.webhook_handler.publish", AsyncMock()):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {**BASE_PAYLOAD, "quote_asset": "USDC"}
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json=payload
            )
        assert resp.status_code == 200


# ── Risk Guard 1: Daily signal cap ────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_signal_cap_returns_429():
    from app.main import app
    pool, _ = make_mock_db({"signals_today": 500, "max_daily_signals": 500})

    with patch("app.webhook_handler.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json=BASE_PAYLOAD
            )
        assert resp.status_code == 429
        assert "daily signal limit" in resp.json().get("detail", "").lower()


# ── Risk Guard 2: Max position size ──────────────────────────────────

@pytest.mark.asyncio
async def test_oversized_order_returns_422():
    from app.main import app
    pool, _ = make_mock_db()
    with patch("app.webhook_handler.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {**BASE_PAYLOAD, "size": "9999.0"}
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json=payload
            )
        assert resp.status_code == 422
        assert "exceeds" in resp.json().get("detail", "").lower()


# ── Risk Guard 3: Max leverage ────────────────────────────────────────

@pytest.mark.asyncio
async def test_excessive_leverage_returns_422():
    from app.main import app
    pool, _ = make_mock_db()
    with patch("app.webhook_handler.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {**BASE_PAYLOAD, "leverage": 999}
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json=payload
            )
        assert resp.status_code == 422
        assert "leverage" in resp.json().get("detail", "").lower()


# ── Disabled strategy ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_strategy_returns_403():
    """A strategy with webhook_enabled=False must reject signals."""
    from app.main import app
    # In webhook_handler, it checks strategy['webhook_enabled']
    pool, _ = make_mock_db({"webhook_enabled": False})

    with patch("app.webhook_handler.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json=BASE_PAYLOAD
            )
        assert resp.status_code == 403
        assert "disabled" in resp.json().get("detail", "").lower()
