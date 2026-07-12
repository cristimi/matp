"""
Unit tests for actual_fill_size propagation in HyperliquidAdapter._place_order.

HL's filled.totalSz carries the total filled base size. When present it is
used directly; when absent the adapter falls back to the _round_size-rounded
submitted size so the DB always has a concrete number.
"""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.adapters.hyperliquid import HyperliquidAdapter
from app.models import OrderRequest

FAKE_CREDS = {"private_key": "0x" + "a" * 64}

# HYPE: szDecimals=2 → _round_size(3.456) = 3.46
HYPE_META = {
    "universe": [
        {"name": "HYPE", "szDecimals": 2, "maxLeverage": 10},
    ]
}
HYPE_ASSET_CTXS = [
    {"markPx": "50.00"},
]


def _make_adapter():
    return HyperliquidAdapter(FAKE_CREDS, mode="demo")


def _make_order(symbol="HYPE-USDT", size="3.456"):
    return OrderRequest(
        order_id="test-hl-fill",
        account_id="acc_test",
        symbol=symbol,
        side="buy",
        signal="open_long",
        order_type="market",
        size=Decimal(size),
        leverage=5,
        margin_mode="isolated",
    )


def _hl_exchange_response(total_sz=None):
    """Build a minimal HL /exchange response. total_sz=None omits the field."""
    filled = {"oid": 12345, "avgPx": "50.10"}
    if total_sz is not None:
        filled["totalSz"] = str(total_sz)
    return {
        "status": "ok",
        "response": {
            "type": "order",
            "data": {"statuses": [{"filled": filled}]},
        },
    }


async def _fake_meta_post(url, json=None, **kw):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if json and json.get("type") == "meta":
        resp.json.return_value = HYPE_META
    elif json and json.get("type") == "metaAndAssetCtxs":
        resp.json.return_value = [HYPE_META, HYPE_ASSET_CTXS]
    elif json and json.get("type") == "updateLeverage":
        resp.json.return_value = {"status": "ok"}
    else:
        resp.json.return_value = {"status": "ok", "response": {}}
    return resp


@pytest.mark.asyncio
async def test_hl_fill_size_from_total_sz():
    """When filled.totalSz is present, actual_fill_size must equal it exactly."""
    adapter = _make_adapter()
    order = _make_order(size="3.456")

    exchange_resp = _hl_exchange_response(total_sz="3.46")

    async def fake_post(url, json=None, **kw):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if json and json.get("type") in ("meta", "metaAndAssetCtxs", "updateLeverage"):
            return await _fake_meta_post(url, json=json)
        # /exchange order placement
        resp.json.return_value = exchange_resp
        return resp

    with patch.object(adapter, "_client", MagicMock(post=AsyncMock(side_effect=fake_post))):
        result = await adapter.submit_order(order)

    assert result.success, f"Expected success: {result}"
    assert result.actual_fill_size == Decimal("3.46"), (
        f"Expected totalSz=3.46, got {result.actual_fill_size}"
    )


@pytest.mark.asyncio
async def test_hl_fill_size_fallback_to_rounded_size():
    """When filled.totalSz is absent, actual_fill_size falls back to _round_size result.
    HYPE szDecimals=2, size=3.456 → _round_size → 3.46."""
    adapter = _make_adapter()
    order = _make_order(size="3.456")

    exchange_resp = _hl_exchange_response(total_sz=None)  # totalSz omitted

    async def fake_post(url, json=None, **kw):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if json and json.get("type") in ("meta", "metaAndAssetCtxs", "updateLeverage"):
            return await _fake_meta_post(url, json=json)
        resp.json.return_value = exchange_resp
        return resp

    with patch.object(adapter, "_client", MagicMock(post=AsyncMock(side_effect=fake_post))):
        result = await adapter.submit_order(order)

    assert result.success, f"Expected success: {result}"
    # szDecimals=2 → round(3.456, 2) = 3.46
    assert result.actual_fill_size == Decimal("3.46"), (
        f"Expected fallback 3.46, got {result.actual_fill_size}"
    )
