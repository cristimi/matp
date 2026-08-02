"""
modify-stops must PRESERVE a leg the caller did not price, not delete it.

Regression cover for the sol-ai-6486 take-profit loss: an `adjust_stops` carrying
only a new stop used to cancel every resting trigger and re-place just the SL,
permanently destroying the TP. See
.gemini/reports/sol-missing-tp-and-rr-zone-borders.md.

No network: the adapter and registry are stubbed.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.main import ModifyStopsRequest, modify_stops


class _Pos:
    def __init__(self, symbol, side, size):
        self.symbol = symbol
        self.side   = side
        self.size   = size


class _StubAdapter:
    """Minimal adapter: records what it was asked to place and cancel, and answers
    list_trigger_orders with whatever is currently 'resting'."""

    def __init__(self, resting):
        self.resting        = list(resting)
        self.cancelled      = []
        self.place_calls    = []
        # Net account: the route must NOT scope trigger calls to a leg, because a
        # net position is labelled "net" on the exchange and a long/short filter
        # would match nothing and read back as "no stops resting".
        self.hedge          = False
        self.last_position_side = "unset"

    async def get_open_positions(self):
        return [_Pos("SOL-USDT", "short", 2.66)]

    async def list_trigger_orders(self, symbol, position_side=None):
        # position_side is recorded rather than applied: this stub models a net
        # account (hedge=False below), where the route passes None.
        self.last_position_side = position_side
        return list(self.resting)

    async def cancel_order(self, symbol, oid):
        self.cancelled.append(oid)
        self.resting = [t for t in self.resting if t["oid"] != oid]
        return {"success": True}

    async def place_trigger_orders(self, symbol, trigger_side, size, tp_price, sl_price,
                                   position_side=None):
        self.place_calls.append({"tp": tp_price, "sl": sl_price, "size": size,
                                 "position_side": position_side})
        placed = []
        if sl_price is not None:
            self.resting.append({"oid": "new-sl", "tpsl": "sl", "triggerPx": str(sl_price)})
            placed.append({"tpsl": "sl", "oid": "new-sl"})
        if tp_price is not None:
            self.resting.append({"oid": "new-tp", "tpsl": "tp", "triggerPx": str(tp_price)})
            placed.append({"tpsl": "tp", "oid": "new-tp"})
        return {"placed": placed}


def _run(adapter, request):
    import asyncio
    with patch("app.main.registry") as reg:
        reg.get = AsyncMock(return_value=adapter)
        return asyncio.run(modify_stops("acct-1", request))


BOTH_LEGS = [
    {"oid": "old-sl", "tpsl": "sl", "triggerPx": "74.2559"},
    {"oid": "old-tp", "tpsl": "tp", "triggerPx": "72.3783"},
]


def test_stop_only_adjust_preserves_the_take_profit():
    """The exact sol-ai-6486 case: tp=None, sl=73.5."""
    adapter = _StubAdapter(BOTH_LEGS)
    result = _run(adapter, ModifyStopsRequest(
        symbol="SOL-USDT", side="short", sl_price=73.5,
    ))

    assert result["success"] is True
    # The TP was carried forward at its original price, not dropped.
    assert result["tp_ok"] is True
    assert result["sl_ok"] is True
    assert adapter.place_calls[0]["tp"] == 72.3783
    assert adapter.place_calls[0]["sl"] == 73.5
    assert result["preserved"] == [{"tpsl": "tp", "triggerPx": 72.3783}]
    # Both legs end up resting.
    assert sorted(t["tpsl"] for t in adapter.resting) == ["sl", "tp"]


def test_target_only_adjust_preserves_the_stop():
    """Symmetric: never leave a position unprotected by omitting sl_price."""
    adapter = _StubAdapter(BOTH_LEGS)
    result = _run(adapter, ModifyStopsRequest(
        symbol="SOL-USDT", side="short", tp_price=71.0,
    ))

    assert result["sl_ok"] is True
    assert adapter.place_calls[0]["sl"] == 74.2559
    assert result["preserved"] == [{"tpsl": "sl", "triggerPx": 74.2559}]


def test_explicit_clear_tp_still_removes_the_target():
    """Deleting a leg is still possible — it just has to be deliberate now."""
    adapter = _StubAdapter(BOTH_LEGS)
    result = _run(adapter, ModifyStopsRequest(
        symbol="SOL-USDT", side="short", sl_price=73.5, clear_tp=True,
    ))

    assert result["tp_ok"] is None            # no TP leg was in play
    assert adapter.place_calls[0]["tp"] is None
    assert result["preserved"] == []
    assert [t["tpsl"] for t in adapter.resting] == ["sl"]


def test_both_legs_priced_is_unchanged():
    """Every existing caller passes both — behaviour must be identical for them."""
    adapter = _StubAdapter(BOTH_LEGS)
    result = _run(adapter, ModifyStopsRequest(
        symbol="SOL-USDT", side="short", tp_price=71.0, sl_price=73.5,
    ))

    assert result["success"] is True
    assert adapter.place_calls[0] == {"tp": 71.0, "sl": 73.5, "size": 2.66,
                                      "position_side": None}
    assert result["preserved"] == []


def test_no_legs_requested_and_none_resting_touches_nothing():
    """Must not cancel a position's protection for no reason."""
    adapter = _StubAdapter([])
    result = _run(adapter, ModifyStopsRequest(symbol="SOL-USDT", side="short"))

    assert result["success"] is True
    assert adapter.cancelled == []
    assert adapter.place_calls == []


def test_net_account_scopes_nothing_to_a_leg():
    """A net position is labelled 'net' on the exchange. Filtering its triggers by
    'short' would return nothing and the route would read that as 'no stops'."""
    adapter = _StubAdapter(BOTH_LEGS)
    _run(adapter, ModifyStopsRequest(symbol="SOL-USDT", side="short", sl_price=73.5))

    assert adapter.last_position_side is None
    assert adapter.place_calls[0]["position_side"] is None


def test_hedge_account_scopes_every_trigger_call_to_the_leg_being_modified():
    """Step 3 cancels everything step 2 read. On a hedge account an unscoped read
    would hand the short's stop to the long's cancel loop and leave it naked."""
    adapter = _StubAdapter(BOTH_LEGS)
    adapter.hedge = True
    _run(adapter, ModifyStopsRequest(symbol="SOL-USDT", side="short", sl_price=73.5))

    assert adapter.last_position_side == "short"
    assert adapter.place_calls[0]["position_side"] == "short"


def test_unknown_trigger_read_still_refuses_to_proceed():
    """The pre-existing safety rule must survive: never cancel what we cannot see."""
    adapter = _StubAdapter(BOTH_LEGS)
    adapter.list_trigger_orders = AsyncMock(return_value=None)
    result = _run(adapter, ModifyStopsRequest(
        symbol="SOL-USDT", side="short", sl_price=73.5,
    ))

    assert result["success"] is False
    assert adapter.cancelled == []
    assert "refusing" in result["error_msg"].lower()
