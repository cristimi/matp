"""
Reconciler: detects external position changes (closes, liquidations, partial reductions)
and syncs the DB. Uses a miss-streak counter to avoid acting on transient API glitches.

Safety rule: never close or shrink a row on a single exchange read.
A discrepancy must hold for RECONCILE_MISS_THRESHOLD consecutive passes before acting.
"""

import logging
import os
import uuid
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

RECONCILE_MISS_THRESHOLD: int = int(os.environ.get("RECONCILE_MISS_THRESHOLD", "3"))
_SIZE_EPSILON_ABS = Decimal("0.000001")          # absolute floor
_SIZE_EPSILON_REL = Decimal("0.005")             # 0.5% of db_size — absorbs lot-rounding drift


async def reconcile_once(pool) -> None:
    """
    Single reconciliation pass:
    1. Load all open positions grouped by account.
    2. Fetch live exchange positions per account.
    3. Compare and update miss counters; act when threshold reached.
    4. Run sync_position_pnl to propagate realized PnL from close orders.
    """
    from app.executor_client import get_account_positions, get_position_history
    from app.webhook_handler import close_strategy_position, sync_position_pnl

    # Load open positions with account_id from strategy
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sp.id, sp.strategy_id, sp.symbol, sp.side,
                   sp.size, sp.opened_at, sp.reconcile_miss_count,
                   sp.reconcile_divergent,
                   s.account_id
            FROM strategy_positions sp
            JOIN strategies s ON sp.strategy_id = s.id
            WHERE sp.status = 'open'
              AND COALESCE(s.is_deleted, false) = false
            """
        )

    if not rows:
        await sync_position_pnl(pool)
        await _recover_manual_close_pnl(pool)
        return

    # Group open positions by account_id
    by_account: dict[str, list] = {}
    for row in rows:
        acct = row["account_id"]
        if acct not in by_account:
            by_account[acct] = []
        by_account[acct].append(row)

    # Fetch live exchange positions once per account.
    # get_account_positions returns None when the exchange/executor is unreachable (UNKNOWN).
    # UNKNOWN must NOT be treated as "no positions": we skip the whole account this pass so a
    # transient outage can never close or shrink a position. The rows stay 'open' in the DB
    # (the dashboard renders them as stale).
    exchange_map: dict[str, dict[tuple, Decimal]] = {}
    unknown_accounts: set[str] = set()
    for acct_id in by_account:
        positions = await get_account_positions(acct_id)
        if positions is None:
            unknown_accounts.add(acct_id)
            logger.warning(
                f"reconciler: positions UNKNOWN for account {acct_id} "
                f"(exchange/executor unreachable) — leaving its "
                f"{len(by_account[acct_id])} open position(s) untouched this pass"
            )
            continue
        pos_map: dict[tuple, Decimal] = {}
        for p in positions:
            try:
                sym  = p.get("symbol") or p.get("instId") or ""
                side = (p.get("side") or "").lower()
                size = Decimal(str(p.get("size") or "0"))
                if sym and side and size > 0:
                    pos_map[(sym, side)] = size
            except Exception as e:
                logger.warning(f"reconciler: bad position entry from exchange: {p}: {e}")
        exchange_map[acct_id] = pos_map
        logger.debug(f"reconciler: account {acct_id} has {len(pos_map)} live positions")

    # Process each open DB row
    for row in rows:
        acct_id   = row["account_id"]
        if acct_id in unknown_accounts:
            # Exchange state UNKNOWN this pass — do not increment misses, do not act.
            continue
        pos_id    = row["id"]
        symbol    = row["symbol"]
        side      = row["side"]
        db_size   = Decimal(str(row["size"]))
        miss_count = row["reconcile_miss_count"]
        opened_at  = row["opened_at"]

        ex_map  = exchange_map.get(acct_id, {})
        ex_size = ex_map.get((symbol, side))  # None if not found

        if ex_size is None:
            ex_size_dec = Decimal("0")
        else:
            ex_size_dec = ex_size

        size_diff = db_size - ex_size_dec
        _tol = max(_SIZE_EPSILON_ABS, db_size * _SIZE_EPSILON_REL)

        if ex_size is not None and abs(size_diff) <= _tol:
            # Sizes match within tolerance — reset miss counter and clear any divergence flag
            already_divergent = row.get("reconcile_divergent", False)
            if miss_count != 0 or already_divergent:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE strategy_positions
                        SET reconcile_miss_count    = 0,
                            reconcile_divergent     = FALSE,
                            reconcile_exchange_size = NULL,
                            reconcile_divergence_at = NULL,
                            updated_at              = NOW()
                        WHERE id = $1
                        """,
                        pos_id,
                    )
                logger.debug(f"reconciler: position {pos_id} ({symbol} {side}) match reset (divergent={already_divergent})")
            continue

        if ex_size is not None and ex_size_dec > db_size + _tol:
            # Exchange size LARGER than DB — flag divergence; never grow from reconciliation.
            # Reset miss counter: the position IS confirmed present (positive evidence it is
            # not disappearing). Flag the size discrepancy so the dashboard can surface it.
            logger.warning(
                f"reconciler: position {pos_id} ({symbol} {side}) "
                f"exchange_size={ex_size_dec} > db_size={db_size} — flagging divergence"
            )
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE strategy_positions
                    SET reconcile_miss_count    = 0,
                        reconcile_divergent     = TRUE,
                        reconcile_exchange_size = $1,
                        reconcile_divergence_at = COALESCE(reconcile_divergence_at, NOW()),
                        updated_at              = NOW()
                    WHERE id = $2
                    """,
                    ex_size_dec,
                    pos_id,
                )
            logger.info(
                f"reconciler: position {pos_id} ({symbol} {side}) divergence flagged "
                f"(exchange={ex_size_dec} > db={db_size})"
            )
            continue

        # Discrepancy: absent or smaller on exchange → increment miss counter
        new_miss = miss_count + 1
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE strategy_positions SET reconcile_miss_count = $1,"
                " updated_at = NOW() WHERE id = $2 AND status = 'open'",
                new_miss,
                pos_id,
            )
        logger.info(
            f"reconciler: position {pos_id} ({symbol} {side}) miss {new_miss}/{RECONCILE_MISS_THRESHOLD}"
            f" db={db_size} exchange={ex_size_dec}"
        )

        if new_miss < RECONCILE_MISS_THRESHOLD:
            continue

        # Threshold reached — act
        strategy = {"id": row["strategy_id"], "account_id": acct_id}

        if ex_size is None or ex_size_dec <= _SIZE_EPSILON_ABS:
            # Full external close
            await _handle_full_external_close(
                pool, strategy, symbol, side, pos_id, opened_at,
                acct_id, get_position_history,
            )
        else:
            # Partial reduction
            reduce_by = size_diff  # db_size - ex_size_dec
            logger.info(
                f"reconciler: partial reduction position {pos_id} ({symbol} {side})"
                f" by {reduce_by} to match exchange {ex_size_dec}"
            )
            result = await close_strategy_position(
                pool, strategy,
                symbol=symbol,
                side=side,
                close_size=reduce_by,
                reason="Closed on exchange",
                skip_exchange=True,
            )
            if result.get("success"):
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE strategy_positions SET reconcile_miss_count = 0 WHERE id = $1",
                        pos_id,
                    )

    await sync_position_pnl(pool)
    await _recover_manual_close_pnl(pool)
    logger.debug("reconciler: pass complete")


async def _handle_full_external_close(
    pool,
    strategy: dict,
    symbol: str,
    side: str,
    pos_id: uuid.UUID,
    opened_at,
    acct_id: str,
    get_position_history,
) -> None:
    from app.webhook_handler import close_strategy_position

    history = await get_position_history(acct_id, symbol, opened_at)

    close_reason   = history.get("close_reason") or "Closed on exchange"
    closing_price  = history.get("closing_price")
    pnl_realized   = history.get("pnl_realized")
    closed_at      = history.get("closed_at")
    pnl_unconfirmed = False

    # Stale-history guard: reject if history is from before this position opened
    if closed_at and opened_at:
        from datetime import datetime, timezone
        try:
            closed_dt = (
                closed_at if hasattr(closed_at, 'tzinfo')
                else datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
            )
            if closed_dt.tzinfo is None:
                closed_dt = closed_dt.replace(tzinfo=timezone.utc)
            opened_dt = opened_at
            if opened_dt.tzinfo is None:
                opened_dt = opened_dt.replace(tzinfo=timezone.utc)
            if closed_dt <= opened_dt:
                logger.warning(
                    f"reconciler: stale history for {pos_id} ({symbol})"
                    f" closed_at={closed_dt} <= opened_at={opened_dt} — using defaults"
                )
                close_reason  = "Closed on exchange"
                closing_price = None
                pnl_realized  = None
                pnl_unconfirmed = True
        except Exception as e:
            logger.warning(f"reconciler: closed_at parse error for {pos_id}: {e}")
            pnl_unconfirmed = True

    if pnl_realized is None and not pnl_unconfirmed:
        pnl_unconfirmed = True

    if pnl_unconfirmed:
        logger.warning(
            f"reconciler: pnl_unconfirmed for position {pos_id} ({symbol} {side})"
            f" close_reason={close_reason}"
        )

    # Create a synthetic closing order so sync_position_pnl can attribute PnL
    synthetic_order_id: Optional[uuid.UUID] = None
    if not pnl_unconfirmed and pnl_realized is not None:
        close_side = "sell" if side == "long" else "buy"
        try:
            pnl_float = float(pnl_realized)
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO orders
                      (symbol, side, signal, order_type, size, platform,
                       strategy_id, account_id, status, actual_fill_price,
                       pnl, raw_webhook, signal_source)
                    VALUES
                      ($1, $2, $3, 'market', 0, 'exchange',
                       $4, $5, 'filled', $6,
                       $7, '{}'::jsonb, 'reconciler')
                    RETURNING id
                    """,
                    symbol, close_side,
                    "liquidation" if close_reason == "Liquidated" else "exchange_close",
                    strategy["id"], acct_id,
                    float(closing_price) if closing_price else None,
                    pnl_float,
                )
            synthetic_order_id = row["id"]
        except Exception as e:
            logger.error(f"reconciler: failed to create synthetic order for {pos_id}: {e}")

    result = await close_strategy_position(
        pool, strategy,
        symbol=symbol,
        side=side,
        close_size=None,
        closing_order_id=synthetic_order_id,
        reason=close_reason,
        skip_exchange=True,
        fill_price=closing_price,
        realized_pnl=pnl_realized if not pnl_unconfirmed else None,
    )

    if result.get("success"):
        logger.info(
            f"reconciler: closed position {pos_id} ({symbol} {side})"
            f" reason={close_reason} pnl={pnl_realized}"
            + (" [pnl_unconfirmed]" if pnl_unconfirmed else "")
        )
    else:
        logger.error(
            f"reconciler: failed to close position {pos_id} ({symbol} {side}): {result}"
        )


