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

# Liquidation-safety guard (Phase 3, safety-sl-fix-report.md): when an active SL is
# found on the wrong side of live liquidation, tighten it to land this fraction of the
# entry->liquidation distance back from liq, toward entry. 10% is comparable in
# magnitude to the Phase 1/2 open-time conservatism buffer (both land the tightened
# BTC-40x-short case within ~1% of each other — see the phase 3 report) while being
# proportional to the actual live distance rather than a fixed price offset, so it
# scales sensibly across symbols/leverage.
_TIGHTEN_MARGIN_FRAC = Decimal("0.10")
# Absolute floor for the pull-back, as a fraction of entry price — guards the edge case
# where entry->liq distance is itself tiny (extreme leverage), where 10% of it could be
# smaller than exchange tick/rounding noise and risk landing back on the unsafe side.
_TIGHTEN_MIN_MARGIN_FRAC = Decimal("0.0005")
# Hard cap: never pull back more than this fraction of the distance, so a pathological
# tiny-distance case can't push the new SL past entry itself.
_TIGHTEN_MAX_MARGIN_FRAC = Decimal("0.90")


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
                   sp.reconcile_divergent, sp.opening_order_id,
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
        raw_by_key: dict[tuple, dict] = {}
        for p in positions:
            try:
                sym  = p.get("symbol") or p.get("instId") or ""
                side = (p.get("side") or "").lower()
                size = Decimal(str(p.get("size") or "0"))
                if sym and side and size > 0:
                    pos_map[(sym, side)] = size
                    raw_by_key[(sym, side)] = p
            except Exception as e:
                logger.warning(f"reconciler: bad position entry from exchange: {p}: {e}")
        exchange_map[acct_id] = pos_map
        logger.debug(f"reconciler: account {acct_id} has {len(pos_map)} live positions")

        # Belt-and-suspenders liquidation-safety guard: backfill liquidation_price and
        # detect/tighten any SL on the wrong side of the exchange's own live liq price.
        # Uses the positions/rows already fetched above — no extra position reads.
        await _guard_liquidation_safety(pool, acct_id, by_account[acct_id], raw_by_key)

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


