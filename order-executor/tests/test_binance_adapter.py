"""
Unit tests for BinanceAdapter's pure logic — the parts that decide what goes on
the wire, without touching the exchange.

Everything network-facing is stubbed. What is pinned here is the behaviour that
would silently corrupt a trade if it drifted: signing, the rounding direction for
quantities, which orders count as triggers, and the fee/pnl sign conventions the
DB depends on.
"""
import hashlib
import hmac
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.adapters.base import MMR_CONSERVATISM_BUFFER
from app.adapters.binance import BinanceAdapter, _TRIGGER_TYPES

FAKE_CREDS = {"api_key": "testkey", "api_secret": "testsecret"}

BTC_SPEC = {
    "symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
    "tickSize": 0.1, "stepSize": 0.001, "minQty": 0.001, "minNotional": 5.0,
    "pricePrecision": 2, "quantityPrecision": 3,
}


def _adapter(mode="demo"):
    a = BinanceAdapter(FAKE_CREDS, mode=mode)
    BinanceAdapter._exchange_info[a.base_url] = {"BTCUSDT": BTC_SPEC}
    BinanceAdapter._exchange_info_ts[a.base_url] = 1e12   # far future: never refresh
    return a


# ── endpoints ─────────────────────────────────────────────────────────────────

def test_demo_mode_uses_the_current_testnet_host():
    # testnet.binancefuture.com is the retired host; demo-fapi is what the current
    # docs point at. Getting this wrong sends live orders from a demo account.
    assert _adapter("demo").base_url == "https://demo-fapi.binance.com"


def test_live_mode_uses_production():
    assert _adapter("live").base_url == "https://fapi.binance.com"


# ── signing ───────────────────────────────────────────────────────────────────

def test_signature_matches_hmac_of_the_exact_query_sent():
    a = _adapter()
    q = a._signed_query({"symbol": "BTCUSDT", "side": "BUY"})
    body, sig = q.rsplit("&signature=", 1)
    expected = hmac.new(b"testsecret", body.encode(), hashlib.sha256).hexdigest()
    assert sig == expected, "signature must cover the query string verbatim"


def test_signed_query_carries_timestamp_and_recv_window():
    q = _adapter()._signed_query({"symbol": "BTCUSDT"})
    assert "timestamp=" in q and "recvWindow=" in q


def test_none_valued_params_are_dropped_not_signed_as_empty():
    q = _adapter()._signed_query({"symbol": "BTCUSDT", "price": None})
    assert "price=" not in q


# ── rounding ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quantity_rounds_down_to_the_step():
    a = _adapter()
    # 0.0059 at a 0.001 step must become 0.005, never 0.006: rounding a size UP
    # can exceed margin, or turn a reduce-only close into a rejected oversize.
    assert await a._fmt_qty("BTCUSDT", 0.0059) == "0.005"


@pytest.mark.asyncio
async def test_quantity_step_uses_decimal_not_float_division():
    """Regression: BTCUSDT's real step is 0.0001, and math.floor(0.0059/0.0001)
    is 58 — the division lands on 58.99999999999999. That shipped 0.0058, 1.7%
    under, on every order. Caught against the live testnet, not in a fixture."""
    a = _adapter()
    BinanceAdapter._exchange_info[a.base_url]["BTCUSDT"] = {
        **BTC_SPEC, "stepSize": 0.0001, "minQty": 0.0001, "quantityPrecision": 4,
    }
    assert await a._fmt_qty("BTCUSDT", 0.0059) == "0.0059"
    assert await a._fmt_qty("BTCUSDT", 0.00295) == "0.0029"
    BinanceAdapter._exchange_info[a.base_url]["BTCUSDT"] = dict(BTC_SPEC)


@pytest.mark.asyncio
async def test_quantity_below_one_step_rounds_to_zero():
    a = _adapter()
    assert float(await a._fmt_qty("BTCUSDT", 0.0004)) == 0.0


@pytest.mark.asyncio
async def test_price_rounds_to_nearest_tick():
    a = _adapter()
    # Prices have no safe direction — Binance rejects a non-multiple outright.
    assert await a._fmt_price("BTCUSDT", 64000.04) == "64000"
    assert await a._fmt_price("BTCUSDT", 64000.06) == "64000.1"


# ── trigger classification ────────────────────────────────────────────────────

