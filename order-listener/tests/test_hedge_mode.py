"""
Unit tests for hedge-account behaviour in order-listener.

Three decisions change when an account holds a long and a short side by side:

  1. the same-symbol guard must stop rejecting the OPPOSITE side (it is a second
     leg now, not a collision) while still rejecting the SAME side (two strategies
     long the same coin still merge into one exchange position with no way to
     attribute size);
  2. the flip-close inference — "this entry came back with PnL, so the exchange
     must have netted away the opposite leg" — is simply false, and acting on it
     would close a live position in the DB while it still runs on the exchange;
  3. target_position=flat must flatten the leg its signal names, and every leg
     when no signal names one — not the first one it happens to find.

And adjust-stops must be told which leg it is moving, or it moves whichever
position opened most recently.

Every one of these fails CLOSED on an unreadable mode: unknown reads as "net".

No live services — pool, executor and helpers are mocked.
"""
import json
import uuid
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch


# ── stubs ────────────────────────────────────────────────────────────────────

def _make_pool(conflict_rows=None, open_legs=None):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch    = AsyncMock(return_value=list(conflict_rows or open_legs or []))
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute  = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__  = AsyncMock(return_value=False)
    pool._conn = conn
    return pool


STRATEGY = {
    "id":         "test-strat",
    "account_id": "acc_test",
    "config":     None,
    "pnl_total":  0.0,
    "pnl_today":  0.0,
}


def _payload(signal="open_long", side="buy", size="0.5", target_position=None):  # noqa: D401
    from app.models import WebhookPayload
    return WebhookPayload(
        base_asset="BTC",
        quote_asset="USDT",
        side=side,
        signal=signal,
        order_type="market",
        size=Decimal(size),
        timestamp="2026-08-02T00:00:00Z",
        token="secret",
        target_position=target_position,
    )


def _resolved(symbol="BTC-USDT"):
    r = MagicMock()
    r.execution_symbol = symbol
    r.coupling_used    = None
    r.price_stripped   = False
    return r


async def _run(payload, pool, position_mode, close_result=None, exec_result=None):
    """Drive _process_order with everything downstream of the guard stubbed out.

    Returns (status_mock, close_mock) — the status the order was written with is
    what tells us whether the guard rejected, and with which reason.
    """
    from app.webhook_handler import _process_order

    update_mock = AsyncMock()
    close_mock  = AsyncMock(return_value=close_result or {
        "success": True, "status": "filled",
        "actual_fill_price": Decimal("60000"), "realized_pnl": Decimal("0"),
        "is_full_close": True,
    })
    executor_mock = AsyncMock(return_value=exec_result or {
        "success": True, "status": "filled", "actual_fill_price": "60000",
    })

    with patch("app.webhook_handler._account_position_mode",
               AsyncMock(return_value=position_mode)), \
         patch("app.webhook_handler.close_strategy_position", close_mock), \
         patch("app.webhook_handler._update_order_status", update_mock), \
         patch("app.webhook_handler._finalize_signal_log", AsyncMock()), \
         patch("app.webhook_handler.publish", AsyncMock()), \
         patch("app.webhook_handler._apply_position_fill", AsyncMock()), \
         patch("app.executor_client.call_executor", executor_mock), \
         patch("app.executor_client.call_executor_get",
               AsyncMock(return_value={"min_base_size": 0.0})):
        await _process_order(
            pool, uuid.uuid4(), payload, STRATEGY, _resolved(),
            price=None, tp_price=None, sl_price=None,
            effective_leverage=10, effective_margin_mode="isolated",
            signal_log_id=1, start_ms=0.0,
            account_id="acc_test", account_label="Test", strategy_id="test-strat",
        )
    return update_mock, close_mock


def _status_of(update_mock):
    """The status _process_order recorded on the order row."""
    assert update_mock.await_args_list, "no order status was ever written"
    return update_mock.await_args_list[0].args[2]


