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
    "max_leverage":              20,
    "pnl_today":                 0.0,
}

# Every market open is sized and bracketed against the account's own exchange mark,
# so a test that expects an open to be accepted has to supply one — without it the
# handler correctly refuses to place an unsized entry.
MARK_PRICE = 63000.0


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
         patch("app.webhook_handler.get_mark_price", AsyncMock(return_value=MARK_PRICE)), \
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
         patch("app.webhook_handler.get_mark_price", AsyncMock(return_value=MARK_PRICE)), \
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


# (The daily signal cap that used to be tested here was removed as broken —
#  db/migrations/030_drop_dead_columns.sql dropped signals_today/max_daily_signals.)


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


# ── Stopped strategy ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stopped_strategy_returns_403():
    """A strategy with enabled=False must reject signals with 403 Strategy stopped."""
    from app.main import app
    pool, _ = make_mock_db({"enabled": False})

    with patch("app.webhook_handler.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json=BASE_PAYLOAD
            )
        assert resp.status_code == 403
        assert "stopped" in resp.json().get("detail", "").lower()


# ── Distance-form bracket (tp_pct / sl_pct) ───────────────────────────
#
# The AI engine sends distances instead of levels for market entries, because its
# own candle feed is the venue's PUBLIC market while the order fills at the
# account's own market — ~1% apart on a demo account. Pricing the bracket here,
# against the exchange mark, is what keeps the asked reward/risk intact.

async def _post_open(payload_extra: dict, *, mark=MARK_PRICE, strategy_override=None):
    """POST an opening webhook and return (response, payload seen by _log_order)."""
    from app.main import app
    pool, _ = make_mock_db(strategy_override)
    logged = {}

    async def _capture_log_order(pool_, payload_, *a, **kw):
        logged["payload"] = payload_

    with patch("app.webhook_handler.get_pool", return_value=pool), \
         patch("app.webhook_handler.get_mark_price", AsyncMock(return_value=mark)), \
         patch("app.webhook_handler._log_order", AsyncMock(side_effect=_capture_log_order)), \
         patch("app.executor_client.call_executor",
               AsyncMock(return_value=make_executor_result())), \
         patch("app.webhook_handler.publish", AsyncMock()):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/webhook/{STRATEGY_ID}", json={**BASE_PAYLOAD, **payload_extra}
            )
    return resp, logged.get("payload")


@pytest.mark.asyncio
async def test_pct_bracket_is_priced_from_the_exchange_mark():
    """sl_pct/tp_pct become prices measured from the account's own mark, not from
    whatever price the caller was looking at."""
    resp, payload = await _post_open({"sl_pct": "1.0", "tp_pct": "1.5"})

    assert resp.status_code == 200
    # 63000 → stop 1% below, target 1.5% above
    assert float(payload.sl_price) == pytest.approx(62370.0)
    assert float(payload.tp_price) == pytest.approx(63945.0)
    # and the ask is recorded, so a later re-anchor knows what was intended
    assert payload.signal_metadata["stops_from_pct"] is True
    assert payload.signal_metadata["sl_pct_asked"] == 1.0
    assert payload.signal_metadata["tp_pct_asked"] == 1.5


@pytest.mark.asyncio
async def test_pct_bracket_mirrors_for_a_short():
    resp, payload = await _post_open(
        {"side": "sell", "signal": "open_short", "sl_pct": "1.0", "tp_pct": "1.5"}
    )

    assert resp.status_code == 200
    assert float(payload.sl_price) == pytest.approx(63630.0)   # above entry
    assert float(payload.tp_price) == pytest.approx(62055.0)   # below entry


# ── Risk Guard: reward/risk floor ─────────────────────────────────────

@pytest.mark.asyncio
async def test_reward_risk_below_floor_is_rejected():
    """The exact shape of the 2026-08-11 BTC AI entry: 0.5% of reward against
    2% of risk. It must not reach the exchange."""
    resp, _ = await _post_open({"sl_pct": "2.0", "tp_pct": "0.5"})

    assert resp.status_code == 422
    detail = resp.json().get("detail", "").lower()
    assert "reward/risk" in detail and "0.25" in detail


@pytest.mark.asyncio
async def test_reward_risk_exactly_one_is_allowed():
    """The floor is 1.0 inclusive — equal reward and risk is a plan, not a symptom."""
    resp, payload = await _post_open({"sl_pct": "1.0", "tp_pct": "1.0"})

    assert resp.status_code == 200
    assert float(payload.tp_price) == pytest.approx(63630.0)


@pytest.mark.asyncio
async def test_reward_risk_floor_also_judges_absolute_prices():
    """The floor is the last line of defence, so it applies to a caller that sends
    levels (TradingView) and not only to distance-form brackets."""
    resp, _ = await _post_open({"sl_price": "61000", "tp_price": "63500"})

    assert resp.status_code == 422
    assert "reward/risk" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_target_on_the_wrong_side_is_rejected():
    """A long whose target sits below its entry: caught as a wrong-side bracket
    rather than passed through by an absolute value."""
    resp, _ = await _post_open({"sl_price": "62000", "tp_price": "62500"})

    assert resp.status_code == 422
    assert "wrong side" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_stop_only_open_is_not_judged_on_reward_risk():
    """Most TradingView strategies send a stop and no target — reward/risk cannot
    be computed, so the guard must stay out of the way."""
    resp, payload = await _post_open({"sl_price": "62000"})

    assert resp.status_code == 200
    assert payload.tp_price is None
