"""
Unit tests for BlofinAdapter in hedge (long_short_mode) accounts.

The whole feature rests on one field — positionSide — being right on every call.
BloFin rejects "net" at a hedge account and rejects "long"/"short" at a net one, and
where it does NOT reject (close-position, which takes no size and no position id)
naming the wrong side flattens the wrong half of the pair. So every wire payload is
asserted here, and every net-mode payload is re-asserted unchanged so switching one
account cannot alter another.

No network calls: the pooled _client and _get_instrument are patched.
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

HYPE_SPEC = {
    "instId":        "HYPE-USDT",
    "contractValue": "1",
    "lotSize":       "0.1",
    "minSize":       "0.1",
    "maxLeverage":   "50",
}


def _adapter(position_mode="hedge"):
    return BlofinAdapter(FAKE_CREDS, mode="demo", position_mode=position_mode)


def _order(side="buy", signal="open_long", size="1.5", symbol="HYPE-USDT"):
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


def _ok_response(payload=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload if payload is not None else {
        "code": "0", "data": [{"orderId": "999"}],
    }
    return resp


def _fill_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "code": "0",
        "data": [{"orderId": "999", "avgPrice": "68.50", "pnl": "0.5",
                  "filledSize": "1.5", "fee": "-0.01"}],
    }
    return resp


async def _run_submit(adapter, order):
    """Submit an order against a mocked client; return (order_bodies, leverage_bodies)."""
    order_bodies, leverage_bodies = [], []

    async def fake_post(url, content=None, headers=None, **kw):
        body = json.loads(content) if content else {}
        if "set-leverage" in url:
            leverage_bodies.append(body)
        else:
            order_bodies.append(body)
        return _ok_response()

    async def fake_get(url, headers=None, **kw):
        return _fill_response()

    client = MagicMock(post=AsyncMock(side_effect=fake_post),
                       get=AsyncMock(side_effect=fake_get))
    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_client", client):
        result = await adapter.submit_order(order)
    assert result.success, f"submit_order failed: {result}"
    return order_bodies, leverage_bodies


# ── entries ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("signal,side,expected", [
    ("open_long",  "buy",  "long"),
    ("open_short", "sell", "short"),
])
async def test_hedge_entry_carries_position_side(signal, side, expected):
    """A hedge entry must say which leg it grows — the exchange rejects it otherwise."""
    bodies, _ = await _run_submit(_adapter("hedge"), _order(side=side, signal=signal))
    assert bodies[0]["positionSide"] == expected
    assert "reduceOnly" not in bodies[0], "an entry must never be reduce-only"


@pytest.mark.asyncio
async def test_net_entry_payload_is_unchanged():
    """Regression: net accounts must keep sending exactly what they sent before."""
    bodies, _ = await _run_submit(_adapter("net"), _order(side="buy", signal="open_long"))
    assert "positionSide" not in bodies[0]
    assert "reduceOnly" not in bodies[0]


# ── closes via submit_order ──────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("signal,side,expected", [
    ("close_long",  "sell", "long"),
    ("close_short", "buy",  "short"),
])
async def test_hedge_close_names_the_leg_being_closed(signal, side, expected):
    """A close is a sell/buy order against the OPPOSITE-named leg — long is closed
    by a sell, and positionSide must still read 'long'."""
    bodies, _ = await _run_submit(_adapter("hedge"), _order(side=side, signal=signal))
    assert bodies[0]["positionSide"] == expected
    assert bodies[0]["reduceOnly"] == "true"


@pytest.mark.asyncio
async def test_net_close_still_says_net():
    bodies, _ = await _run_submit(_adapter("net"), _order(side="sell", signal="close_long"))
    assert bodies[0]["positionSide"] == "net"
    assert bodies[0]["reduceOnly"] == "true"


# ── leverage is per leg in hedge mode ────────────────────────────────────────

@pytest.mark.asyncio
async def test_hedge_leverage_is_set_on_the_right_leg_only():
    """Setting leverage without a side would move BOTH legs on a hedge account."""
    _, lev = await _run_submit(_adapter("hedge"), _order(side="sell", signal="open_short"))
    assert lev and lev[0]["positionSide"] == "short"
    assert lev[0]["leverage"] == "20"


@pytest.mark.asyncio
async def test_net_leverage_still_says_net():
    _, lev = await _run_submit(_adapter("net"), _order(side="buy", signal="open_long"))
    assert lev and lev[0]["positionSide"] == "net"


# ── full close ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("mode,side,expected", [
    ("hedge", "long",  "long"),
    ("hedge", "short", "short"),
    ("net",   "long",  "net"),
    ("net",   "short", "net"),
])
async def test_close_position_targets_the_right_leg(mode, side, expected):
    """close-position takes no size and no position id: positionSide is the ONLY
    thing separating the two legs, so a wrong value flattens the wrong one."""
    adapter = _adapter(mode)
    bodies = []

    async def fake_post(url, content=None, headers=None, **kw):
        bodies.append(json.loads(content))
        return _ok_response({"code": "0", "data": {"orderId": "555"}})

    async def fake_get(url, headers=None, **kw):
        return _fill_response()

    client = MagicMock(post=AsyncMock(side_effect=fake_post),
                       get=AsyncMock(side_effect=fake_get))
    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "get_open_positions", AsyncMock(return_value=[])), \
         patch.object(adapter, "_client", client):
        result = await adapter.close_position("HYPE-USDT", side)

    assert result.success
    assert bodies[0]["positionSide"] == expected
    assert bodies[0]["instId"] == "HYPE-USDT"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,side,expected_pos,expected_order_side", [
    ("hedge", "long",  "long",  "sell"),
    ("hedge", "short", "short", "buy"),
    ("net",   "long",  "net",   "sell"),
])
async def test_partial_close_targets_the_right_leg(mode, side, expected_pos, expected_order_side):
    adapter = _adapter(mode)
    bodies = []

    async def fake_post(url, content=None, headers=None, **kw):
        bodies.append(json.loads(content))
        return _ok_response()

    async def fake_get(url, headers=None, **kw):
        return _fill_response()

    client = MagicMock(post=AsyncMock(side_effect=fake_post),
                       get=AsyncMock(side_effect=fake_get))
    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_client", client):
        result = await adapter.close_position("HYPE-USDT", side, size=Decimal("0.5"))

    assert result.success
    assert bodies[0]["positionSide"] == expected_pos
    assert bodies[0]["side"] == expected_order_side
    assert bodies[0]["reduceOnly"] == "true"


# ── reading positions back ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hedge_positions_are_split_by_position_side_not_by_sign():
    """Both hedge legs report a POSITIVE quantity, so the old sign test would have
    called the short a long. positionSide is the only reliable discriminator."""
    adapter = _adapter("hedge")
    payload = {"code": "0", "data": [
        {"instId": "HYPE-USDT", "positionSide": "long",  "positions": "3",
         "averagePrice": "68.0", "markPrice": "69.0", "unrealizedPnl": "3", "lever": "20"},
        {"instId": "HYPE-USDT", "positionSide": "short", "positions": "2",
         "averagePrice": "70.0", "markPrice": "69.0", "unrealizedPnl": "2", "lever": "20"},
    ]}
    client = MagicMock(get=AsyncMock(return_value=_ok_response(payload)))
    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_client", client):
        positions = await adapter.get_open_positions()

    by_side = {p.side: p for p in positions}
    assert set(by_side) == {"long", "short"}, "both legs must survive as distinct sides"
    assert by_side["long"].size == Decimal("3")
    assert by_side["short"].size == Decimal("2"), "a short's size must be positive, not -2"


@pytest.mark.asyncio
async def test_net_positions_still_read_side_from_the_sign():
    """Regression: net accounts report positionSide='net' and encode the side in the
    sign of the quantity."""
    adapter = _adapter("net")
    payload = {"code": "0", "data": [
        {"instId": "HYPE-USDT", "positionSide": "net", "positions": "-4",
         "averagePrice": "70.0", "markPrice": "69.0", "unrealizedPnl": "4", "lever": "20"},
    ]}
    client = MagicMock(get=AsyncMock(return_value=_ok_response(payload)))
    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_client", client):
        positions = await adapter.get_open_positions()

    assert len(positions) == 1
    assert positions[0].side == "short"
    assert positions[0].size == Decimal("4")


# ── stop-loss / take-profit triggers ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_trigger_orders_returns_only_the_named_leg():
    """modify-stops CANCELS everything this returns. Unscoped on a hedge account it
    would take the other leg's stop down with it."""
    adapter = _adapter("hedge")
    payload = {"code": "0", "data": [
        {"tpslId": "1", "positionSide": "long",  "slTriggerPrice": "60", "size": "3"},
        {"tpslId": "2", "positionSide": "short", "slTriggerPrice": "80", "size": "2"},
    ]}
    client = MagicMock(get=AsyncMock(return_value=_ok_response(payload)))
    with patch.object(adapter, "_client", client):
        longs = await adapter.list_trigger_orders("HYPE-USDT", position_side="long")
        both  = await adapter.list_trigger_orders("HYPE-USDT")

    assert [t["oid"] for t in longs] == ["1"]
    assert {t["oid"] for t in both} == {"1", "2"}, "unscoped must still return everything"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger_side,expected", [("sell", "long"), ("buy", "short")])