def test_stop_and_take_profit_variants_all_count_as_triggers():
    for t in ("STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET",
              "TRAILING_STOP_MARKET"):
        assert t in _TRIGGER_TYPES
    assert "LIMIT" not in _TRIGGER_TYPES and "MARKET" not in _TRIGGER_TYPES


@pytest.mark.asyncio
async def test_open_orders_excludes_triggers_and_trigger_list_excludes_book_orders():
    a = _adapter()
    raw = [
        {"orderId": 1, "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
         "price": "64000", "origQty": "0.01", "executedQty": "0", "time": 111},
        {"orderId": 2, "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET",
         "stopPrice": "60000", "origQty": "0", "closePosition": True, "time": 222},
        {"orderId": 3, "symbol": "BTCUSDT", "side": "SELL", "type": "TAKE_PROFIT_MARKET",
         "stopPrice": "70000", "origQty": "0", "closePosition": True, "time": 333},
    ]
    with patch.object(a, "_private", AsyncMock(return_value=raw)):
        book = await a.get_open_orders("BTCUSDT")
        trig = await a.list_trigger_orders("BTCUSDT")

    assert [o["order_id"] for o in book] == ["1"]
    assert {t["tpsl"] for t in trig} == {"sl", "tp"}
    assert [t["oid"] for t in trig] == ["2", "3"]
    # A closePosition trigger has no fixed size; say so rather than reporting 0.
    assert all(t["sz"] == "position" for t in trig)


@pytest.mark.asyncio
async def test_list_trigger_orders_returns_none_on_failure_not_empty():
    a = _adapter()
    with patch.object(a, "_private", AsyncMock(side_effect=RuntimeError("boom"))):
        assert await a.list_trigger_orders("BTCUSDT") is None


@pytest.mark.asyncio
async def test_open_orders_returns_empty_list_on_failure():
    a = _adapter()
    with patch.object(a, "_private", AsyncMock(side_effect=RuntimeError("boom"))):
        assert await a.get_open_orders("BTCUSDT") == []


# ── fills, pnl and fees ───────────────────────────────────────────────────────

def test_trade_aggregation_is_volume_weighted_with_gross_pnl_and_positive_fee():
    trades = [
        {"price": "100", "qty": "1", "realizedPnl": "5",  "commission": "0.04"},
        {"price": "110", "qty": "3", "realizedPnl": "15", "commission": "0.12"},
    ]
    agg = BinanceAdapter._aggregate_trades(trades)
    assert agg["size"] == Decimal("4")
    assert agg["price"] == Decimal(str((100 * 1 + 110 * 3) / 4))
    # realizedPnl excludes commission on Binance, so the sum is already GROSS —
    # which is what callers subtract their own fees from.
    assert agg["pnl"] == Decimal("20")
    # Positive means paid, matching the DB convention.
    assert agg["fee"] == Decimal("0.16")


def test_trade_aggregation_of_nothing_is_empty_not_a_divide_by_zero():
    assert BinanceAdapter._aggregate_trades([]) == {}


@pytest.mark.asyncio
async def test_closed_position_details_reports_close_only_fee_scope():
    a = _adapter()
    trades = [
        {"price": "100", "qty": "1", "realizedPnl": "0",  "commission": "0.04", "time": 1},
        {"price": "120", "qty": "1", "realizedPnl": "20", "commission": "0.05", "time": 2},
    ]
    with patch.object(a, "_get_trades", AsyncMock(return_value=trades)), \
         patch.object(a, "_was_liquidated", AsyncMock(return_value=False)):
        d = await a.get_closed_position_details("BTCUSDT")

    # Only the leg that moved realized PnL is a closing leg, so the opening fill's
    # fee is not counted here — the caller already holds it on the opening order.
    assert d["fee_scope"] == "close_only"
    assert d["pnl_realized"] == Decimal("20")
    assert d["fee"] == Decimal("0.05")
    assert d["closing_price"] == Decimal("120")
    assert d["close_reason"] == "Closed on exchange"


@pytest.mark.asyncio
async def test_closed_position_details_flags_a_liquidation():
    a = _adapter()
    trades = [{"price": "90", "qty": "1", "realizedPnl": "-10", "commission": "0.05", "time": 9}]
    with patch.object(a, "_get_trades", AsyncMock(return_value=trades)), \
         patch.object(a, "_was_liquidated", AsyncMock(return_value=True)):
        d = await a.get_closed_position_details("BTCUSDT")
    assert d["close_reason"] == "Liquidated"


