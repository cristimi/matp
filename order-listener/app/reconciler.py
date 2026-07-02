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
    0. Reconcile pending (resting) limit orders — detect fills/cancels.
    1. Load all open positions grouped by account.
    2. Fetch live exchange positions per account.
    3. Compare and update miss counters; act when threshold reached.
    4. Run sync_position_pnl to propagate realized PnL from close orders.
    """
    from app.executor_client import get_account_positions, get_position_history
    from app.webhook_handler import close_strategy_position, sync_position_pnl

    # Pending-order reconciliation runs unconditionally (independent of whether any
    # position rows exist yet — a strategy's very first order may still be resting).
    await _reconcile_pending_orders(pool)

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
                acct_id, get_position_history, db_size,
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


async def _reconcile_pending_orders(pool) -> None:
    """
    Detect fills and cancels for resting limit orders (orders.status='pending', placed via
    the webhook path and left resting on the exchange by order-executor since Part 1).

    Fill vs cancel disambiguation: a pending order that has fallen off the executor's
    open-orders list has either filled or been cancelled/expired externally. We distinguish
    by comparing the account's live exchange position for (symbol, side) against what the
    DB already has tracked for that strategy: any exchange size beyond what's already
    tracked is attributed to the oldest not-yet-reconciled pending order for that key
    (orders are processed received_at-ascending, and the DB position is re-read fresh for
    each order, so a second pending order on the same key sees the first order's
    just-applied top-up and can't double-claim the same fill). If nothing beyond what's
    already tracked shows up, the order is marked cancelled and no position is touched.

    Partial fills: if the exchange shows less size attributable than the order's own size
    but more than the reconciler's tolerance, the order is still marked 'filled' with the
    exchange-confirmed (smaller) fill_size — this mirrors how a market order's fill_size can
    already differ from the requested size elsewhere in this codebase (e.g. Blofin lot
    rounding) and is still recorded as 'filled' rather than a separate partial status.

    TP/SL: live investigation (see Part 2 report) found both adapters attach tp/sl at
    order-placement time already — Blofin inline on the order body (but its trigger only
    activates once that specific order fills, sized to that order's own size), Hyperliquid
    via linked 'waitingForFill' child orders in the normalTpsl grouping (pre-sized to the
    ORIGINAL requested size, not any eventual partial-fill size). Neither is guaranteed to
    match the final confirmed position size, so once a fill is materialized here we
    unconditionally (re)apply modify-stops using the order's own tp_price/sl_price — cheap,
    idempotent, and guarantees the trigger size matches the real fill size even under a
    partial fill or Blofin's per-order (non-position-aware) trigger stacking.

    UNKNOWN safety: if the executor is unreachable for an account's open-orders or positions
    fetch, that account's pending orders are left untouched this pass (mirrors the open-
    position UNKNOWN handling in reconcile_once).
    """
    from datetime import datetime, timezone
    from app.executor_client import (
        get_account_positions, get_account_open_orders, call_executor_modify_stops,
    )
    from app.webhook_handler import _apply_position_fill, _update_order_status, _get_account_label
    from app.models import WebhookPayload, OrderResult

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT o.id, o.strategy_id, o.symbol, o.side, o.size, o.price,
                   o.tp_price, o.sl_price, o.exchange_order_id, o.leverage,
                   o.margin_mode, o.signal,
                   s.account_id
            FROM orders o
            JOIN strategies s ON o.strategy_id = s.id
            WHERE o.status = 'pending'
              AND COALESCE(s.is_deleted, false) = false
            ORDER BY o.received_at ASC
            """
        )
    if not rows:
        return

    by_account: dict[str, list] = {}
    for row in rows:
        by_account.setdefault(row["account_id"], []).append(row)

    for acct_id, order_rows in by_account.items():
        open_orders = await get_account_open_orders(acct_id)
        if open_orders is None:
            logger.warning(
                f"reconciler: open-orders UNKNOWN for account {acct_id} — leaving "
                f"{len(order_rows)} pending order(s) untouched this pass"
            )
            continue
        positions = await get_account_positions(acct_id)
        if positions is None:
            logger.warning(
                f"reconciler: positions UNKNOWN for account {acct_id} — leaving "
                f"{len(order_rows)} pending order(s) untouched this pass"
            )
            continue

        resting_ids = {str(o.get("order_id")) for o in open_orders}
        pos_by_key: dict[tuple, dict] = {}
        for p in positions:
            sym  = p.get("symbol") or ""
            side = (p.get("side") or "").lower()
            if sym and side:
                pos_by_key[(sym, side)] = p

        account_label = await _get_account_label(pool, acct_id)

        for row in order_rows:
            exch_oid = row["exchange_order_id"]
            if not exch_oid:
                continue  # never got an exchange id at placement — nothing to check against
            if str(exch_oid) in resting_ids:
                continue  # still resting — nothing to do this pass

            symbol     = row["symbol"]
            pos_side   = "long" if row["side"] == "buy" else "short"
            order_size = Decimal(str(row["size"]))
            tol        = max(_SIZE_EPSILON_ABS, order_size * _SIZE_EPSILON_REL)

            async with pool.acquire() as conn:
                existing = await conn.fetchrow(
                    """SELECT id, size, entry_price FROM strategy_positions
                       WHERE strategy_id = $1 AND symbol = $2 AND side = $3 AND status = 'open'""",
                    row["strategy_id"], symbol, pos_side,
                )
            old_size  = Decimal(str(existing["size"]))        if existing else Decimal("0")
            old_entry = Decimal(str(existing["entry_price"])) if existing else Decimal("0")

            live       = pos_by_key.get((symbol, pos_side))
            exch_size  = Decimal(str(live.get("size")))        if live else Decimal("0")
            exch_entry = Decimal(str(live.get("entry_price"))) if live else Decimal("0")

            available = exch_size - old_size

            base_asset, quote_asset = symbol.split("-", 1)
            synthetic_payload = WebhookPayload(
                base_asset=base_asset, quote_asset=quote_asset,
                side=row["side"], order_type="limit",
                size=order_size, price=row["price"],
                leverage=row["leverage"], margin_mode=row["margin_mode"],
                signal=row["signal"], timestamp=datetime.now(timezone.utc),
                token="reconciler", signal_source="reconciler",
            )
            strategy_stub = {"id": row["strategy_id"]}

            if available > tol:
                fill_size = min(order_size, available)
                # Back-solve the marginal price of just this fill from the exchange's
                # blended aggregate entry, so _apply_position_fill's top-up blend
                # reproduces the exchange-confirmed entry_price exactly (reduces to
                # exch_entry itself when there's no pre-existing DB leg, i.e. old_size=0).
                marginal_price = (
                    (exch_entry * exch_size - old_entry * old_size) / fill_size
                    if fill_size > 0 else exch_entry
                )

                result = OrderResult(
                    success=True, status="filled",
                    exchange_order_id=str(exch_oid),
                    actual_fill_price=Decimal(str(marginal_price)),
                    actual_fill_size=fill_size,
                )
                await _apply_position_fill(
                    pool, strategy_stub, synthetic_payload, symbol, row["id"], result,
                    effective_leverage=int(row["leverage"] or 1),
                    effective_margin_mode=row["margin_mode"] or "isolated",
                )
                await _update_order_status(
                    pool, row["id"], "filled", synthetic_payload, result,
                    acct_id, account_label, row["strategy_id"],
                )
                logger.info(
                    f"reconciler: pending order {row['id']} ({symbol} {pos_side}) FILLED "
                    f"fill_size={fill_size} fill_price={marginal_price}"
                )

                if row["tp_price"] is not None or row["sl_price"] is not None:
                    stop_result = await call_executor_modify_stops(
                        account_id=acct_id, symbol=symbol, side=pos_side,
                        tp_price=row["tp_price"], sl_price=row["sl_price"],
                    )
                    logger.info(
                        f"reconciler: post-fill modify-stops for order {row['id']} "
                        f"({symbol} {pos_side}): success={stop_result.get('success')}"
                    )
            else:
                result = OrderResult(success=False, status="cancelled", exchange_order_id=str(exch_oid))
                await _update_order_status(
                    pool, row["id"], "cancelled", synthetic_payload, result,
                    acct_id, account_label, row["strategy_id"],
                )
                logger.info(
                    f"reconciler: pending order {row['id']} ({symbol} {pos_side}) CANCELLED "
                    f"(no matching fill found on exchange)"
                )