async def _recover_manual_close_pnl(pool) -> None:
    """
    Fallback PnL recovery for closed positions with pnl_realized=0/NULL and no
    attributable close orders (manual UI closes, executor-only closes, etc.).
    Calls /positions/history and applies the stale-history guard.
    """
    from app.executor_client import get_position_history
    from datetime import datetime, timezone

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sp.id, sp.strategy_id, sp.symbol, sp.side, sp.opened_at, s.account_id
            FROM strategy_positions sp
            JOIN strategies s ON sp.strategy_id = s.id
            WHERE sp.status = 'closed'
              AND (sp.pnl_realized IS NULL OR sp.pnl_realized = 0)
              AND sp.closed_at >= NOW() - INTERVAL '7 days'
              AND COALESCE(s.is_deleted, false) = false
              AND NOT EXISTS (
                SELECT 1 FROM orders o
                WHERE o.closes_position_id = sp.id AND o.pnl IS NOT NULL
              )
            ORDER BY sp.closed_at DESC
            """
        )

    if not rows:
        return

    logger.debug(f"reconciler: {len(rows)} closed positions need PnL history fallback")

    for row in rows:
        pos_id    = row["id"]
        symbol    = row["symbol"]
        side      = row["side"]
        acct_id   = row["account_id"]
        opened_at = row["opened_at"]

        try:
            history = await get_position_history(acct_id, symbol, opened_at)
        except Exception as e:
            logger.warning(f"reconciler: history fetch failed for {pos_id} ({symbol}): {e}")
            continue

        if not history or not history.get("pnl_realized"):
            logger.debug(
                f"reconciler: pnl_unconfirmed for {pos_id} ({symbol} {side}) — no history PnL"
            )
            continue

        pnl_realized   = history.get("pnl_realized")
        hist_closed_at = history.get("closed_at")

        # Stale-history guard: reject history predating this position's open
        if hist_closed_at and opened_at:
            try:
                closed_dt = (
                    hist_closed_at if hasattr(hist_closed_at, "tzinfo")
                    else datetime.fromisoformat(str(hist_closed_at).replace("Z", "+00:00"))
                )
                if closed_dt.tzinfo is None:
                    closed_dt = closed_dt.replace(tzinfo=timezone.utc)
                opened_dt = opened_at
                if opened_dt.tzinfo is None:
                    opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                if closed_dt <= opened_dt:
                    logger.warning(
                        f"reconciler: stale history for {pos_id} ({symbol})"
                        f" hist_closed_at={closed_dt} <= opened_at={opened_dt} — skipping"
                    )
                    continue
            except Exception as e:
                logger.warning(f"reconciler: stale guard parse error for {pos_id}: {e}")
                continue

        try:
            pnl_float = float(pnl_realized)
            async with pool.acquire() as conn:
                updated_row = await conn.fetchrow(
                    """
                    UPDATE strategy_positions
                    SET pnl_realized = $1, updated_at = NOW()
                    WHERE id = $2 AND status = 'closed'
                      AND pnl_realized IS NULL
                    RETURNING id, strategy_id, pnl_realized
                    """,
                    pnl_float,
                    pos_id,
                )
            if updated_row:
                logger.info(
                    f"reconciler: history fallback set pnl_realized={pnl_float}"
                    f" for {pos_id} ({symbol} {side})"
                )
                from app.webhook_handler import _book_realized_pnl
                await _book_realized_pnl(
                    pool, str(updated_row['strategy_id']), updated_row['pnl_realized']
                )
        except Exception as e:
            logger.error(f"reconciler: pnl recovery update failed for {pos_id}: {e}")
