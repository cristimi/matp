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

    mock_client = MagicMock(post=AsyncMock(side_effect=fake_post),
                             get=AsyncMock(side_effect=fake_get))

    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_set_leverage", AsyncMock()), \
         patch.object(adapter, "_client", mock_client):
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

    mock_client = MagicMock(post=AsyncMock(side_effect=fake_post),
                             get=AsyncMock(side_effect=fake_get))

    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_set_leverage", AsyncMock()), \
         patch.object(adapter, "_client", mock_client):
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

    mock_client = MagicMock(post=AsyncMock(side_effect=fake_post),
                             get=AsyncMock(side_effect=fake_get))

    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_set_leverage", AsyncMock()), \
         patch.object(adapter, "_client", mock_client):
        result = await adapter.submit_order(order)

    assert result.success, "Order must still be filled even if details fetch fails"
    assert result.actual_fill_size is None, (
        "actual_fill_size must be None when details fetch fails"
    )


def _make_limit_order(symbol="HYPE-USDT", size="1.45598556", price="68.00"):
    return OrderRequest(
        order_id="test-order-limit",
        account_id="acc_test",
        symbol=symbol,
        side="buy",
        signal="open_long",
        order_type="limit",
        size=Decimal(size),
        price=Decimal(price),
        leverage=20,
        margin_mode="isolated",
    )


@pytest.mark.asyncio
async def test_limit_order_returns_actual_fill_size():
    """Limit orders must set actual_fill_size = _to_base(order_size).
    No details fetch happens; the rounded submitted size is stored instead.
    HYPE: contractValue=1, lotSize=0.1, size=1.45598556 → order_size='1.5' → base=1.5."""
    adapter = _make_adapter()
    order = _make_limit_order(size="1.45598556", price="68.00")

    async def fake_post(url, content=None, headers=None, **kw):
        return _mock_place_response()

    mock_client = MagicMock(post=AsyncMock(side_effect=fake_post))

    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_set_leverage", AsyncMock()), \
         patch.object(adapter, "_client", mock_client):
        result = await adapter.submit_order(order)

    assert result.success, f"Expected success: {result}"
    assert result.actual_fill_size is not None, (
        "actual_fill_size must be set for limit orders (rounded submitted size)"
    )
    # HYPE contractValue=1, lotSize=0.1 → _to_contracts(1.45598556)=1.5 → _to_base(1.5)=1.5
    assert result.actual_fill_size == Decimal("1.5"), (
        f"Expected 1.5 base coins for limit order, got {result.actual_fill_size}"
    )


@pytest.mark.asyncio
async def test_market_order_still_uses_details_fetch():
    """Regression: market orders must still use the details fetch path, not the limit-order else."""
    adapter = _make_adapter()
    order = _make_order(side="buy", signal="open_long", size="1.45598556")

    async def fake_post(url, content=None, headers=None, **kw):
        return _mock_place_response()

    async def fake_get(url, headers=None, **kw):
        # Details return filledSize=1.3 — different from the submitted 1.5
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": "0",
            "data": [{"orderId": "777", "filledSize": "1.3", "avgPrice": "68.50", "pnl": "0"}],
        }
        return mock_resp

    mock_client = MagicMock(post=AsyncMock(side_effect=fake_post),
                             get=AsyncMock(side_effect=fake_get))

    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_set_leverage", AsyncMock()), \
         patch.object(adapter, "_client", mock_client):
        result = await adapter.submit_order(order)

    assert result.success
    # Must use the details-fetched value, not the submitted order_size
    assert result.actual_fill_size == Decimal("1.3"), (
        f"Market order must use filledSize from details, got {result.actual_fill_size}"
    )