async def _handle_full_external_close(
    pool,
    strategy: dict,
    symbol: str,
    side: str,
    pos_id: uuid.UUID,
    opened_at,
    acct_id: str,
    get_position_history,
    db_size: Decimal,
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

    # Create a synthetic closing order carrying the real closed size.
    # Always created — even when pnl is unconfirmed — so the timeline has a close row.
    # pnl is left NULL when unconfirmed; sync_position_pnl / _recover_manual_close_pnl
    # will fill it in once exchange history confirms it.
    synthetic_order_id: Optional[uuid.UUID] = None
    close_side = "sell" if side == "long" else "buy"
    pnl_float = float(pnl_realized) if pnl_realized is not None and not pnl_unconfirmed else None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO orders
                  (symbol, side, signal, order_type, size, platform,
                   strategy_id, account_id, status, actual_fill_price,
                   pnl, raw_webhook, signal_source)
                VALUES
                  ($1, $2, $3, 'market', $4, 'exchange',
                   $5, $6, 'filled', $7,
                   $8, '{}'::jsonb, 'reconciler')
                RETURNING id
                """,
                symbol, close_side,
                "liquidation" if close_reason == "Liquidated" else "exchange_close",
                float(db_size),
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
    Fallback PnL recovery for closed positions with pnl_realized=NULL (not yet booked)
    and no attributable close orders (manual UI closes, native-SL, liquidation, etc.).
    Calls /positions/history and applies the stale-history guard. Books via
    _book_realized_pnl on the NULL→value transition (idempotent).
    """
    from app.executor_client import get_position_history
    from datetime import datetime, timezone

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sp.id, sp.strategy_id, sp.symbol, sp.side, sp.opened_at,
                   sp.size, sp.closing_price, s.account_id
            FROM strategy_positions sp
            JOIN strategies s ON sp.strategy_id = s.id
            WHERE sp.status = 'closed'
              AND sp.pnl_realized IS NULL
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
        pos_id           = row["id"]
        symbol           = row["symbol"]
        side             = row["side"]
        acct_id          = row["account_id"]
        opened_at        = row["opened_at"]
        pos_size         = row["size"]
        pos_closing_price = row["closing_price"]

        try:
            history = await get_position_history(acct_id, symbol, opened_at)
        except Exception as e:
            logger.warning(f"reconciler: history fetch failed for {pos_id} ({symbol}): {e}")
            continue

        if not history or history.get("pnl_realized") is None:
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
                # Create a linked synthetic close order if none exists
                close_side = "sell" if side == "long" else "buy"
                try:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """
                            INSERT INTO orders
                              (symbol, side, signal, order_type, size, platform,
                               strategy_id, account_id, status, actual_fill_price,
                               pnl, raw_webhook, signal_source, closes_position_id)
                            SELECT $1, $2, 'exchange_close', 'market', $3, 'exchange',
                                   $4, $5, 'filled', $6,
                                   $7, '{}'::jsonb, 'reconciler', $8
                            WHERE NOT EXISTS (
                                SELECT 1 FROM orders o WHERE o.closes_position_id = $8
                            )
                            """,
                            symbol, close_side,
                            float(pos_size),
                            str(updated_row['strategy_id']), acct_id,
                            float(pos_closing_price) if pos_closing_price else None,
                            pnl_float,
                            pos_id,
                        )
                except Exception as e:
                    logger.error(f"reconciler: failed to create close order for {pos_id}: {e}")
        except Exception as e:
            logger.error(f"reconciler: pnl recovery update failed for {pos_id}: {e}")
