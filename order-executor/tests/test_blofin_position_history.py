"""
Unit tests for BlofinAdapter.get_closed_position_details.

Blofin's positions-history reports `realizedPnl` ALREADY NET of the position's whole
round-trip fee, and reports that fee as a NEGATIVE round-trip total. Callers subtract fees
themselves and the DB stores fees positive, so the adapter must hand back a GROSS pnl and a
positive fee tagged 'round_trip'.

The payload below is a real response captured from the live demo account (position
9fe0f6bc, BNB-USDT), where the per-order fees we booked — 0.06107616 + 0.0033912 +
0.0577218 — sum to exactly the 0.12218916 reported here, confirming it is a round-trip
total and not the closing leg alone.

No network calls: the adapter's pooled _client is patched.
"""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.adapters.blofin import BlofinAdapter

FAKE_CREDS = {
    "api_key":        "test_key",
    "api_secret":     "test_secret",
    "api_passphrase": "test_pass",
}

LIVE_ENTRY = {
    "historyId":             "100863258",
    "positionId":            "1000000297888",
    "instId":                "BNB-USDT",
    "instType":              "SWAP",
    "marginMode":            "isolated",
    "positionSide":          "net",
    "closePositions":        "0.18",
    "maxPositions":          "0.18",
    "liquidationPositions":  "0",
    "openAveragePrice":      "565.52",
    "closeAveragePrice":     "565.861111111111111111",
    "createTime":            "1784943977789",
    "updateTime":            "1784949370082",
    "leverage":              "10",
    "realizedPnl":           "-0.06078916",
    "realizedPnlRatio":      "-0.00597180569308876",
    "fee":                   "-0.12218916",
}


def _adapter_with(entries):
    adapter = BlofinAdapter(FAKE_CREDS, mode="demo")
    resp = MagicMock()
    resp.json = MagicMock(return_value={"data": entries})
    adapter._client = MagicMock()
    adapter._client.get = AsyncMock(return_value=resp)
    return adapter


@pytest.mark.asyncio
async def test_pnl_is_returned_gross_not_net():
    """realizedPnl is net of the round trip; callers need gross, so the fee comes back off.

    gross = (565.861111 - 565.52) * 0.18 = +0.0614, and -0.06078916 - (-0.12218916)
    recovers exactly that.
    """
    details = await _adapter_with([LIVE_ENTRY]).get_closed_position_details("BNB-USDT")
    assert details["pnl_realized"] == pytest.approx(Decimal("0.0614"), abs=Decimal("1e-8"))


@pytest.mark.asyncio
async def test_fee_is_positive_magnitude():
    """Blofin signs fees negative; the DB convention is positive-means-paid."""
    details = await _adapter_with([LIVE_ENTRY]).get_closed_position_details("BNB-USDT")
    assert details["fee"] == Decimal("0.12218916")
    assert details["fee"] > 0


@pytest.mark.asyncio
async def test_fee_scope_is_round_trip():
    """Callers must know the opening fill's fee is included, or they double-count it."""
    details = await _adapter_with([LIVE_ENTRY]).get_closed_position_details("BNB-USDT")
    assert details["fee_scope"] == "round_trip"


@pytest.mark.asyncio
async def test_gross_minus_fee_reproduces_exchange_net():
    """The round trip must be lossless: gross - fee == Blofin's own realizedPnl."""
    details = await _adapter_with([LIVE_ENTRY]).get_closed_position_details("BNB-USDT")
    net = details["pnl_realized"] - details["fee"]
    assert net == pytest.approx(Decimal("-0.06078916"), abs=Decimal("1e-8"))


@pytest.mark.asyncio
async def test_liquidation_is_flagged():
    entry = dict(LIVE_ENTRY, liquidationPositions="1")
    details = await _adapter_with([entry]).get_closed_position_details("BNB-USDT")
    assert details["close_reason"] == "Liquidated"


@pytest.mark.asyncio
async def test_no_history_returns_none():
    assert await _adapter_with([]).get_closed_position_details("BNB-USDT") is None
