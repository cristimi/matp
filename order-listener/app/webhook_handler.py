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
from app.redis_client import publish, cache_get, cache_set, cache_delete
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


_STRATEGY_CACHE_TTL = 5   # seconds, per SDD §4.1 / §7.3
_ACCOUNT_LABEL_TTL  = 60  # account labels rarely change

async def _get_account_label(pool, account_id: str) -> str:
    """Return the human-readable label for an account, Redis-cached."""
    if not account_id:
        return ""
    cache_key = f"config:account_label:{account_id}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached.get("label", "")
    async with pool.acquire() as conn:
        label = await conn.fetchval(
            "SELECT label FROM exchange_accounts WHERE id = $1", account_id
        )
    label = label or ""
    await cache_set(cache_key, {"label": label}, ttl=_ACCOUNT_LABEL_TTL)
    return label


async def _get_strategy(pool, strategy_id: str):
    """Retrieve strategy configuration, Redis-cached with 5s TTL."""
    cache_key = f"config:strategy_cache:{strategy_id}"

    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM strategies WHERE id = $1 AND COALESCE(is_deleted, false) = false",
            strategy_id,
        )

    if row:
        data = dict(row)
        await cache_set(cache_key, data, ttl=_STRATEGY_CACHE_TTL)
        return data

    return None


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
        # Extract AI fields from signal_metadata (safe for non-AI signals)
        signal_metadata = body_dict.get('signal_metadata') or {}
        ai_reasoning    = signal_metadata.get('reasoning')    # str | None
        ai_confidence   = signal_metadata.get('confidence')   # float | None

        async with pool.acquire() as conn:
            row_id = await conn.fetchval(
                """
                INSERT INTO signal_log (strategy_id, source_ip, raw_body, ai_reasoning, ai_confidence)
                VALUES ($1, $2::inet, $3::jsonb, $4, $5)
                RETURNING id
                """,
                strategy_id,
                source_ip,
                json.dumps(body_dict),
                ai_reasoning,
                ai_confidence,
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


async def _log_order(pool, payload: WebhookPayload, order_id: uuid.UUID, strategy_id: str, pair_id: int, symbol: str, effective_leverage: int, effective_margin_mode: str = "isolated") -> None:
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
            effective_margin_mode,
            payload.tp_price,
            payload.sl_price,
            'auto',
            strategy_id,
            json.dumps(payload.model_dump(mode="json", exclude={"token"})),
            payload.signal_source,
            json.dumps(payload.signal_metadata),
            payload.indicator_price
        )

async def _update_order_status(
    pool, order_id: uuid.UUID, status: str, payload: WebhookPayload,
    result: OrderResult = None,
    account_id: str = "",
    account_label: str = "",
    strategy_id: str = "",
) -> None:
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
            result.realized_pnl if result else None,
        )

    # Publish status update to Redis
    await publish(f"orders:{status}", {
        "event":              f"orders:{status}",
        "order_id":           str(order_id),
        "status":             status,
        "symbol":             f"{payload.base_asset}-{payload.quote_asset}",
        "side":               payload.side,
        "size":               str(payload.size),
        "signal":             payload.signal,
        "signal_source":      payload.signal_source or "",
        "actual_fill_price":  str(result.actual_fill_price) if result and result.actual_fill_price else None,
        "account_id":         account_id,
        "account_label":      account_label,
        "strategy_id":        strategy_id,
        "timestamp":          datetime.now(timezone.utc).isoformat(),
    })