async def test_hedge_triggers_name_the_leg_they_protect(trigger_side, expected):
    """A sell trigger protects a LONG. Inferring it from trigger_side is what keeps
    an SL attached to the position it belongs to when no side is passed."""
    adapter = _adapter("hedge")
    bodies = []

    async def fake_post(url, content=None, headers=None, **kw):
        bodies.append(json.loads(content))
        return _ok_response({"code": "0", "data": {"tpslId": "77"}})

    client = MagicMock(post=AsyncMock(side_effect=fake_post))
    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_client", client):
        result = await adapter.place_trigger_orders(
            "HYPE-USDT", trigger_side, size=1.0, sl_price=60.0
        )

    assert result["success"]
    assert bodies[0]["positionSide"] == expected


@pytest.mark.asyncio
async def test_net_triggers_send_no_position_side():
    adapter = _adapter("net")
    bodies = []

    async def fake_post(url, content=None, headers=None, **kw):
        bodies.append(json.loads(content))
        return _ok_response({"code": "0", "data": {"tpslId": "77"}})

    client = MagicMock(post=AsyncMock(side_effect=fake_post))
    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_client", client):
        await adapter.place_trigger_orders("HYPE-USDT", "sell", size=1.0, sl_price=60.0)

    assert "positionSide" not in bodies[0]