# ── 1. the same-symbol guard ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_net_account_still_rejects_the_opposite_side():
    """Regression: without hedge mode an opposite-side entry would net against the
    other strategy's position, so it must keep being refused."""
    pool = _make_pool(conflict_rows=[{"strategy_id": "other", "side": "short"}])
    update_mock, _ = await _run(_payload("open_long", "buy"), pool, "net")
    assert _status_of(update_mock) == "opp_pos_conflict"


@pytest.mark.asyncio
async def test_hedge_account_allows_the_opposite_side():
    """The point of the whole feature: another strategy's short must not block a
    long on the same coin."""
    pool = _make_pool(conflict_rows=[{"strategy_id": "other", "side": "short"}])
    update_mock, _ = await _run(_payload("open_long", "buy"), pool, "hedge")
    assert _status_of(update_mock) not in ("opp_pos_conflict", "same_symbol_conflict")


@pytest.mark.asyncio
async def test_hedge_account_still_rejects_the_same_side():
    """Two strategies long the same coin merge into ONE exchange position in hedge
    mode exactly as in net mode — there is still no way to attribute the size."""
    pool = _make_pool(conflict_rows=[{"strategy_id": "other", "side": "long"}])
    update_mock, _ = await _run(_payload("open_long", "buy"), pool, "hedge")
    assert _status_of(update_mock) == "same_symbol_conflict"


@pytest.mark.asyncio
async def test_hedge_short_entry_is_blocked_by_an_existing_short():
    pool = _make_pool(conflict_rows=[{"strategy_id": "other", "side": "short"}])
    update_mock, _ = await _run(_payload("open_short", "sell"), pool, "hedge")
    assert _status_of(update_mock) == "same_symbol_conflict"


@pytest.mark.asyncio
async def test_hedge_short_entry_is_allowed_against_an_existing_long():
    pool = _make_pool(conflict_rows=[{"strategy_id": "other", "side": "long"}])
    update_mock, _ = await _run(_payload("open_short", "sell"), pool, "hedge")
    assert _status_of(update_mock) not in ("opp_pos_conflict", "same_symbol_conflict")


@pytest.mark.asyncio
async def test_hedge_with_both_sides_open_still_blocks_the_matching_one():
    """A long and a short already running: a third strategy going long collides
    with the long leg and must be refused."""
    pool = _make_pool(conflict_rows=[
        {"strategy_id": "a", "side": "long"},
        {"strategy_id": "b", "side": "short"},
    ])
    update_mock, _ = await _run(_payload("open_long", "buy"), pool, "hedge")
    assert _status_of(update_mock) == "same_symbol_conflict"


# ── 2. the flip inference ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_net_account_still_books_a_flip_close():
    """Regression: on a net account an entry that reports realised PnL really did
    eat the opposite leg, and that leg must be closed in the DB."""
    pool = _make_pool(conflict_rows=[])
    pool._conn.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
    _, close_mock = await _run(
        _payload("open_long", "buy"), pool, "net",
        exec_result={"success": True, "status": "filled",
                     "actual_fill_price": "60000", "pnl": "12.5"},
    )
    flips = [c for c in close_mock.await_args_list
             if c.kwargs.get("reason") == "flip_close"]
    assert len(flips) == 1
    assert flips[0].kwargs["side"] == "short"


@pytest.mark.asyncio
async def test_hedge_account_never_books_a_flip_close():
    """On a hedge account the opposite leg is ALIVE. Closing its DB row here would
    desynchronise the books from the exchange while the position keeps running."""
    pool = _make_pool(conflict_rows=[])
    pool._conn.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
    _, close_mock = await _run(
        _payload("open_long", "buy"), pool, "hedge",
        exec_result={"success": True, "status": "filled",
                     "actual_fill_price": "60000", "pnl": "12.5"},
    )
    flips = [c for c in close_mock.await_args_list
             if c.kwargs.get("reason") == "flip_close"]
    assert flips == [], "hedge mode must not infer a flip from realised PnL"


