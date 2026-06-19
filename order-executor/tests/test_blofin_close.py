"""
Unit tests for BlofinAdapter.submit_order — verify reduceOnly behaviour.

No network calls are made: httpx.AsyncClient and _get_instrument are patched.
"""
import json
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.adapters.blofin import BlofinAdapter
from app.models import OrderRequest, OrderResult

FAKE_CREDS = {
    "api_key":        "test_key",
    "api_secret":     "test_secret",
    "api_passphrase": "test_pass",
}

HYPE_SPEC = {
    "instId":        "HYPE-USDT",
    "contractValue": "1",
    "lotSize":       "0.1",
    "minSize":       "0.1",
    "maxLeverage":   "50",
}

BTC_SPEC = {
    "instId":        "BTC-USDT",
    "contractValue": "0.001",
    "lotSize":       "0.1",
    "minSize":       "0.1",
    "maxLeverage":   "125",
}


def _make_adapter():
    return BlofinAdapter(FAKE_CREDS, mode="demo")


def _make_order(symbol="HYPE-USDT", side="buy", signal="open_long", size="1.5"):
    return OrderRequest(
        order_id="test-order-1",
        account_id="acc_test",
        symbol=symbol,
        side=side,
        signal=signal,
        order_type="market",
        size=Decimal(size),
        leverage=20,
        margin_mode="isolated",
    )


def _mock_http_response(order_id="999"):
    """Return a mock httpx response that looks like a successful BloFin order."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "code": "0",
        "data": [{"orderId": order_id}],
    }
    return mock_resp


def _mock_fill_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "code": "0",
        "data": [{"orderId": "999", "avgPrice": "68.50", "pnl": "0.5"}],
    }
    return mock_resp


@pytest.mark.asyncio
async def test_submit_order_close_short_has_reduce_only():
    """close_short signal must add reduceOnly=true and positionSide=net."""
    adapter = _make_adapter()
    order = _make_order(side="buy", signal="close_short", size="1.0")

    captured_bodies = []

    async def fake_post(url, content=None, headers=None, **kw):
        if content:
            captured_bodies.append(json.loads(content))
        return _mock_http_response()

    async def fake_get(url, headers=None, **kw):
        return _mock_fill_response()

    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_set_leverage", AsyncMock()), \
         patch("app.adapters.blofin.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(side_effect=fake_post),
                                   get=AsyncMock(side_effect=fake_get))
        )
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await adapter.submit_order(order)

    assert result.success, f"Expected success, got: {result}"
    assert captured_bodies, "No order body was captured"
    body = captured_bodies[0]
    assert body.get("reduceOnly") == "true", f"Expected reduceOnly='true', got body={body}"
    assert body.get("positionSide") == "net", f"Expected positionSide='net', got body={body}"


@pytest.mark.asyncio
async def test_submit_order_close_long_has_reduce_only():
    """close_long signal must also add reduceOnly=true."""
    adapter = _make_adapter()
    order = _make_order(side="sell", signal="close_long", size="1.0")

    captured_bodies = []

    async def fake_post(url, content=None, headers=None, **kw):
        if content:
            captured_bodies.append(json.loads(content))
        return _mock_http_response()

    async def fake_get(url, headers=None, **kw):
        return _mock_fill_response()

    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_set_leverage", AsyncMock()), \
         patch("app.adapters.blofin.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(side_effect=fake_post),
                                   get=AsyncMock(side_effect=fake_get))
        )
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await adapter.submit_order(order)

    assert result.success
    body = captured_bodies[0]
    assert body.get("reduceOnly") == "true"
    assert body.get("positionSide") == "net"


@pytest.mark.asyncio
async def test_submit_order_open_long_has_no_reduce_only():
    """open_long signal must NOT set reduceOnly."""
    adapter = _make_adapter()
    order = _make_order(side="buy", signal="open_long", size="1.5")

    captured_bodies = []

    async def fake_post(url, content=None, headers=None, **kw):
        if content:
            captured_bodies.append(json.loads(content))
        return _mock_http_response()

    async def fake_get(url, headers=None, **kw):
        return _mock_fill_response()

    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_set_leverage", AsyncMock()), \
         patch("app.adapters.blofin.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(side_effect=fake_post),
                                   get=AsyncMock(side_effect=fake_get))
        )
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await adapter.submit_order(order)

    assert result.success
    body = captured_bodies[0]
    assert "reduceOnly" not in body, f"open_long must not have reduceOnly; got body={body}"
    assert "positionSide" not in body, f"open_long must not have positionSide; got body={body}"
