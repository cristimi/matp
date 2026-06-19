"""
Unit tests for Phase 1 close-path unification.

Verifies that close_long / close_short signals in _process_order:
  1. Route through close_strategy_position(skip_exchange=False).
  2. Never call call_executor / submit_order.
  3. Correctly handle oversized close (size > open size) without calling submit.

No live services needed — all external deps are mocked.
"""
import uuid
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

# ── Minimal stubs ──────────────────────────────────────────────────────


def _make_pool():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch    = AsyncMock(return_value=[])
    conn.execute  = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__  = AsyncMock(return_value=False)
    return pool


STRATEGY = {
    "id":          "test-strat",
    "account_id":  "acc_test",
    "config":      None,
    "pnl_total":   0.0,
    "pnl_today":   0.0,
}


def _make_payload(signal="close_short", side="buy", size="0.5"):
    from app.models import WebhookPayload
    return WebhookPayload(
        base_asset="HYPE",
        quote_asset="USDT",
        side=side,
        signal=signal,
        order_type="market",
        size=Decimal(size),
        timestamp="2026-06-19T00:00:00Z",
        token="secret",
    )


def _resolved(symbol="HYPE-USDT"):
    r = MagicMock()
    r.execution_symbol = symbol
    r.coupling_used    = None
    r.price_stripped   = False
    return r


CLOSE_SUCCESS = {
    "success":           True,
    "status":            "filled",
    "actual_fill_price": Decimal("68.50"),
    "realized_pnl":      Decimal("1.23"),
    "exchange_order_id": "eid-999",
    "is_full_close":     True,
}

CLOSE_NO_POSITION = {
    "success":   False,
    "status":    "no_position_to_close",
    "error_msg": "No open short position for HYPE-USDT",
}


async def _run_process_order(
    signal="close_short",
    side="buy",
    size="0.5",
    close_result=None,
):
    """Call _process_order directly with patched dependencies."""
    from app.webhook_handler import _process_order

    if close_result is None:
        close_result = CLOSE_SUCCESS

    pool        = _make_pool()
    order_id    = uuid.uuid4()
    payload     = _make_payload(signal=signal, side=side, size=size)
    resolved    = _resolved()

    close_mock    = AsyncMock(return_value=close_result)
    executor_mock = AsyncMock(return_value={
        "success": False, "status": "route_failed", "error_msg": "should not be called"
    })
    publish_mock  = AsyncMock()
    finalize_mock = AsyncMock()
    update_mock   = AsyncMock()

    with patch("app.webhook_handler.close_strategy_position", close_mock), \
         patch("app.executor_client.call_executor", executor_mock), \
         patch("app.webhook_handler.publish", publish_mock), \
         patch("app.webhook_handler._finalize_signal_log", finalize_mock), \
         patch("app.webhook_handler._update_order_status", update_mock):
        await _process_order(
            pool, order_id, payload, STRATEGY, resolved,
            price=None, tp_price=None, sl_price=None,
            effective_leverage=20, effective_margin_mode="isolated",
            signal_log_id=1, start_ms=0.0,
            account_id="acc_test", account_label="Test", strategy_id="test-strat",
        )

    return close_mock, executor_mock, update_mock


# ── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_short_routes_through_close_strategy_position():
    """close_short must call close_strategy_position, not call_executor."""
    close_mock, executor_mock, _ = await _run_process_order(
        signal="close_short", side="buy", size="0.5"
    )

    close_mock.assert_awaited_once()
    executor_mock.assert_not_awaited()

    call_kwargs = close_mock.await_args.kwargs
    assert call_kwargs.get("skip_exchange") is False
    assert call_kwargs.get("side") == "short"
    assert call_kwargs.get("symbol") == "HYPE-USDT"
    assert call_kwargs.get("close_size") == Decimal("0.5")


@pytest.mark.asyncio
async def test_close_long_routes_through_close_strategy_position():
    """close_long must call close_strategy_position with side='long'."""
    close_mock, executor_mock, _ = await _run_process_order(
        signal="close_long", side="sell", size="1.0"
    )

    close_mock.assert_awaited_once()
    executor_mock.assert_not_awaited()

    call_kwargs = close_mock.await_args.kwargs
    assert call_kwargs.get("skip_exchange") is False
    assert call_kwargs.get("side") == "long"


@pytest.mark.asyncio
async def test_oversized_close_still_routes_to_close_not_executor():
    """close with size >> open size must still use close_strategy_position.
    The clamping happens inside close_strategy_position; call_executor is never called."""
    close_mock, executor_mock, update_mock = await _run_process_order(
        signal="close_short", side="buy", size="999",  # massively oversized
        close_result=CLOSE_SUCCESS,
    )

    close_mock.assert_awaited_once()
    executor_mock.assert_not_awaited()

    # The full-close result must propagate to order status
    call_kwargs = close_mock.await_args.kwargs
    assert call_kwargs.get("close_size") == Decimal("999")
    # Despite oversized, the result is success (close_strategy_position clamps internally)
    assert update_mock.await_args is not None


@pytest.mark.asyncio
async def test_close_with_no_open_position_updates_order_status():
    """If no DB position exists, close_strategy_position returns failure;
    _update_order_status is called with the failure status."""
    close_mock, executor_mock, update_mock = await _run_process_order(
        signal="close_short", side="buy", size="0.5",
        close_result=CLOSE_NO_POSITION,
    )

    close_mock.assert_awaited_once()
    executor_mock.assert_not_awaited()

    # The status passed to _update_order_status must be "no_position_to_close"
    status_arg = update_mock.await_args.args[2]   # positional: pool, order_id, status, ...
    assert status_arg == "no_position_to_close"