# ── 3. flattening a symbol ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_flat_closes_every_open_leg_on_a_hedge_account():
    """'Flat' must mean flat. Closing one leg and reporting success would leave the
    other running with the operator believing it was shut."""
    pool = _make_pool(open_legs=[
        {"symbol": "BTC-USDT", "side": "long"},
        {"symbol": "BTC-USDT", "side": "short"},
    ])
    _, close_mock = await _run(
        _payload("open_long", "buy", target_position="flat"), pool, "hedge"
    )
    sides = sorted(c.kwargs["side"] for c in close_mock.await_args_list)
    assert sides == ["long", "short"]


@pytest.mark.asyncio
async def test_flat_reports_the_failing_leg_not_the_first_one():
    """A partly-failed flatten must not be recorded as a clean fill."""
    pool = _make_pool(open_legs=[
        {"symbol": "BTC-USDT", "side": "long"},
        {"symbol": "BTC-USDT", "side": "short"},
    ])
    results = [
        {"success": True,  "status": "filled"},
        {"success": False, "status": "route_failed", "error_msg": "exchange down"},
    ]
    close_mock = AsyncMock(side_effect=results)
    update_mock = AsyncMock()

    from app.webhook_handler import _process_order
    with patch("app.webhook_handler._account_position_mode", AsyncMock(return_value="hedge")), \
         patch("app.webhook_handler.close_strategy_position", close_mock), \
         patch("app.webhook_handler._update_order_status", update_mock), \
         patch("app.webhook_handler._finalize_signal_log", AsyncMock()), \
         patch("app.webhook_handler.publish", AsyncMock()):
        await _process_order(
            pool, uuid.uuid4(),
            _payload("open_long", "buy", target_position="flat"),
            STRATEGY, _resolved(),
            price=None, tp_price=None, sl_price=None,
            effective_leverage=10, effective_margin_mode="isolated",
            signal_log_id=1, start_ms=0.0,
            account_id="acc_test", account_label="Test", strategy_id="test-strat",
        )

    assert close_mock.await_count == 2
    assert _status_of(update_mock) == "route_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("signal,expected", [("close_long", "long"), ("close_short", "short")])
async def test_flat_on_a_close_signal_touches_only_that_leg(signal, expected):
    """target_position=flat means two different things depending on the signal that
    carries it. On close_long it says "the size field is not what decides the
    quantity — close this leg whole", and it must not reach across to the short."""
    pool = _make_pool(open_legs=[
        {"symbol": "BTC-USDT", "side": "long"},
        {"symbol": "BTC-USDT", "side": "short"},
    ])
    side = "sell" if signal == "close_long" else "buy"
    _, close_mock = await _run(
        _payload(signal, side, target_position="flat"), pool, "hedge"
    )
    assert close_mock.await_count == 1
    assert close_mock.await_args.kwargs["side"] == expected


@pytest.mark.asyncio
async def test_flat_on_a_close_signal_with_only_the_other_leg_open_closes_nothing():
    """A close_long while only a short is open must leave the short alone — under
    the old code it would have closed whatever single position it found."""
    pool = _make_pool(open_legs=[{"symbol": "BTC-USDT", "side": "short"}])
    update_mock, close_mock = await _run(
        _payload("close_long", "sell", target_position="flat"), pool, "hedge"
    )
    assert close_mock.await_count == 0
    assert _status_of(update_mock) == "no_position"


@pytest.mark.asyncio
async def test_flat_with_a_single_leg_is_unchanged():
    """Regression for net accounts, which only ever have one leg to close."""
    pool = _make_pool(open_legs=[{"symbol": "BTC-USDT", "side": "long"}])
    _, close_mock = await _run(
        _payload("open_long", "buy", target_position="flat"), pool, "net"
    )
    assert close_mock.await_count == 1
    assert close_mock.await_args.kwargs["side"] == "long"