# ── closed-position history ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_closed_position_history_picks_the_requested_leg():
    """This number is booked as realised PnL. On a hedge account the wrong leg's
    figure would mis-book both positions."""
    adapter = _adapter("hedge")
    payload = {"code": "0", "data": [
        {"positionSide": "short", "realizedPnl": "-5", "fee": "-1",
         "closeAveragePrice": "70", "updateTime": "2000", "liquidationPositions": "0"},
        {"positionSide": "long",  "realizedPnl": "12", "fee": "-1",
         "closeAveragePrice": "69", "updateTime": "1999", "liquidationPositions": "0"},
    ]}
    client = MagicMock(get=AsyncMock(return_value=_ok_response(payload)))
    with patch.object(adapter, "_client", client):
        long_side = await adapter.get_closed_position_details("HYPE-USDT", side="long")
        unscoped  = await adapter.get_closed_position_details("HYPE-USDT")

    assert long_side["pnl_realized"] == Decimal("13")   # 12 - (-1)
    assert unscoped["pnl_realized"] == Decimal("-4")    # first entry, the short


@pytest.mark.asyncio
async def test_recover_close_fill_ignores_the_other_leg():
    """Both legs can post a reduce-only fill of the same size in the same instant.
    Without the positionSide filter that is an ambiguous tie and the fill is lost."""
    adapter = _adapter("hedge")
    payload = {"code": "0", "data": [
        {"orderId": "A", "reduceOnly": "true", "side": "sell", "state": "filled",
         "positionSide": "long",  "createTime": "1000", "filledSize": "3"},
        {"orderId": "B", "reduceOnly": "true", "side": "sell", "state": "filled",
         "positionSide": "short", "createTime": "1000", "filledSize": "3"},
    ]}
    client = MagicMock(get=AsyncMock(return_value=_ok_response(payload)))
    with patch.object(adapter, "_get_instrument", AsyncMock(return_value=HYPE_SPEC)), \
         patch.object(adapter, "_client", client):
        found = await adapter._recover_close_fill(
            "HYPE-USDT", "sell", None, since_ms=1000, position_side="long"
        )
        ambiguous = await adapter._recover_close_fill(
            "HYPE-USDT", "sell", None, since_ms=1000
        )

    assert found and found["orderId"] == "A"
    assert ambiguous is None, "unscoped, the two legs tie and must not be guessed between"


