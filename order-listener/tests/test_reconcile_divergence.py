"""
Unit tests for Phase 3 — reconcile_divergent flag columns (Bug 2).

Verifies:
  1. "Exchange larger" branch sets reconcile_divergent=TRUE and reconcile_exchange_size.
  2. "Exchange larger" branch uses COALESCE to preserve first-seen reconcile_divergence_at.
  3. "Sizes match" branch clears the divergence flag when it was previously set.
  4. "Sizes match" with no prior divergence and miss_count=0 → no DB write at all.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

OPENED_AT = datetime(2026, 6, 19, 0, 0, tzinfo=timezone.utc)


# ── Shared fakes (same pattern as test_reconciler.py) ────────────────

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
        id="pos1", strategy_id="str1", symbol="HYPE-USDT", side="long",
        size=Decimal("1.45"), opened_at=OPENED_AT,
        reconcile_miss_count=0, reconcile_divergent=False,
        account_id="acc1",
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


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exchange_larger_sets_divergent_flag():
    """When exchange_size > db_size, the reconciler must SET reconcile_divergent=TRUE
    and record reconcile_exchange_size in the UPDATE."""
    pool, close = await _run_reconcile(
        [_row(size=Decimal("1.45"), reconcile_miss_count=0, reconcile_divergent=False)],
        exchange_positions=[{"symbol": "HYPE-USDT", "side": "long", "size": "5.8"}],
    )

    close.assert_not_awaited()
    assert pool.conn.executed, "Expected at least one DB write"

    sql, args = pool.conn.executed[-1]
    assert "reconcile_divergent = TRUE" in sql, (
        f"Expected reconcile_divergent=TRUE in SQL, got: {sql}"
    )
    assert "reconcile_exchange_size = $1" in sql, (
        f"Expected reconcile_exchange_size=$1 in SQL, got: {sql}"
    )
    assert Decimal("5.8") in args, (
        f"Expected exchange size 5.8 in args, got: {args}"
    )


@pytest.mark.asyncio
async def test_exchange_larger_preserves_first_seen_timestamp():
    """COALESCE(reconcile_divergence_at, NOW()) must appear in the SQL so the
    first-detection timestamp is never overwritten by subsequent passes."""
    pool, _ = await _run_reconcile(
        [_row(size=Decimal("1.45"), reconcile_miss_count=0, reconcile_divergent=False)],
        exchange_positions=[{"symbol": "HYPE-USDT", "side": "long", "size": "5.8"}],
    )

    sql, _ = pool.conn.executed[-1]
    assert "COALESCE(reconcile_divergence_at, NOW())" in sql, (
        f"Expected COALESCE in reconcile_divergence_at assignment, got: {sql}"
    )


@pytest.mark.asyncio
async def test_exchange_larger_also_resets_miss_count():
    """The divergence branch must reset reconcile_miss_count=0 (position IS confirmed present)."""
    pool, _ = await _run_reconcile(
        [_row(size=Decimal("1.45"), reconcile_miss_count=2, reconcile_divergent=False)],
        exchange_positions=[{"symbol": "HYPE-USDT", "side": "long", "size": "5.8"}],
    )

    sql, _ = pool.conn.executed[-1]
    assert "reconcile_miss_count = 0" in sql, (
        f"Expected reconcile_miss_count=0 in divergence UPDATE, got: {sql}"
    )


@pytest.mark.asyncio
async def test_sizes_match_clears_divergence_flag():
    """When sizes match after a previous divergence, the reconciler must clear the flag
    (reconcile_divergent=FALSE, exchange_size=NULL, divergence_at=NULL)."""
    pool, close = await _run_reconcile(
        [_row(size=Decimal("1.5"), reconcile_miss_count=0, reconcile_divergent=True)],
        exchange_positions=[{"symbol": "HYPE-USDT", "side": "long", "size": "1.5"}],
    )

    close.assert_not_awaited()
    assert pool.conn.executed, "Expected a DB write to clear divergence flag"

    sql, _ = pool.conn.executed[-1]
    assert "reconcile_divergent = FALSE" in sql, (
        f"Expected reconcile_divergent=FALSE in match-reset SQL, got: {sql}"
    )
    assert "reconcile_exchange_size = NULL" in sql, (
        f"Expected reconcile_exchange_size=NULL in match-reset SQL, got: {sql}"
    )
    assert "reconcile_divergence_at = NULL" in sql, (
        f"Expected reconcile_divergence_at=NULL in match-reset SQL, got: {sql}"
    )


@pytest.mark.asyncio
async def test_sizes_match_no_write_when_already_clean():
    """When sizes match AND miss_count=0 AND reconcile_divergent=False, no DB write is needed."""
    pool, _ = await _run_reconcile(
        [_row(size=Decimal("1.5"), reconcile_miss_count=0, reconcile_divergent=False)],
        exchange_positions=[{"symbol": "HYPE-USDT", "side": "long", "size": "1.5"}],
    )

    # Only the initial SELECT should have run; no UPDATE
    updates = [sql for sql, _ in pool.conn.executed if sql.startswith("UPDATE")]
    assert not updates, f"Expected no UPDATE when already clean; got: {updates}"
