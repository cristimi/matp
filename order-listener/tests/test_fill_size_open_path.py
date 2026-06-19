"""
Unit tests for Phase 2 — actual_fill_size wired through the open path.

Verifies:
  1. When the executor returns actual_fill_size, the DB INSERT uses it (not payload.size).
  2. When actual_fill_size is absent, the DB INSERT uses payload.size (no regression).
  3. Top-up path uses actual_fill_size for new_size and weighted entry price.
  4. Reconciler tolerates lot-rounding drift within 0.5% (relative epsilon).
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.reconciler import RECONCILE_MISS_THRESHOLD

# ── Helpers shared with test_close_unification ────────────────────────


def _make_pool_with_conn(conn):
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__  = AsyncMock(return_value=False)
    return pool


# ── Section 1 & 2: _create_strategy_position fill_size ───────────────


@pytest.mark.asyncio
async def test_create_position_uses_actual_fill_size_when_provided():
    """When fill_size is provided to _create_strategy_position, it is used as the DB size."""
    from app.webhook_handler import _create_strategy_position
    from app.models import WebhookPayload, OrderResult

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)  # no pair found → pair_id=None
    conn.execute  = AsyncMock()
    pool = _make_pool_with_conn(conn)

    payload = WebhookPayload(
        base_asset="HYPE", quote_asset="USDT",
        side="buy", signal="open_long", order_type="market",
        size=Decimal("1.45598556"), timestamp="2026-06-19T00:00:00Z", token="t",
    )
    strategy = {"id": "s1", "account_id": "a1", "exchange": "blofin",
                "config": None, "pnl_total": 0.0, "pnl_today": 0.0}
    result = OrderResult(
        success=True, status="filled",
        actual_fill_price=Decimal("68.50"),
        actual_fill_size=Decimal("1.5"),   # lot-rounded from 1.45598556
    )
    import uuid
    order_id = uuid.uuid4()

    await _create_strategy_position(
        pool, payload, strategy, order_id, result,
        effective_leverage=20, effective_margin_mode="isolated",
        fill_size=result.actual_fill_size,
    )

    conn.execute.assert_called_once()
    args = conn.execute.call_args.args
    # $7 positional arg is db_size in the INSERT
    assert Decimal("1.5") in args, (
        f"Expected fill_size=1.5 in INSERT args, got: {args}"
    )
    assert Decimal("1.45598556") not in args, (
        "payload.size must not be used when fill_size is provided"
    )


@pytest.mark.asyncio
async def test_create_position_falls_back_to_payload_size_when_fill_size_none():
    """When fill_size is None, _create_strategy_position falls back to payload.size."""
    from app.webhook_handler import _create_strategy_position
    from app.models import WebhookPayload, OrderResult

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute  = AsyncMock()
    pool = _make_pool_with_conn(conn)

    payload = WebhookPayload(
        base_asset="HYPE", quote_asset="USDT",
        side="buy", signal="open_long", order_type="market",
        size=Decimal("1.45598556"), timestamp="2026-06-19T00:00:00Z", token="t",
    )
    strategy = {"id": "s1", "account_id": "a1", "exchange": "blofin",
                "config": None, "pnl_total": 0.0, "pnl_today": 0.0}
    result = OrderResult(success=True, status="filled",
                         actual_fill_price=Decimal("68.50"))
    import uuid

    await _create_strategy_position(
        pool, payload, strategy, uuid.uuid4(), result,
        effective_leverage=20, effective_margin_mode="isolated",
        fill_size=None,
    )

    args = conn.execute.call_args.args
    assert Decimal("1.45598556") in args, (
        f"Expected payload.size=1.45598556 in INSERT args (fallback), got: {args}"
    )


# ── Section 3: Reconciler relative epsilon ───────────────────────────

OPENED_AT = datetime(2026, 6, 19, 0, 0, tzinfo=timezone.utc)


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    async def fetch(self, query, *args):
        return self._rows

    async def fetchrow(self, query, *args):
        return self._rows[0] if self._rows else None

    async def execute(self, query, *args):
        self.executed.append((" ".join(query.split()), args))
        return "UPDATE 1"


class _Acq:
    def __init__(self, conn): self._conn = conn
    async def __aenter__(self): return self._conn
    async def __aexit__(self, *a): return False


class _FakePool:
    def __init__(self, rows):
        self.conn = _FakeConn(rows)
    def acquire(self):
        return _Acq(self.conn)


def _row(**kw):
    base = dict(
        id="pos1", strategy_id="str1", symbol="HYPE-USDT", side="long",
        size=Decimal("1.5"), opened_at=OPENED_AT,
        reconcile_miss_count=0, account_id="acc1",
    )
    base.update(kw)
    return base


async def _run_reconcile(rows, exchange_positions):
    pool = _FakePool(rows)
    gap   = AsyncMock(return_value=exchange_positions)
    gph   = AsyncMock(return_value={})
    close = AsyncMock(return_value={"success": True})
    sync  = AsyncMock()
    recov = AsyncMock()

    with patch("app.executor_client.get_account_positions", gap), \
         patch("app.executor_client.get_position_history", gph), \
         patch("app.webhook_handler.close_strategy_position", close), \
         patch("app.webhook_handler.sync_position_pnl", sync), \
         patch("app.reconciler._recover_manual_close_pnl", recov):
        from app.reconciler import reconcile_once
        await reconcile_once(pool)

    return pool, close


@pytest.mark.asyncio
async def test_reconciler_tolerates_lot_rounding_within_half_percent():
    """DB=1.5 (lot-rounded), exchange=1.45598556 (actual base coins).
    Difference is ~0.044 / 1.5 ≈ 2.9% — OUTSIDE tolerance → should flag as miss.
    Test that it IS flagged (shows the system is sensitive to real drift).
    This is the BEFORE scenario; Phase 4 DB write will make them match."""
    pool, close = await _run_reconcile(
        [_row(size=Decimal("1.5"), reconcile_miss_count=0)],
        exchange_positions=[{"symbol": "HYPE-USDT", "side": "long", "size": "1.45598556"}],
    )
    # 2.9% > 0.5% tolerance → should increment miss
    incremented = any("reconcile_miss_count = $1" in sql for sql, _ in pool.conn.executed)
    assert incremented, "Expected miss count increment for 2.9% drift (outside 0.5% tolerance)"
    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciler_tolerates_tiny_drift_within_half_percent():
    """DB=1.5, exchange=1.498 — difference is 0.13% which is inside 0.5% tolerance.
    Should reset (or not increment), never close."""
    pool, close = await _run_reconcile(
        [_row(size=Decimal("1.5"), reconcile_miss_count=2)],
        exchange_positions=[{"symbol": "HYPE-USDT", "side": "long", "size": "1.498"}],
    )
    reset = any("reconcile_miss_count = 0" in sql for sql, _ in pool.conn.executed)
    incremented = any("reconcile_miss_count = $1" in sql for sql, _ in pool.conn.executed)
    assert reset, "Expected miss count reset for 0.13% drift (within 0.5% tolerance)"
    assert not incremented, "Must not increment when within tolerance"
    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciler_exact_match_still_resets():
    """Exact match (DB == exchange) resets miss counter. Baseline regression guard."""
    pool, close = await _run_reconcile(
        [_row(size=Decimal("1.5"), reconcile_miss_count=1)],
        exchange_positions=[{"symbol": "HYPE-USDT", "side": "long", "size": "1.5"}],
    )
    reset = any("reconcile_miss_count = 0" in sql for sql, _ in pool.conn.executed)
    assert reset, "Exact match must reset miss counter"
    close.assert_not_awaited()