# ── risk ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_maintenance_margin_picks_the_right_tier_and_adds_the_buffer():
    a = _adapter()
    brackets = [
        {"notionalFloor": 0,      "notionalCap": 50000,  "maintMarginRatio": 0.004, "initialLeverage": 125},
        {"notionalFloor": 50000,  "notionalCap": 250000, "maintMarginRatio": 0.005, "initialLeverage": 100},
        {"notionalFloor": 250000, "notionalCap": 1e6,    "maintMarginRatio": 0.01,  "initialLeverage": 50},
    ]
    with patch.object(a, "_get_brackets", AsyncMock(return_value=brackets)):
        assert await a.get_maintenance_margin_rate("BTCUSDT", 10_000) == pytest.approx(
            0.004 + MMR_CONSERVATISM_BUFFER)
        assert await a.get_maintenance_margin_rate("BTCUSDT", 300_000) == pytest.approx(
            0.01 + MMR_CONSERVATISM_BUFFER)


@pytest.mark.asyncio
async def test_maintenance_margin_is_none_when_unknown_never_zero():
    a = _adapter()
    with patch.object(a, "_get_brackets", AsyncMock(return_value=[])):
        assert await a.get_maintenance_margin_rate("BTCUSDT", 10_000) is None


@pytest.mark.asyncio
async def test_max_leverage_comes_from_the_brackets():
    a = _adapter()
    brackets = [{"initialLeverage": 125}, {"initialLeverage": 100}]
    with patch.object(a, "_get_brackets", AsyncMock(return_value=brackets)):
        assert await a.get_max_leverage("BTCUSDT") == 125


# ── order construction ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_limit_goes_out_post_only_and_close_limit_does_not():
    from app.models import OrderRequest
    a = _adapter()
    sent = []

    async def fake_private(method, path, params=None):
        sent.append(params)
        return {"orderId": 7, "status": "NEW"}

    with patch.object(a, "_private", AsyncMock(side_effect=fake_private)), \
         patch.object(a, "_check_position_mode", AsyncMock(return_value=None)), \
         patch.object(a, "get_max_leverage", AsyncMock(return_value=125)), \
         patch.object(a, "_set_margin_type", AsyncMock()), \
         patch.object(a, "_set_leverage", AsyncMock()):
        await a.submit_order(OrderRequest(
            order_id="1", account_id="x", symbol="BTCUSDT", side="buy",
            signal="open_long", order_type="limit", size=Decimal("0.01"),
            price=Decimal("64000"), leverage=10, margin_mode="isolated"))
        await a.submit_order(OrderRequest(
            order_id="2", account_id="x", symbol="BTCUSDT", side="sell",
            signal="close_long", order_type="limit", size=Decimal("0.01"),
            price=Decimal("64000"), leverage=10, margin_mode="isolated"))

    # GTX = post-only: a taker fill on an entry lands at market, at or through the
    # stop derived from the intended limit price.
    assert sent[0]["timeInForce"] == "GTX"
    assert "reduceOnly" not in sent[0]
    assert sent[1]["timeInForce"] == "GTC"
    assert sent[1]["reduceOnly"] == "true"


@pytest.mark.asyncio
async def test_expired_post_only_entry_is_rejected_not_reported_filled():
    from app.models import OrderRequest
    a = _adapter()
    with patch.object(a, "_private", AsyncMock(return_value={"orderId": 9, "status": "EXPIRED"})), \
         patch.object(a, "_check_position_mode", AsyncMock(return_value=None)), \
         patch.object(a, "get_max_leverage", AsyncMock(return_value=125)), \
         patch.object(a, "_set_margin_type", AsyncMock()), \
         patch.object(a, "_set_leverage", AsyncMock()):
        r = await a.submit_order(OrderRequest(
            order_id="1", account_id="x", symbol="BTCUSDT", side="buy",
            signal="open_long", order_type="limit", size=Decimal("0.01"),
            price=Decimal("64000"), leverage=10, margin_mode="isolated"))
    assert r.success is False and r.status == "rejected"
    assert "post-only" in r.error_msg


