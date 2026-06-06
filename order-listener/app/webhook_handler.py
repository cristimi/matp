"""
Webhook reception, authentication, validation, logging, and routing.
Now supports strategy-specific endpoints: /webhook/{strategy_id}
"""

import hmac
import json
import logging
import time
import uuid
import asyncio
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import JSONResponse

from app.database import get_pool
from app.models import WebhookPayload, OrderResponse, OrderResult
from app.redis_client import publish
from app.symbol_validator import resolve_symbol, SymbolMismatchError

logger = logging.getLogger(__name__)
router = APIRouter()


async def _verify_token(token: str, secret: str) -> bool:
    """Constant-time token comparison to prevent timing attacks."""
    return hmac.compare_digest(token.encode(), secret.encode())


async def _log_webhook_call(pool, strategy_id: str, http_status: int, error_message: str | None = None) -> None:
    """Log webhook call attempt to the database."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategy_webhook_calls (strategy_id, http_status, error_message)
            VALUES ($1, $2, $3)
            """,
            strategy_id, http_status, error_message
        )


async def _get_strategy(pool, strategy_id: str):
    """Retrieve strategy configuration from the database."""
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM strategies WHERE id = $1", strategy_id)


async def _check_rate_limit(pool, strategy_id: str, max_signals: int) -> bool:
    """Check if the strategy has exceeded its daily signal limit."""
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM strategy_webhook_calls 
            WHERE strategy_id = $1 
            AND http_status = 200 
            AND received_at >= $2
            """,
            strategy_id,
            datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
        )
        return count < max_signals


async def _insert_signal_log(
    pool, strategy_id: str, source_ip: Optional[str], body_dict: dict
) -> Optional[int]:
    """Insert initial signal_log row. Returns row id, or None on error."""
    try:
        async with pool.acquire() as conn:
            row_id = await conn.fetchval(
                """
                INSERT INTO signal_log (strategy_id, source_ip, raw_body)
                VALUES ($1, $2::inet, $3::jsonb)
                RETURNING id
                """,
                strategy_id,
                source_ip,
                json.dumps(body_dict),
            )
        return row_id
    except Exception as e:
        logger.error(f"Failed to insert signal_log: {e}")
        return None


async def _finalize_signal_log(
    pool,
    signal_log_id: Optional[int],
    http_status: int,
    outcome: str,
    error_detail: Optional[str],
    start_ms: float,
) -> None:
    """Update signal_log row with final outcome. Non-fatal — never raises."""
    if signal_log_id is None:
        return
    try:
        duration_ms = int(time.monotonic() * 1000 - start_ms)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE signal_log
                SET http_status  = $2,
                    outcome      = $3,
                    error_detail = $4,
                    duration_ms  = $5
                WHERE id = $1
                """,
                signal_log_id,
                http_status,
                outcome,
                error_detail,
                duration_ms,
            )
    except Exception as e:
        logger.error(f"Failed to finalize signal_log {signal_log_id}: {e}")


async def _log_order(pool, payload: WebhookPayload, order_id: uuid.UUID, strategy_id: str, pair_id: int, symbol: str, effective_leverage: int) -> None:
    """Write initial order record to PostgreSQL."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO orders (
                id, received_at, pair_id, symbol, side, signal, order_type, size, price,
                leverage, margin_mode, tp_price, sl_price, platform, strategy_id,
                status, raw_webhook, signal_source, signal_metadata, indicator_price
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13, $14, $15,
                'received', $16, $17, $18, $19
            )
            """,
            order_id,
            datetime.now(timezone.utc),
            pair_id,
            symbol,
            payload.side,
            payload.signal,
            payload.order_type,
            payload.size,
            payload.price,
            effective_leverage,
            payload.margin_mode,
            payload.tp_price,
            payload.sl_price,
            'auto',
            strategy_id,
            json.dumps(payload.model_dump(mode="json", exclude={"token"})),
            payload.signal_source,
            json.dumps(payload.signal_metadata),
            payload.indicator_price
        )