# ── the mode itself ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("wire,expected", [
    ("long_short_mode", "hedge"),
    ("net_mode",        "net"),
    ("something_else",  None),
])
async def test_get_position_mode_maps_blofin_wire_values(wire, expected):
    adapter = _adapter("net")
    client = MagicMock(get=AsyncMock(return_value=_ok_response(
        {"code": "0", "data": {"positionMode": wire}}
    )))
    with patch.object(adapter, "_client", client):
        assert await adapter.get_position_mode() == expected


@pytest.mark.asyncio
async def test_get_position_mode_returns_none_on_failure_not_net():
    """A failed read must never masquerade as 'net' — that would send net-shaped
    orders to a hedge account and have every one rejected."""
    adapter = _adapter("net")
    client = MagicMock(get=AsyncMock(side_effect=RuntimeError("network down")))
    with patch.object(adapter, "_client", client):
        assert await adapter.get_position_mode() is None


@pytest.mark.asyncio
async def test_set_position_mode_requires_agreement_on_read_back():
    """The exchange accepting the call is not proof it took effect."""
    adapter = _adapter("net")
    client = MagicMock(post=AsyncMock(return_value=_ok_response({"code": "0"})))
    with patch.object(adapter, "_client", client), \
         patch.object(adapter, "get_position_mode", AsyncMock(return_value="net")):
        result = await adapter.set_position_mode("hedge")

    assert result["success"] is False
    assert "reads back" in result["error"]
    assert adapter.position_mode == "net", "the live adapter must not flip on a failed switch"


@pytest.mark.asyncio
async def test_set_position_mode_updates_the_live_adapter_on_success():
    adapter = _adapter("net")
    client = MagicMock(post=AsyncMock(return_value=_ok_response({"code": "0"})))
    with patch.object(adapter, "_client", client), \
         patch.object(adapter, "get_position_mode", AsyncMock(return_value="hedge")):
        result = await adapter.set_position_mode("hedge")

    assert result["success"] is True
    assert adapter.position_mode == "hedge"
    assert adapter.hedge is True


@pytest.mark.asyncio
async def test_set_position_mode_passes_through_the_exchange_refusal():
    """BloFin refuses the switch while anything is open. That refusal is a safety
    property, not an error to paper over."""
    adapter = _adapter("net")
    client = MagicMock(post=AsyncMock(return_value=_ok_response(
        {"code": "103003", "msg": "Please close all positions and orders first"}
    )))
    with patch.object(adapter, "_client", client):
        result = await adapter.set_position_mode("hedge")

    assert result["success"] is False
    assert "close all positions" in result["error"]


def test_position_side_refuses_to_guess_on_a_hedge_account():
    """Better a loud failure than a silent order against the wrong leg."""
    adapter = _adapter("hedge")
    assert adapter._position_side("long") == "long"
    with pytest.raises(ValueError):
        adapter._position_side(None)
    with pytest.raises(ValueError):
        adapter._position_side("net")


def test_position_side_ignores_the_argument_on_a_net_account():
    adapter = _adapter("net")
    assert adapter._position_side("long") == "net"
    assert adapter._position_side(None) == "net"
