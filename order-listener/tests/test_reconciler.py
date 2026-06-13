"""
Unit tests for the reconciler — covering the four incident fixes:

  Part 1  UNKNOWN exchange read -> skip account, never increment, never close
  Part 2  confirmed-present (equal OR larger) -> reset miss counter, never close
  Inc.    confirmed-absent / confirmed-smaller -> increment; at threshold -> act
  Part 4  full external close looks up history scoped to the position's opened_at

No live services or DB: get_account_positions / get_position_history /
close_strategy_position / sync_position_pnl are patched on their source modules,
and the asyncpg pool is faked.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.reconciler import RECONCILE_MISS_THRESHOLD

OPENED_AT = datetime(2026, 6, 12, 16, 48, tzinfo=timezone.utc)

HISTORY_OK = {
    "close_reason":  "Closed on exchange",
    "closing_price": Decimal("60000"),
    "pnl_realized":  Decimal("0.24"),
    "closed_at":     datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc),  # after OPENED_AT
}


# ── Fakes ────────────────────────────────────────────────────────────
class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []   # list of (normalized_sql, args)

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
        id="pos1", strategy_id="str1", symbol="BTC-USDT", side="short",
        size=Decimal("0.01"), opened_at=OPENED_AT,
        reconcile_miss_count=0, account_id="acc1",
    )
    base.update(kw)
    return base


def _exch(symbol="BTC-USDT", side="short", size="0.01"):
    return {"symbol": symbol, "side": side, "size": size}


def _did_reset(pool):
    return any("reconcile_miss_count = 0" in sql for sql, _ in pool.conn.executed)

def _did_increment(pool):
    return any("reconcile_miss_count = $1" in sql for sql, _ in pool.conn.executed)


async def _run(rows, gap_ret, history=None):
    """Run one reconcile pass with all external deps patched.
    gap_ret: a list/[]/None applied to every account, OR a dict {account_id: ret}.
    Returns (pool, gap_mock, gph_mock, close_mock)."""
    pool = _FakePool(rows)

    if isinstance(gap_ret, dict):
        async def _se(acct_id): return gap_ret.get(acct_id)
        gap = AsyncMock(side_effect=_se)
    else:
        gap = AsyncMock(return_value=gap_ret)

    gph   = AsyncMock(return_value=(history if history is not None else {}))
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

    return pool, gap, gph, close


# ── Part 1: UNKNOWN read is never treated as a close ────────────────────
@pytest.mark.asyncio
async def test_unknown_read_skips_account_no_increment_no_close():
    pool, gap, gph, close = await _run([_row(reconcile_miss_count=0)], gap_ret=None)
    assert pool.conn.executed == []          # no miss write at all
    assert not _did_increment(pool)
    close.assert_not_awaited()
    gph.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_read_does_not_advance_seeded_counter():
    # Even with a counter near threshold, UNKNOWN must not push it over.
    pool, _, _, close = await _run(
        [_row(reconcile_miss_count=RECONCILE_MISS_THRESHOLD - 1)], gap_ret=None
    )
    assert pool.conn.executed == []
    close.assert_not_awaited()


# ── Part 2: confirmed-present resets the counter ────────────────────────
@pytest.mark.asyncio
async def test_present_equal_resets_counter_no_close():
    pool, _, _, close = await _run(
        [_row(reconcile_miss_count=2, size=Decimal("0.01"))],
        gap_ret=[_exch(size="0.01")],          # exact match
    )
    assert _did_reset(pool)
    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_present_larger_resets_counter_no_close():
    # The Part 2 fix: 'will not grow' branch must reset, not ratchet.
    pool, _, _, close = await _run(
        [_row(reconcile_miss_count=2, size=Decimal("0.01"))],
        gap_ret=[_exch(size="0.05")],          # exchange larger
    )
    assert _did_reset(pool)
    close.assert_not_awaited()


# ── Increment paths ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_absent_below_threshold_increments_no_close():
    pool, _, _, close = await _run(
        [_row(reconcile_miss_count=0)],
        gap_ret=[],                             # confirmed empty
    )
    assert _did_increment(pool)
    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_absent_at_threshold_triggers_full_close_scoped_by_opened_at():
    pool, _, gph, close = await _run(
        [_row(reconcile_miss_count=RECONCILE_MISS_THRESHOLD - 1)],
        gap_ret=[],                             # confirmed empty -> full close
        history=HISTORY_OK,
    )
    close.assert_awaited()
    # Part 4: history was looked up scoped to this position's open time
    gph.assert_awaited()
    assert gph.await_args.args[2] == OPENED_AT


@pytest.mark.asyncio
async def test_smaller_at_threshold_triggers_partial_reduction():
    pool, _, _, close = await _run(
        [_row(reconcile_miss_count=RECONCILE_MISS_THRESHOLD - 1, size=Decimal("0.01"))],
        gap_ret=[_exch(size="0.004")],          # exchange smaller -> partial
    )
    close.assert_awaited()
    assert close.await_args.kwargs.get("skip_exchange") is True


# ── Per-account isolation: one UNKNOWN account must not affect another ──
@pytest.mark.asyncio
async def test_unknown_account_isolated_from_healthy_account():
    rows = [
        _row(id="posA", strategy_id="strA", account_id="acctA", reconcile_miss_count=2),
        _row(id="posB", strategy_id="strB", account_id="acctB", reconcile_miss_count=2,
             size=Decimal("0.01")),
    ]
    # acctA UNKNOWN (None); acctB present-larger -> should reset
    pool, _, _, close = await _run(
        rows, gap_ret={"acctA": None, "acctB": [_exch(size="0.05")]},
    )
    # acctB reset happened; acctA produced no write
    resets = [args for sql, args in pool.conn.executed if "reconcile_miss_count = 0" in sql]
    assert any("posB" in str(a) for a in resets)
    assert not any("posA" in str(a) for a in resets)
    close.assert_not_awaited()