@router.post("/positions/{position_id}/close")
async def close_position_by_id(position_id: str, request: Request):
    """Manual close: look up the position, derive strategy/symbol/side, call canonical routine."""
    pool = get_pool()
    body_dict: dict = {}
    try:
        raw_bytes = await request.body()
        if raw_bytes:
            body_dict = json.loads(raw_bytes)
    except Exception:
        pass

    close_size_raw = body_dict.get("size")
    close_size = Decimal(str(close_size_raw)) if close_size_raw else None

    try:
        pos_uuid = uuid.UUID(position_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid position_id format")

    async with pool.acquire() as conn:
        pos = await conn.fetchrow(
            """
            SELECT id, strategy_id, symbol, side, status
            FROM strategy_positions
            WHERE id = $1
            """,
            pos_uuid,
        )

    if pos is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if pos['status'] != 'open':
        raise HTTPException(status_code=400, detail=f"Position is {pos['status']}, not open")

    strategy = await _get_strategy(pool, pos['strategy_id'])
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    result = await close_strategy_position(
        pool, strategy,
        symbol=pos['symbol'],
        side=pos['side'],
        close_size=close_size,
    )

    if not result.get('success'):
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": result.get("error_msg", "Close failed")},
        )

    return result


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
    if not strategy['enabled']:
        logger.warning(f"Rejected webhook: stopped strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 403, "Strategy stopped")
        await _finalize_signal_log(pool, signal_log_id, 403, "auth_failed", "Strategy stopped", start_ms)
        raise HTTPException(status_code=403, detail="Strategy stopped")

    if not strategy['webhook_enabled']:
        logger.warning(f"Rejected webhook: webhooks disabled strategy={strategy_id}")
        await _log_webhook_call(pool, strategy_id, 403, "Webhooks disabled")
        await _finalize_signal_log(pool, signal_log_id, 403, "auth_failed", "Webhooks disabled", start_ms)
        raise HTTPException(status_code=403, detail="Webhooks disabled")

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

    # ── Effective margin mode resolution ──────────────────────────────
    effective_margin_mode = (
        payload.margin_mode
        if payload.margin_mode is not None
        else (strategy.get("margin_mode") or "isolated")
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
    await _log_order(pool, payload, order_id, strategy_id, None, resolved.execution_symbol, effective_leverage, effective_margin_mode)

    _acct_id    = strategy.get("account_id") or ""
    _acct_label = await _get_account_label(pool, _acct_id)
    _strat_id   = strategy.get("id") or strategy_id
    await publish("orders:received", {
        "event":             "orders:received",
        "order_id":          str(order_id),
        "status":            "received",
        "symbol":            f"{payload.base_asset}-{payload.quote_asset}",
        "side":              payload.side,
        "size":              str(payload.size),
        "signal":            payload.signal,
        "signal_source":     payload.signal_source or "",
        "actual_fill_price": None,
        "account_id":        _acct_id,
        "account_label":     _acct_label,
        "strategy_id":       _strat_id,
        "timestamp":         datetime.now(timezone.utc).isoformat(),
    })

    asyncio.create_task(
        _process_order(
            pool, order_id, payload, strategy, resolved,
            price, tp_price, sl_price, effective_leverage, effective_margin_mode,
            signal_log_id=signal_log_id, start_ms=start_ms,
            account_id=_acct_id, account_label=_acct_label, strategy_id=_strat_id,
        )
    )

    return OrderResponse(order_id=order_id, status="received", message="OK")


async def _create_strategy_position(pool, payload: WebhookPayload, strategy: dict, opening_order_id: uuid.UUID, result: OrderResult, effective_leverage: int, effective_margin_mode: str = "isolated") -> None:
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
            effective_margin_mode,
            opening_order_id,
        )


async def close_strategy_position(
    pool,
    strategy: dict,
    symbol: str,
    side: str,
    close_size: Optional[Decimal] = None,
    closing_order_id: Optional[uuid.UUID] = None,
    reason: Optional[str] = None,
    skip_exchange: bool = False,
    fill_price=None,
    realized_pnl=None,
) -> dict:
    """
    Canonical close/reduce routine. Atomic: exchange call before DB write.
    - close_size=None or >= open size  → full close (status='closed')
    - close_size < open size           → partial reduce (status stays 'open')
    - skip_exchange=True               → exchange already executed; pass fill_price/realized_pnl
    Returns a dict: {success, status, actual_fill_price, realized_pnl, ...}
    """
    from app.executor_client import call_executor_close_position

    async with pool.acquire() as conn:
        pos = await conn.fetchrow(
            """
            SELECT id, size
            FROM strategy_positions
            WHERE strategy_id = $1 AND symbol = $2 AND side = $3 AND status = 'open'
            """,
            strategy['id'], symbol, side,
        )

    if pos is None:
        logger.info(
            f"close_strategy_position: no open {side} {symbol} for strategy {strategy['id']}"
        )
        return {
            "success": False,
            "status": "no_position_to_close",
            "error_msg": f"No open {side} position for {symbol}",
        }

    current_size = Decimal(str(pos['size']))
    eff_close_size = (
        min(Decimal(str(close_size)), current_size)
        if close_size is not None else current_size
    )
    is_full = eff_close_size >= current_size

    if not skip_exchange:
        acct_id = strategy.get('account_id') or ""
        close_result = await call_executor_close_position(
            account_id=acct_id,
            symbol=symbol,
            side=side,
            size=eff_close_size if not is_full else None,
        )
        if not close_result.get('success'):
            return close_result
        fill_price   = close_result.get('actual_fill_price')
        realized_pnl = close_result.get('realized_pnl')

    fill_price_f   = float(fill_price)   if fill_price   is not None else None
    realized_pnl_f = float(realized_pnl) if realized_pnl is not None else None

    async with pool.acquire() as conn:
        if is_full:
            updated = await conn.fetchrow(
                """
                UPDATE strategy_positions
                SET status           = 'closed',
                    closing_price    = $1,
                    pnl_realized     = pnl_realized + COALESCE($2, 0),
                    closing_order_id = $3,
                    closed_at        = NOW(),
                    updated_at       = NOW()
                WHERE id = $4 AND status = 'open'
                RETURNING id
                """,
                fill_price_f,
                realized_pnl_f,
                closing_order_id,
                pos['id'],
            )
        else:
            updated = await conn.fetchrow(
                """
                UPDATE strategy_positions
                SET size         = size - $1,
                    pnl_realized = pnl_realized + COALESCE($2, 0),
                    updated_at   = NOW()
                WHERE id = $3 AND status = 'open'
                RETURNING id
                """,
                eff_close_size,
                realized_pnl_f,
                pos['id'],
            )

    if updated is None:
        logger.warning(f"close_strategy_position: race condition on position {pos['id']}")
        return {
            "success": False,
            "status": "race_condition",
            "error_msg": "Position already closed by concurrent request",
        }

    logger.info(
        f"{'Closed' if is_full else 'Partially closed'} position {pos['id']} "
        f"for strategy {strategy['id']} ({symbol} {side}), "
        f"close_size={eff_close_size}, fill={fill_price_f}, pnl={realized_pnl_f}"
    )

    ret: dict = {
        "success":           True,
        "status":            "filled",
        "actual_fill_price": fill_price,
        "realized_pnl":      realized_pnl,
        "is_full_close":     is_full,
        "position_id":       str(pos['id']),
    }
    if not skip_exchange:
        ret["exchange_order_id"] = close_result.get('exchange_order_id')
    return ret


async def _process_order(
    pool, order_id: uuid.UUID, payload: WebhookPayload, strategy: dict, resolved,
    price, tp_price, sl_price, effective_leverage: int, effective_margin_mode: str = "isolated",
    signal_log_id: Optional[int] = None, start_ms: float = 0.0,
    account_id: str = "", account_label: str = "", strategy_id: str = "",
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
            flat_symbol = resolved.execution_symbol

            # Look up open position by execution symbol (not by recency)
            async with pool.acquire() as conn:
                open_pos = await conn.fetchrow(
                    """
                    SELECT symbol, side
                    FROM strategy_positions
                    WHERE strategy_id = $1 AND symbol = $2 AND status = 'open'
                    """,
                    strategy['id'], flat_symbol,
                )

            if open_pos is None:
                logger.info(
                    f"target_position=flat for {strategy['id']}: "
                    f"no open {flat_symbol} position — ignoring"
                )
                await _update_order_status(pool, order_id, "no_position", payload, None, account_id, account_label, strategy_id)
                return

            close_result = await close_strategy_position(
                pool, strategy,
                symbol=open_pos["symbol"],
                side=open_pos["side"],
                closing_order_id=order_id,
            )

            logger.info(
                f"Flat signal processed for strategy {strategy['id']}: "
                f"close result = {close_result.get('status')}"
            )

            flat_order_result = None
            if close_result.get("success"):
                fp = close_result.get("actual_fill_price")
                rp = close_result.get("realized_pnl")
                flat_order_result = OrderResult(
                    success=True,
                    status="filled",
                    exchange_order_id=close_result.get("exchange_order_id"),
                    actual_fill_price=Decimal(str(fp)) if fp is not None else None,
                    realized_pnl=Decimal(str(rp))      if rp is not None else None,
                )

            await _update_order_status(
                pool, order_id,
                close_result.get("status", "route_failed"),
                payload,
                flat_order_result,
                account_id,
                account_label,
                strategy_id,
            )

            # Update pnl_today
            result_pnl = close_result.get("realized_pnl")
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
                    logger.debug(f"Updated pnl_today for {strategy['id']}: delta={result_pnl}")
                except Exception as e:
                    logger.warning(f"Failed to update pnl_today for {strategy['id']}: {e}")

            return
        # ── End flat signal handler ───────────────────────────────────────

        # ── Resolve account_id from strategy record ──────────────────────────
        account_id = strategy.get("account_id") or "acc_blofin_demo_default"

        pos_symbol = resolved.execution_symbol

        # ── Step 6: Same-symbol guard (open signals only) ─────────────────────
        if payload.signal in ("open_long", "open_short"):
            _conflict_reason = None
            try:
                async with pool.acquire() as conn:
                    _conflict_rows = await conn.fetch(
                        """
                        SELECT sp.strategy_id, sp.side
                        FROM strategy_positions sp
                        JOIN strategies s ON sp.strategy_id = s.id
                        WHERE s.account_id   = $1
                          AND sp.symbol      = $2
                          AND sp.status      = 'open'
                          AND sp.strategy_id != $3
                          AND s.enabled      = true
                        """,
                        account_id, pos_symbol, strategy['id'],
                    )
                if _conflict_rows:
                    _open_sides = {r['side'] for r in _conflict_rows}
                    _our_side   = "long" if payload.signal == "open_long" else "short"
                    _opposite   = "short" if _our_side == "long" else "long"
                    # status must fit varchar(20); "same_symbol_conflict"=19, "opp_pos_conflict"=16
                    _conflict_reason = "opp_pos_conflict" if _opposite in _open_sides else "same_symbol_conflict"
            except Exception as e:
                logger.warning(f"Same-symbol guard DB query failed (continuing): {e}")

            if _conflict_reason:
                logger.warning(
                    f"Strategy {strategy['id']} {payload.signal} rejected: "
                    f"{_conflict_reason} on {pos_symbol} (account={account_id})"
                )
                await _update_order_status(pool, order_id, _conflict_reason, payload, None, account_id, account_label, strategy_id)
                await _finalize_signal_log(pool, signal_log_id, 200, "guard_rejected", _conflict_reason, start_ms)
                return

        # ── Step 7: Min-order-value guard ─────────────────────────────────────
        # Fetch per-instrument min base size from executor; fall back to $1 floor
        _min_base_size = 0.0
        try:
            from app.executor_client import call_executor_get
            _instr_info = await call_executor_get(
                f"/accounts/{account_id}/min-order-size/{pos_symbol}"
            )
            _min_base_size = float(_instr_info.get("min_base_size") or 0.0)
        except Exception as _e:
            logger.warning(f"min-order-size lookup failed for {pos_symbol}: {_e}")

        _price_est  = float(payload.indicator_price or payload.price or 0)
        _order_size = float(payload.size)
        _notional   = _order_size * _price_est
        _size_rejected = (
            (_min_base_size > 0 and _order_size < _min_base_size)
            or (_price_est > 0 and _notional < 1.0)
        )
        if _size_rejected:
            logger.warning(
                f"Order {order_id} rejected: size={_order_size} "
                f"(min_base={_min_base_size:.8g}), "
                f"notional={_notional:.4f} (size={payload.size}, price={_price_est})"
            )
            await _update_order_status(pool, order_id, "size_too_small", payload, None, account_id, account_label, strategy_id)
            await _finalize_signal_log(pool, signal_log_id, 200, "guard_rejected", "size_too_small", start_ms)
            return

        # ── Build OrderRequest for executor ──────────────────────────────────
        order_request = {
            "order_id":       str(order_id),
            "account_id":     account_id,
            "symbol":         pos_symbol,
            "side":           payload.side,
            "signal":         payload.signal,
            "order_type":     payload.order_type,
            "size":           str(payload.size),
            "price":          str(price)    if price    else None,
            "leverage":       effective_leverage,
            "margin_mode":    effective_margin_mode,
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

        await _update_order_status(pool, order_id, final_status, payload, result, account_id, account_label, strategy_id)
        await _finalize_signal_log(
            pool, signal_log_id, 200,
            "filled" if result.success else "route_failed",
            result.error_msg, start_ms,
        )

        # ── Update pnl_today ──────────────────────────────────────────────────
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
                logger.debug(f"Updated pnl_today for {strategy['id']}: delta={result_pnl}")
            except Exception as e:
                logger.warning(f"Failed to update pnl_today for {strategy['id']}: {e}")

        logger.info(f"Order {order_id} processed for strategy {strategy['id']}: {final_status}")

        if result.success and payload.base_asset and payload.quote_asset and payload.size:

            # ── Close path: canonical routine (exchange already executed) ─────
            if payload.signal in ("close_long", "close_short"):
                pos_side = "long" if payload.signal == "close_long" else "short"
                # Pass payload.size as close_size: if size < open size it's a partial reduce
                # (e.g. AI partial_close); if size >= open size it's a full close.
                await close_strategy_position(
                    pool, strategy,
                    symbol=pos_symbol,
                    side=pos_side,
                    close_size=payload.size,
                    closing_order_id=order_id,
                    skip_exchange=True,
                    fill_price=result.actual_fill_price,
                    realized_pnl=result.realized_pnl,
                )

            # ── Open path: top-up existing or create new position ─────────────
            elif payload.signal in ("open_long", "open_short"):
                pos_side = "long" if payload.signal == "open_long" else "short"
                fill_price_val = (
                    result.actual_fill_price
                    or payload.price
                    or payload.indicator_price
                    or Decimal("0")
                )

                async with pool.acquire() as conn:
                    existing = await conn.fetchrow(
                        """SELECT id, size, entry_price FROM strategy_positions
                           WHERE strategy_id = $1 AND symbol = $2 AND side = $3
                             AND status = 'open'""",
                        strategy['id'], pos_symbol, pos_side,
                    )

                    if existing:
                        old_size  = Decimal(str(existing['size']))
                        old_entry = Decimal(str(existing['entry_price']))
                        new_size  = old_size + payload.size
                        new_entry = (
                            (old_entry * old_size + Decimal(str(fill_price_val)) * payload.size)
                            / new_size
                        )
                        await conn.execute(
                            """
                            UPDATE strategy_positions
                            SET size        = $1,
                                entry_price = $2,
                                updated_at  = NOW()
                            WHERE id = $3
                            """,
                            new_size, new_entry, existing['id'],
                        )
                        logger.info(
                            f"Topped up position {existing['id']} for {strategy['id']} "
                            f"({pos_symbol} {pos_side}): "
                            f"size {old_size}→{new_size}, entry {old_entry:.4f}→{new_entry:.4f}"
                        )
                    else:
                        await _create_strategy_position(
                            pool, payload, strategy, order_id, result,
                            effective_leverage, effective_margin_mode,
                        )

    except Exception as e:
        logger.exception(f"Error processing order {order_id}: {e}")
        await _update_order_status(pool, order_id, "route_failed", payload, None, account_id, account_label, strategy_id)
        await _finalize_signal_log(pool, signal_log_id, 200, "route_failed", str(e), start_ms)