async def _update_order_status(pool, order_id: uuid.UUID, status: str, payload: WebhookPayload, result: OrderResult = None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE orders SET status = $1,
                exchange_order_id = $2,
                raw_response = $3,
                error_msg = $4,
                actual_fill_price = $6,
                pnl = $7
            WHERE id = $5
            """,
            status,
            result.exchange_order_id if result else None,
            json.dumps(result.raw_response) if result and result.raw_response else None,
            result.error_msg if result else None,
            order_id,
            result.actual_fill_price if result else None,
            result.pnl if hasattr(result, 'pnl') else None,
        )

    # Publish status update to Redis
    await publish(f"orders:{status}", {
        "event": f"orders:{status}",
        "order_id": str(order_id),
        "status": status,
        "pair": f"{payload.base_asset}-{payload.quote_asset}",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@router.post("/webhook/{strategy_id}", response_model=OrderResponse)
async def receive_webhook(
    strategy_id: str,
    request: Request,
    x_webhook_token: str = Header(None),
):
    start_ms = time.monotonic() * 1000
    pool = get_pool()
    source_ip = request.client.host if request.client else None

    # ── Parse raw body (before any Pydantic validation) ───────────────
    try:
        raw_bytes = await request.body()
        body_dict = json.loads(raw_bytes)
    except Exception:
        body_dict = {}

    # ── Log every attempt immediately ─────────────────────────────────
    signal_log_id = await _insert_signal_log(pool, strategy_id, source_ip, body_dict)

    # ── Validate payload ──────────────────────────────────────────────
    try:
        payload = WebhookPayload(**body_dict)
    except Exception as exc:
        await _finalize_signal_log(pool, signal_log_id, 422, "validation_failed", str(exc), start_ms)
        raise HTTPException(status_code=422, detail=str(exc))

    # ── Load strategy ─────────────────────────────────────────────────
    strategy = await _get_strategy(pool, strategy_id)
    if not strategy:
        logger.warning(f"Rejected webhook: unknown strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 404, "Strategy not found")
        await _finalize_signal_log(pool, signal_log_id, 404, "auth_failed", "Strategy not found", start_ms)
        raise HTTPException(status_code=404, detail="Strategy not found")

    # ── Authenticate ──────────────────────────────────────────────────
    token_to_verify = x_webhook_token or payload.token
    if not token_to_verify or not await _verify_token(token_to_verify, strategy['webhook_secret']):
        logger.warning(f"Rejected webhook: invalid token for strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 403, "Invalid token")
        await _finalize_signal_log(pool, signal_log_id, 403, "auth_failed", "Invalid token", start_ms)
        raise HTTPException(status_code=403, detail="Invalid token")

    # ── Check enabled ─────────────────────────────────────────────────
    if not strategy['webhook_enabled']:
        logger.warning(f"Rejected webhook: disabled strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 403, "Strategy disabled")
        await _finalize_signal_log(pool, signal_log_id, 403, "auth_failed", "Strategy disabled", start_ms)
        raise HTTPException(status_code=403, detail="Strategy disabled")

    # ── Symbol resolution ─────────────────────────────────────────────
    try:
        resolved = resolve_symbol(
            base_asset           = payload.base_asset,
            quote_asset          = payload.quote_asset,
            execution_symbol     = strategy["symbol"],
            allow_quote_variants = strategy.get("allow_quote_variants", False),
            allow_cross_charting = strategy.get("allow_cross_charting", False),
        )
    except SymbolMismatchError as e:
        logger.warning(f"Symbol mismatch for strategy {strategy_id}: {e}")
        await _finalize_signal_log(pool, signal_log_id, 422, "symbol_rejected", str(e), start_ms)
        raise HTTPException(status_code=422, detail=str(e))

    # Apply price stripping if loose coupling was used
    price    = None if resolved.price_stripped else payload.price
    tp_price = None if resolved.price_stripped else payload.tp_price
    sl_price = None if resolved.price_stripped else payload.sl_price

    # ── Effective leverage resolution ─────────────────────────────────
    effective_leverage = (
        int(payload.leverage)
        if payload.leverage is not None
        else int(strategy.get("default_leverage") or 1)
    )

    # ── Risk Management Guards ────────────────────────────────────────

    # Guard 1: Daily signal cap
    signals_today = strategy.get("signals_today", 0) or 0
    max_daily     = strategy.get("max_daily_signals", 500) or 500
    if signals_today >= max_daily:
        detail = (
            f"Daily signal limit reached for strategy {strategy_id}. "
            f"Limit: {max_daily}. Signals today: {signals_today}."
        )
        logger.warning(f"Strategy {strategy_id} daily signal cap reached ({signals_today}/{max_daily})")
        await _finalize_signal_log(pool, signal_log_id, 429, "guard_rejected", detail, start_ms)
        raise HTTPException(status_code=429, detail=detail)

    # Guard 4: Daily drawdown stop
    pnl_today        = float(strategy.get("pnl_today", 0) or 0)
    max_drawdown_pct = float(strategy.get("max_daily_drawdown_percent", 20) or 20)
    drawdown_limit   = -(max_drawdown_pct)

    if pnl_today < drawdown_limit:
        logger.warning(
            f"Strategy {strategy_id} daily drawdown limit reached "
            f"(pnl_today={pnl_today:.2f}, limit={drawdown_limit:.2f}) — auto-disabling"
        )
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE strategies SET enabled = false, updated_at = NOW() WHERE id = $1",
                    strategy_id,
                )
        except Exception as e:
            logger.error(f"Failed to auto-disable strategy {strategy_id}: {e}")

        detail = (
            f"Daily drawdown limit reached for strategy {strategy_id}. "
            f"P&L today: {pnl_today:.2f}. Limit: {drawdown_limit:.2f}. "
            f"Strategy has been automatically disabled. "
            f"Use POST /strategies/{strategy_id}/reset-daily to re-enable."
        )
        await _finalize_signal_log(pool, signal_log_id, 429, "guard_rejected", detail, start_ms)
        raise HTTPException(status_code=429, detail=detail)

    # Guard 2: Max position size
    incoming_size = float(payload.size)
    max_size      = float(strategy.get("max_position_size", 1.0) or 1.0)
    if incoming_size > max_size:
        detail = (
            f"Order size {incoming_size} exceeds strategy maximum "
            f"of {max_size} for strategy {strategy_id}."
        )
        logger.warning(f"Strategy {strategy_id} order size {incoming_size} exceeds max {max_size}")
        await _finalize_signal_log(pool, signal_log_id, 422, "guard_rejected", detail, start_ms)
        raise HTTPException(status_code=422, detail=detail)

    # Guard 3: Max leverage
    max_lev = int(strategy.get("max_leverage", 10) or 10)
    if effective_leverage > max_lev:
        detail = (
            f"Leverage {effective_leverage}x exceeds strategy maximum of "
            f"{max_lev}x for strategy {strategy_id}."
        )
        logger.warning(f"Strategy {strategy_id} leverage {effective_leverage}x exceeds max {max_lev}x")
        await _finalize_signal_log(pool, signal_log_id, 422, "guard_rejected", detail, start_ms)
        raise HTTPException(status_code=422, detail=detail)

    # ── End Risk Guards ───────────────────────────────────────────────

    order_id = uuid.uuid4()

    if resolved.coupling_used:
        logger.info(
            f"Symbol coupling applied for order {order_id}: "
            f"incoming {payload.base_asset}-{payload.quote_asset} → "
            f"{resolved.execution_symbol} via {resolved.coupling_used}. "
            f"Price stripped: {resolved.price_stripped}"
        )

    await _log_webhook_call(pool, strategy_id, 200)
    await _log_order(pool, payload, order_id, strategy_id, None, resolved.execution_symbol, effective_leverage)

    await publish("orders:received", {
        "event": "orders:received",
        "order_id": str(order_id),
        "status": "received",
        "pair": f"{payload.base_asset}-{payload.quote_asset}",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    asyncio.create_task(
        _process_order(
            pool, order_id, payload, strategy, resolved,
            price, tp_price, sl_price, effective_leverage,
            signal_log_id=signal_log_id, start_ms=start_ms,
        )
    )

    return OrderResponse(order_id=order_id, status="received", message="OK")


async def _create_strategy_position(pool, payload: WebhookPayload, strategy: dict, opening_order_id: uuid.UUID, result: OrderResult, effective_leverage: int) -> None:
    """Create a new strategy position record in the database."""
    async with pool.acquire() as conn:
        entry_price = result.actual_fill_price or payload.price or payload.indicator_price or 0
        
        # Look up pair_id
        pair = await conn.fetchrow(
            "SELECT tp.id FROM trading_pairs tp JOIN assets b ON tp.base_asset_id = b.id JOIN assets q ON tp.quote_asset_id = q.id WHERE b.symbol = $1 AND q.symbol = $2",
            payload.base_asset, payload.quote_asset
        )
        pair_id = pair['id'] if pair else None

        # Positions use long/short; orders use buy/sell
        pos_side = "long" if payload.side == "buy" else "short"

        await conn.execute(
            """
            INSERT INTO strategy_positions (
                strategy_id, exchange, symbol, pair_id, side, entry_price, current_price, size,
                leverage, margin_mode, opening_order_id, status, opened_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $6, $7,
                $8, $9, $10, 'open', NOW()
            )
            """,
            strategy['id'],
            strategy.get('exchange', 'auto'),
            f"{payload.base_asset}-{payload.quote_asset}",
            pair_id,
            pos_side,
            entry_price,
            payload.size,
            effective_leverage,
            payload.margin_mode,
            opening_order_id,
        )


async def _process_order(
    pool, order_id: uuid.UUID, payload: WebhookPayload, strategy: dict, resolved,
    price, tp_price, sl_price, effective_leverage: int,
    signal_log_id: Optional[int] = None, start_ms: float = 0.0,
):
    try:
        # Increment daily signal counter (best-effort, non-blocking)
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE strategies
                    SET signals_today = signals_today + 1,
                        last_signal_at = NOW()
                    WHERE id = $1
                    """,
                    strategy['id'],
                )
        except Exception as e:
            logger.warning(f"Failed to increment signals_today for {strategy['id']}: {e}")

        # ── Handle target_position = "flat" ──────────────────────────────
        if payload.target_position == "flat":
            from app.executor_client import call_executor_close_position

            # Look up open position for this strategy
            async with pool.acquire() as conn:
                open_pos = await conn.fetchrow(
                    """
                    SELECT symbol, side
                    FROM strategy_positions
                    WHERE strategy_id = $1
                      AND status = 'open'
                    ORDER BY opened_at DESC
                    LIMIT 1
                    """,
                    strategy['id'],
                )

            if open_pos is None:
                logger.info(
                    f"target_position=flat received for {strategy['id']} "
                    f"but no open position found — ignoring"
                )
                # We've already returned 200 from receive_webhook, so just update the order status
                await _update_order_status(pool, order_id, "no_position", payload, None)
                return

            # Use account_id from strategy record
            account_id = strategy.get("account_id") or "acc_blofin_demo_default"

            close_result = await call_executor_close_position(
                account_id=account_id,
                symbol=open_pos["symbol"],
                side=open_pos["side"],
            )

            logger.info(
                f"Flat signal processed for strategy {strategy['id']}: "
                f"close result = {close_result.get('status')}"
            )

            # Update order record with close result
            await _update_order_status(
                pool, 
                order_id, 
                close_result.get("status", "route_failed"), 
                payload, 
                OrderResult(**close_result) if close_result.get("success") else None
            )

            # ── Update pnl_today if result contains PnL ──────────────────────
            result_pnl = close_result.get("pnl") or close_result.get("realized_pnl")
            if result_pnl is not None:
                try:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE strategies
                            SET pnl_today  = pnl_today + $1,
                                updated_at = NOW()
                            WHERE id = $2
                            """,
                            float(result_pnl),
                            strategy['id'],
                        )
                    logger.debug(
                        f"Updated pnl_today for strategy {strategy['id']}: "
                        f"delta={result_pnl}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to update pnl_today for {strategy['id']}: {e}")
            # ── End pnl_today update ──────────────────────────────────────────

            return
        # ── End flat signal handler ───────────────────────────────────────

        # ── Resolve account_id from strategy record ──────────────────────────
        account_id = strategy.get("account_id") or "acc_blofin_demo_default"

        # ── Build OrderRequest for executor ──────────────────────────────────
        order_request = {
            "order_id":       str(order_id),
            "account_id":     account_id,
            "symbol":         resolved.execution_symbol,
            "side":           payload.side,
            "signal":         payload.signal,
            "order_type":     payload.order_type,
            "size":           str(payload.size),
            "price":          str(price)    if price    else None,
            "leverage":       effective_leverage,
            "margin_mode":    payload.margin_mode,
            "tp_price":       str(tp_price) if tp_price else None,
            "sl_price":       str(sl_price) if sl_price else None,
            "config":         json.loads(strategy["config"]) if strategy.get("config") else {},
            "signal_log_id":  signal_log_id,
        }

        # ── Call executor ─────────────────────────────────────────────────────
        from app.executor_client import call_executor
        exec_result = await call_executor(order_request)
        result      = OrderResult(**exec_result)
        final_status = result.status

        await _update_order_status(pool, order_id, final_status, payload, result)
        await _finalize_signal_log(
            pool, signal_log_id, 200,
            "filled" if result.success else "route_failed",
            result.error_msg, start_ms,
        )

        # ── Update pnl_today if result contains PnL ──────────────────────
        result_pnl = exec_result.get("pnl") or exec_result.get("realized_pnl")
        if result_pnl is not None:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE strategies
                        SET pnl_today  = pnl_today + $1,
                            updated_at = NOW()
                        WHERE id = $2
                        """,
                        float(result_pnl),
                        strategy['id'],
                    )
                logger.debug(
                    f"Updated pnl_today for strategy {strategy['id']}: "
                    f"delta={result_pnl}"
                )
            except Exception as e:
                logger.warning(f"Failed to update pnl_today for {strategy['id']}: {e}")
        # ── End pnl_today update ──────────────────────────────────────────

        logger.info(f"Order {order_id} processed for strategy {strategy['id']}: {final_status}")

        if result.success and payload.base_asset and payload.quote_asset and payload.side and payload.size:
            async with pool.acquire() as conn:
                # Lookup pair_id
                pair = await conn.fetchrow(
                    "SELECT tp.id FROM trading_pairs tp JOIN assets b ON tp.base_asset_id = b.id JOIN assets q ON tp.quote_asset_id = q.id WHERE b.symbol = $1 AND q.symbol = $2",
                    payload.base_asset, payload.quote_asset
                )
                pair_id = pair['id'] if pair else None

                # Check for existing open position
                existing = await conn.fetchrow(
                    "SELECT sp.id, sp.size FROM strategy_positions sp WHERE strategy_id = $1 AND pair_id = $2 AND status = 'open'",
                    strategy['id'], pair_id
                )

                if existing:
                    pass  # position already exists, no action needed
                else:
                    # Only create a strategy position if the order was successful
                    await _create_strategy_position(pool, payload, strategy, order_id, result, effective_leverage)

    except Exception as e:
        logger.exception(f"Error processing order {order_id}: {e}")
        await _update_order_status(pool, order_id, "route_failed", payload, None)
        await _finalize_signal_log(pool, signal_log_id, 200, "route_failed", str(e), start_ms)