# ── the mode lookup itself ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_position_mode_reads_as_net():
    """Failing closed matters: reading an unknown mode as 'hedge' would wave through
    an opposite-side entry that the exchange would then net away silently."""
    from app.webhook_handler import _account_position_mode
    pool = _make_pool()
    pool._conn.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("app.webhook_handler.cache_get", AsyncMock(return_value=None)):
        assert await _account_position_mode(pool, "acc_test") == "net"


@pytest.mark.asyncio
async def test_absent_account_reads_as_net():
    from app.webhook_handler import _account_position_mode
    pool = _make_pool()
    pool._conn.fetchval = AsyncMock(return_value=None)
    with patch("app.webhook_handler.cache_get", AsyncMock(return_value=None)), \
         patch("app.webhook_handler.cache_set", AsyncMock()):
        assert await _account_position_mode(pool, "nope") == "net"


@pytest.mark.asyncio
async def test_hedge_is_read_and_cached():
    from app.webhook_handler import _account_position_mode
    pool = _make_pool()
    pool._conn.fetchval = AsyncMock(return_value="hedge")
    cache_set = AsyncMock()
    with patch("app.webhook_handler.cache_get", AsyncMock(return_value=None)), \
         patch("app.webhook_handler.cache_set", cache_set):
        assert await _account_position_mode(pool, "acc_test") == "hedge"
    assert cache_set.await_args.args[1] == {"position_mode": "hedge"}


# ── adjust-stops must know which leg it is moving ────────────────────────────

async def _run_adjust(body, open_rows, modify=None):
    """Call the adjust-stops route directly with everything external stubbed."""
    from app.webhook_handler import adjust_stops_for_strategy

    conn = AsyncMock()
    conn.fetch    = AsyncMock(return_value=open_rows)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute  = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__  = AsyncMock(return_value=False)

    modify_mock = AsyncMock(return_value=modify or {
        "success": True, "sl_ok": True, "tp_ok": True, "preserved": [],
    })

    class _Req:
        async def body(self):
            return json.dumps(body).encode()

    with patch("app.webhook_handler.get_pool", lambda: pool), \
         patch("app.webhook_handler._get_strategy",
               AsyncMock(return_value={"id": "s1", "webhook_secret": "sec",
                                       "account_id": "acc"})), \
         patch("app.webhook_handler._verify_token", AsyncMock(return_value=True)), \
         patch("app.executor_client.call_executor_modify_stops", modify_mock):
        result = await adjust_stops_for_strategy("s1", _Req(), x_webhook_token="sec")
    return result, modify_mock


BOTH_LEGS_OPEN = [
    # Newest first, as the route's ORDER BY opened_at DESC returns them.
    {"id": "p-short", "symbol": "BTC-USDT", "side": "short", "opening_order_id": None},
    {"id": "p-long",  "symbol": "BTC-USDT", "side": "long",  "opening_order_id": None},
]


@pytest.mark.asyncio
async def test_adjust_stops_moves_the_leg_it_was_given():
    """Without side, this route takes the most recent position. With a long and a
    short both open that is a coin flip, and the loser is a live stop."""
    _, modify = await _run_adjust({"sl_price": 61000, "side": "long"}, BOTH_LEGS_OPEN)
    assert modify.await_args.kwargs["side"] == "long"


@pytest.mark.asyncio
async def test_adjust_stops_without_a_side_still_takes_the_newest():
    """Regression: every existing caller omits side and holds one position."""
    _, modify = await _run_adjust({"sl_price": 61000}, BOTH_LEGS_OPEN)
    assert modify.await_args.kwargs["side"] == "short"


@pytest.mark.asyncio
async def test_adjust_stops_refuses_a_side_that_is_not_open():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        await _run_adjust({"sl_price": 61000, "side": "long"},
                          [BOTH_LEGS_OPEN[0]])
    assert e.value.status_code == 404
    assert "long" in e.value.detail


@pytest.mark.asyncio
async def test_adjust_stops_rejects_a_nonsense_side():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        await _run_adjust({"sl_price": 61000, "side": "sideways"}, BOTH_LEGS_OPEN)
    assert e.value.status_code == 400