@pytest.mark.asyncio
async def test_leverage_above_the_exchange_max_is_rejected_not_clamped():
    from app.models import OrderRequest
    a = _adapter()
    with patch.object(a, "_check_position_mode", AsyncMock(return_value=None)), \
         patch.object(a, "get_max_leverage", AsyncMock(return_value=20)):
        r = await a.submit_order(OrderRequest(
            order_id="1", account_id="x", symbol="BTCUSDT", side="buy",
            signal="open_long", order_type="market", size=Decimal("0.01"),
            leverage=50, margin_mode="isolated"))
    assert r.success is False and "exceeds Binance max 20x" in r.error_msg


@pytest.mark.asyncio
async def test_hedge_mode_account_is_refused_before_any_order_is_sent():
    from app.models import OrderRequest
    a = _adapter()
    sent = []
    with patch.object(a, "_private", AsyncMock(side_effect=lambda *args, **kw: sent.append(args))), \
         patch.object(a, "_check_position_mode", AsyncMock(return_value="account is in Hedge Mode")):
        r = await a.submit_order(OrderRequest(
            order_id="1", account_id="x", symbol="BTCUSDT", side="buy",
            signal="open_long", order_type="market", size=Decimal("0.01"),
            leverage=10, margin_mode="isolated"))
    assert r.success is False and "Hedge Mode" in r.error_msg
    assert sent == [], "nothing may reach the exchange once hedge mode is detected"


@pytest.mark.asyncio
async def test_triggers_close_the_whole_position_rather_than_a_fixed_size():
    a = _adapter()
    sent = []

    async def fake_private(method, path, params=None):
        sent.append(params)
        return {"orderId": 5}

    with patch.object(a, "_private", AsyncMock(side_effect=fake_private)):
        out = await a.place_trigger_orders("BTCUSDT", "sell", 0.01,
                                           tp_price=70000, sl_price=60000)

    assert out["success"] is True
    assert {p["type"] for p in sent} == {"TAKE_PROFIT_MARKET", "STOP_MARKET"}
    for p in sent:
        # closePosition keeps the stop correct after a partial close and lets the
        # exchange cancel it when the position goes flat.
        assert p["closePosition"] == "true"
        assert "quantity" not in p
        assert p["workingType"] == "MARK_PRICE"


@pytest.mark.asyncio
async def test_a_failed_trigger_leg_makes_the_whole_placement_unsuccessful():
    a = _adapter()
    with patch.object(a, "_private", AsyncMock(return_value={"code": -2021, "msg": "Order would immediately trigger."})):
        out = await a.place_trigger_orders("BTCUSDT", "sell", 0.01, sl_price=60000)
    assert out["success"] is False


@pytest.mark.asyncio
async def test_positions_read_failure_raises_rather_than_reporting_flat():
    from app.adapters.base import ExchangeUnavailableError
    a = _adapter()
    with patch.object(a, "_private", AsyncMock(side_effect=RuntimeError("network"))):
        with pytest.raises(ExchangeUnavailableError):
            await a.get_open_positions()


@pytest.mark.asyncio
async def test_zero_size_positions_are_skipped_and_sides_derived_from_sign():
    a = _adapter()
    raw = [
        {"symbol": "BTCUSDT", "positionAmt": "0",     "entryPrice": "0",     "leverage": "10"},
        {"symbol": "ETHUSDT", "positionAmt": "-1.5",  "entryPrice": "2000",  "leverage": "20",
         "markPrice": "1990", "unRealizedProfit": "15", "liquidationPrice": "2400"},
    ]
    with patch.object(a, "_private", AsyncMock(return_value=raw)):
        out = await a.get_open_positions()
    assert len(out) == 1
    assert out[0].symbol == "ETHUSDT" and out[0].side == "short"
    assert out[0].size == Decimal("1.5")


@pytest.mark.asyncio
async def test_balance_never_raises_and_reports_the_error():
    a = _adapter()
    with patch.object(a, "_private", AsyncMock(side_effect=RuntimeError("down"))):
        b = await a.get_balance()
    assert b["total_balance"] == 0.0 and "error" in b and b["currency"] == "USDT"


@pytest.mark.asyncio
async def test_account_meta_never_leaks_the_secret():
    meta = await _adapter().get_account_meta()
    assert meta["api_key"] == "testkey"
    assert "testsecret" not in str(meta)