async def _guard_liquidation_safety(
    pool, acct_id: str, db_rows: list, raw_by_key: dict,
) -> None:
    """
    After-fill belt-and-suspenders (Phase 3 of docs/process/reports/
    safety-sl-fix-report.md): cross-check each open position's CURRENTLY-active SL
    against the exchange's own reported liquidation_price. The Phase 1/2 open-time
    formula estimates a safe SL before the position exists (from a derived/fallback
    MMR plus a conservatism buffer); this guard checks the real thing once it does.

    Authoritative "current SL" source: the exchange's own resting SL trigger order
    (get_trigger_orders), NOT any DB column. strategy_positions has no sl_price column,
    and the /strategies/{id}/adjust-stops management route can change a position's live
    stop without writing anything back to the DB — so orders.sl_price (the value at the
    ORIGINAL fill) can silently go stale. If no resting SL trigger is found this pass,
    no comparison is made — this guard only ever tightens an EXISTING stop, never
    invents one.

    db_rows: this account's open strategy_positions rows (from the caller's already-
    fetched query). raw_by_key: (symbol, side) -> live position dict for this account
    (from the caller's already-fetched get_account_positions() call — no extra read).
    """
    import asyncio
    import json
    from datetime import datetime, timezone
    from app.executor_client import get_trigger_orders, call_executor_modify_stops
    from app.webhook_handler import _infer_price_decimals

    _TIGHTEN_ATTEMPTS = 3
    _TIGHTEN_RETRY_DELAY_S = 1.5

    async def _is_guard_managed(opening_order_id) -> bool:
        """True if a prior pass of THIS guard successfully placed the resting SL —
        i.e. we are responsible for this position's stop existing at all, so a stop
        that later disappears is our failure to fix, not an intentional gap to leave
        alone."""
        if opening_order_id is None:
            return False
        async with pool.acquire() as conn:
            meta_row = await conn.fetchrow(
                "SELECT signal_metadata->>'liq_safety_tightened' AS flag FROM orders WHERE id = $1",
                opening_order_id,
            )
        return bool(meta_row and meta_row["flag"] == "true")

    async def _place_and_verify(symbol, side, new_sl, current_tp, liq_price) -> Optional[Decimal]:
        """
        Call modify-stops and verify the new SL actually landed by reading it back —
        do NOT trust call_executor_modify_stops's top-level 'success' alone. Confirmed
        live: Hyperliquid's place_trigger_orders can return success=True with a
        per-leg 'error' inside `placed` (e.g. testnet "Only post-only orders allowed
        immediately after network upgrade") when the whole signed action was accepted
        but an individual trigger leg was rejected — modify-stops already CANCELLED
        the old stop by that point, so a blind trust here can leave a position with NO
        SL at all. Retries a few times (this specific rejection was transient in
        practice). Returns the landed SL price (Decimal) on confirmed success, else None.
        """
        for attempt in range(1, _TIGHTEN_ATTEMPTS + 1):
            await call_executor_modify_stops(
                account_id=acct_id, symbol=symbol, side=side,
                tp_price=current_tp, sl_price=new_sl,
            )
            verify = await get_trigger_orders(acct_id, symbol)
            if verify is not None:
                sl_now = [
                    t for t in verify
                    if t.get("tpsl") == "sl" and t.get("triggerPx") is not None
                ]
                if sl_now:
                    try:
                        landed = Decimal(str(sl_now[0]["triggerPx"]))
                    except Exception:
                        landed = None
                    if landed is not None:
                        landed_safe = not (
                            (side == "short" and landed >= liq_price) or
                            (side == "long"  and landed <= liq_price)
                        )
                        if landed_safe:
                            return landed
            logger.warning(
                f"reconciler: liquidation-safety modify-stops attempt {attempt}/"
                f"{_TIGHTEN_ATTEMPTS} for {acct_id}/{symbol} did not land a verified "
                f"safe SL — {'retrying' if attempt < _TIGHTEN_ATTEMPTS else 'giving up this pass'}"
            )
            if attempt < _TIGHTEN_ATTEMPTS:
                await asyncio.sleep(_TIGHTEN_RETRY_DELAY_S)
        return None

    for row in db_rows:
        symbol = row["symbol"]
        side   = row["side"]
        pos    = raw_by_key.get((symbol, side))
        if not pos:
            continue  # not live on exchange this pass — size-reconciliation path handles it

        liq_raw = pos.get("liquidation_price")
        if liq_raw is None:
            continue  # exchange didn't report one (e.g. some cross-margin states) — can't guard
        try:
            liq_price = Decimal(str(liq_raw))
        except Exception:
            continue
        if liq_price <= 0:
            continue

        # Backfill strategy_positions.liquidation_price — was always NULL before this guard.
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE strategy_positions
                SET liquidation_price = $1, updated_at = NOW()
                WHERE id = $2 AND status = 'open'
                  AND (liquidation_price IS NULL OR liquidation_price != $1)
                """,
                liq_price, row["id"],
            )

        trigger_orders = await get_trigger_orders(acct_id, symbol)
        if trigger_orders is None:
            logger.warning(
                f"reconciler: trigger-orders UNKNOWN for {acct_id}/{symbol} "
                f"(executor/exchange unreachable) — skipping liquidation-safety check this pass"
            )
            continue

        sl_triggers = [
            t for t in trigger_orders
            if t.get("tpsl") == "sl" and t.get("triggerPx") is not None
        ]
        tp_triggers = [
            t for t in trigger_orders
            if t.get("tpsl") == "tp" and t.get("triggerPx") is not None
        ]
        current_tp = None
        if tp_triggers:
            try:
                current_tp = float(Decimal(str(tp_triggers[0]["triggerPx"])))
            except Exception:
                current_tp = None

        opening_order_id = row["opening_order_id"]

        if not sl_triggers:
            # Never invent a stop for a position we never touched — but if a PRIOR
            # pass of this guard placed one and it's since vanished (e.g. the
            # modify-stops leg-rejection race above), that's our gap to close, not an
            # intentional no-SL choice to respect.
            if not await _is_guard_managed(opening_order_id):
                continue
            logger.error(
                f"reconciler: position {row['id']} ({symbol} {side}) is guard-managed "
                f"but has NO resting SL trigger — exchange-side stop is MISSING, "
                f"re-establishing protection"
            )
            active_sl_for_log = None
        else:
            try:
                active_sl = Decimal(str(sl_triggers[0]["triggerPx"]))
            except Exception:
                continue
            is_unsafe = (
                (side == "short" and active_sl >= liq_price) or
                (side == "long"  and active_sl <= liq_price)
            )
            if not is_unsafe:
                continue
            logger.warning(
                f"reconciler: UNSAFE SL detected for position {row['id']} ({symbol} {side}) "
                f"active_sl={active_sl} liquidation_price={liq_price} — "
                f"SL is on the wrong side of live liquidation"
            )
            active_sl_for_log = active_sl

        entry_raw = pos.get("entry_price")
        try:
            entry_price = Decimal(str(entry_raw)) if entry_raw is not None else None
        except Exception:
            entry_price = None
        if not entry_price or entry_price <= 0:
            logger.error(
                f"reconciler: cannot tighten SL for {row['id']} ({symbol} {side}) — "
                f"no usable entry_price from exchange this pass"
            )
            continue

        distance = abs(liq_price - entry_price)
        if distance <= 0:
            logger.error(
                f"reconciler: cannot tighten SL for {row['id']} ({symbol} {side}) — "
                f"liquidation_price == entry_price ({liq_price}), degenerate distance"
            )
            continue

        margin = max(distance * _TIGHTEN_MARGIN_FRAC, entry_price * _TIGHTEN_MIN_MARGIN_FRAC)
        margin = min(margin, distance * _TIGHTEN_MAX_MARGIN_FRAC)
        new_sl = (liq_price - margin) if side == "short" else (liq_price + margin)
        new_sl = round(float(new_sl), _infer_price_decimals(float(entry_price)))

        landed_sl = await _place_and_verify(symbol, side, new_sl, current_tp, liq_price)
        if landed_sl is None:
            logger.error(
                f"reconciler: liquidation-safety tighten FAILED (unverified) for {row['id']} "
                f"({symbol} {side}) after {_TIGHTEN_ATTEMPTS} attempts — position may be "
                f"UNPROTECTED; will retry next pass"
            )
            continue

        logger.warning(
            f"reconciler: liquidation-safety TIGHTENED SL for {row['id']} ({symbol} {side}): "
            f"{active_sl_for_log} -> {landed_sl} (liq={liq_price}, margin={margin}, "
            f"tp_preserved={current_tp})"
        )

        if opening_order_id is None:
            logger.warning(
                f"reconciler: position {row['id']} has no opening_order_id — "
                f"tighten applied but not recorded in signal_metadata"
            )
            continue

        audit_patch = {
            "liq_safety_tightened":         True,
            "liq_safety_tightened_at":      datetime.now(timezone.utc).isoformat(),
            "liq_safety_prior_sl":          float(active_sl_for_log) if active_sl_for_log is not None else None,
            "liq_safety_new_sl":            float(landed_sl),
            "liq_safety_liquidation_price": float(liq_price),
        }
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE orders
                SET signal_metadata = COALESCE(signal_metadata, '{}'::jsonb) || $1::jsonb,
                    updated_at = NOW()
                WHERE id = $2
                """,
                json.dumps(audit_patch), opening_order_id,
            )


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
