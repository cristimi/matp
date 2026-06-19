"""
Unit tests for actual_fill_size propagation in BlofinAdapter.submit_order.

BloFin stores positions in contracts; _to_contracts rounds to lot size.
The adapter must convert the filled contract count back to base coins and
return it as actual_fill_size so the listener can record the correct DB size.
"""
import json
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.adapters.blofin import BlofinAdapter
from app.models import OrderRequest

FAKE_CREDS = {
    "api_key":        "test_key",
    "api_secret":     "test_secret",
    "api_passphrase": "test_pass",
}

# HYPE contractValue=1, lotSize=0.1 → _to_contracts(1.45598556) = 1.5 contracts
#   → _to_base(1.5) = 1.5 base coins
HYPE_SPEC = {
    "instId":        "HYPE-USDT",
    "contractValue": "1",
    "lotSize":       "0.1",
    "minSize":       "0.1",
    "maxLeverage":   "50",
}

# BTC contractValue=0.001, lotSize=0.1 → _to_contracts(0.73) ≈ 730 contracts
BTC_SPEC = {
    "instId":        "BTC-USDT",
    "contractValue": "0.001",
    "lotSize":       "0.1",
    "minSize":       "0.1",
    "maxLeverage":   "125",
}


def _make_adapter():
    return BlofinAdapter(FAKE_CREDS, mode="demo")


def _make_order(symbol="HYPE-USDT", side="buy", signal="open_long", size="1.45598556"):
    return OrderRequest(
        order_id="test-order-fill",
        account_id="acc_test",
        symbol=symbol,
        side=side,
        signal=signal,
        order_type="market",
        size=Decimal(size),
        leverage=20,
        margin_mode="isolated",
    )


def _mock_place_response(order_id="777"):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": "0", "data": [{"orderId": order_id}]}
    return mock_resp


def _mock_details_response(filled_size="1.5", avg_price="68.50"):
    """Simulate BloFin orders-history returning filledSize in contracts."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "code": "0",
        "data": [{
            "orderId":    "777",
            "filledSize": filled_size,  # contracts
            "avgPrice":   avg_price,
            "pnl":        "0",
        }],
    }
    return mock_resp


@pytest.mark.asyncio
async def test_submit_order_returns_actual_fill_size_in_base_coins():
    """actual_fill_size must be base coins = filledSize × contractValue.
    HYPE: contractValue=1, filledSize=1.5 → actual_fill_size=1.5 base coins."""
    adapter = _make_adapter()
    order = _make_order(side="buy", signal="open_long", size="1.45598556")

    async def fake_post(url, content=None, headers=None, **kw):
        return _mock_place_response()

    async def fake_get(url, headers=None, **kw):
        return _mock_details_response(filled_size="1.5", avg_price="68.50")

    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_set_leverage", AsyncMock()), \
         patch("app.adapters.blofin.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(side_effect=fake_post),
                                   get=AsyncMock(side_effect=fake_get))
        )
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await adapter.submit_order(order)

    assert result.success, f"Expected success: {result}"
    assert result.actual_fill_size is not None, "actual_fill_size must be set for market orders"
    # HYPE contractValue=1 → 1.5 contracts × 1 = 1.5 base coins
    assert result.actual_fill_size == Decimal("1.5"), (
        f"Expected 1.5 base coins, got {result.actual_fill_size}"
    )


@pytest.mark.asyncio
async def test_submit_order_falls_back_to_submitted_size_when_no_details():
    """When order details fetch returns no filledSize, fall back to submitted contract count.
    HYPE: order_size=_to_contracts(1.45598556)=1.5 → _to_base(1.5)=1.5 base coins."""
    adapter = _make_adapter()
    order = _make_order(side="buy", signal="open_long", size="1.45598556")

    async def fake_post(url, content=None, headers=None, **kw):
        return _mock_place_response()

    async def fake_get(url, headers=None, **kw):
        # Details response omits filledSize — no field at all
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": "0",
            "data": [{"orderId": "777", "avgPrice": "68.50", "pnl": "0"}],
        }
        return mock_resp

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
    # Fallback: submitted size = "1.5" (lot-rounded) → _to_base = 1.5 base coins
    assert result.actual_fill_size == Decimal("1.5"), (
        f"Expected fallback 1.5 base coins, got {result.actual_fill_size}"
    )


@pytest.mark.asyncio
async def test_submit_order_fill_size_none_when_details_fetch_fails():
    """When the details fetch throws, actual_fill_size is None (not a crash).
    The order is still reported as filled — fill size just won't be available."""
    adapter = _make_adapter()
    order = _make_order(side="buy", signal="open_long", size="1.45598556")

    async def fake_post(url, content=None, headers=None, **kw):
        return _mock_place_response()

    async def fake_get(url, headers=None, **kw):
        raise RuntimeError("network error")

    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_set_leverage", AsyncMock()), \
         patch("app.adapters.blofin.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(side_effect=fake_post),
                                   get=AsyncMock(side_effect=fake_get))
        )
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await adapter.submit_order(order)

    assert result.success, "Order must still be filled even if details fetch fails"
    assert result.actual_fill_size is None, (
        "actual_fill_size must be None when details fetch fails"
    )
