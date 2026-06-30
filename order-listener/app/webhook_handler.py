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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import JSONResponse

from app.config import MMR, MIN_SAFETY_SL_DIST
from app.database import get_pool
from app.executor_client import get_mark_price
from app.models import WebhookPayload, OrderResponse, OrderResult
from app.redis_client import publish, cache_get, cache_set, cache_delete
from app.symbol_validator import resolve_symbol, SymbolMismatchError

logger = logging.getLogger(__name__)
router = APIRouter()


def _client_ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return request.client.host if request.client else None


def _infer_price_decimals(price: float) -> int:
    """Infer sensible decimal precision from price magnitude."""
    if price >= 10_000:
        return 1
    elif price >= 1_000:
        return 2
    elif price >= 100:
        return 3
    elif price >= 10:
        return 4
    elif price >= 1:
        return 5
    return 6


def compute_guaranteed_sl(
    entry_ref: float,
    effective_leverage: int,
    side: str,
    strategy_sl: Optional[float],
) -> tuple[float, str]:
    """
    Compute the tighter of (strategy SL, liquidation-safe SL).
    Returns (sl_final, sl_source) where sl_source is 'strategy' or 'liquidation_safe'.
    side must be 'long' or 'short'.
    """
    sl_distance = max((1.0 / effective_leverage) - MMR, MIN_SAFETY_SL_DIST)
    sl_liq = (
        entry_ref * (1 - sl_distance) if side == "long"
        else entry_ref * (1 + sl_distance)
    )

    strategy_sl_valid = (
        strategy_sl is not None
        and ((side == "long"  and strategy_sl < entry_ref)
             or  (side == "short" and strategy_sl > entry_ref))
    )

    if strategy_sl_valid:
        if side == "long":
            sl_final  = max(strategy_sl, sl_liq)   # higher price = closer to entry
            sl_source = "strategy" if strategy_sl >= sl_liq else "liquidation_safe"
        else:
            sl_final  = min(strategy_sl, sl_liq)   # lower price = closer to entry
            sl_source = "strategy" if strategy_sl <= sl_liq else "liquidation_safe"
    else:
        sl_final  = sl_liq
        sl_source = "liquidation_safe"

    return round(sl_final, _infer_price_decimals(entry_ref)), sl_source


async def _verify_token(token: str, secret: str) -> bool:
    """Constant-time token comparison to prevent timing attacks."""
    return hmac.compare_digest(token.encode(), secret.encode())


def _is_drawdown_breached(cap_alloc: float, peak: float, max_dd_pct: float) -> bool:
    """True if the live allocation has fallen max_dd_pct below its high-water peak."""
    if peak <= 0:
        return False
    floor = peak * (1.0 - max_dd_pct / 100.0)
    return cap_alloc <= floor


async def _disable_if_drawdown_breached(pool, strategy_id: str) -> None:
    """After a close updates the allocation, flatten all open legs then auto-disable
    the strategy if it has breached its high-water drawdown floor."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, enabled, capital_allocation, allocation_peak, max_drawdown_pct, "
                "account_id FROM strategies WHERE id = $1",
                strategy_id,
            )
        if not row or not row["enabled"]:
            return
        strategy = dict(row)
        _cap  = float(strategy["capital_allocation"] or 0)
        _peak = float(strategy["allocation_peak"] or _cap)
        _dd   = float(strategy["max_drawdown_pct"] or 50)
        if _is_drawdown_breached(_cap, _peak, _dd):
            _floor = _peak * (1.0 - _dd / 100.0)
            await _flatten_strategy_positions(pool, strategy)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE strategies SET enabled = false, stop_reason = 'drawdown', updated_at = NOW() WHERE id = $1",
                    strategy_id,
                )
            logger.warning(
                f"DRAWDOWN STOP (on close) strategy={strategy_id}: "
                f"alloc={_cap:.2f} peak={_peak:.2f} floor={_floor:.2f} — auto-disabled + flattened"
            )
    except Exception as _e:
        logger.error(f"drawdown-on-close check failed for {strategy_id}: {_e}")


async def _book_realized_pnl(pool, strategy_id: str, pnl) -> None:
    """Book one realized-PnL event into the strategy's compounding allocation, move the
    high-water peak, and evaluate the drawdown stop. Call EXACTLY ONCE per position close,
    gated by the caller on the pnl_realized NULL->value transition."""
    if pnl is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE strategies
                SET pnl_today          = pnl_today + $1,
                    pnl_total          = pnl_total + $1,
                    capital_allocation = capital_allocation + $1,
                    allocation_peak    = GREATEST(COALESCE(allocation_peak, capital_allocation),
                                                  capital_allocation + $1),
                    updated_at         = NOW()
                WHERE id = $2
                """,
                float(pnl), strategy_id,
            )
        await _disable_if_drawdown_breached(pool, strategy_id)
    except Exception as e:
        logger.warning(f"_book_realized_pnl failed for {strategy_id}: {e}")


async def _flatten_strategy_positions(pool, strategy: dict) -> list[dict]:
    """Close every open leg for the strategy via close_strategy_position (skip_exchange=False).
    Returns a list of per-leg results."""
    async with pool.acquire() as conn:
        legs = await conn.fetch(
            "SELECT symbol, side FROM strategy_positions WHERE strategy_id=$1 AND status='open'",
            strategy['id'],
        )
    results = []
    for leg in legs:
        r = await close_strategy_position(
            pool, strategy, symbol=leg['symbol'], side=leg['side'],
            reason="flatten_on_disable",
        )
        results.append({"symbol": leg['symbol'], "side": leg['side'], **(r or {})})
    return results


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


@router.post("/strategies/{strategy_id}/adjust-stops")
async def adjust_stops_for_strategy(
    strategy_id: str,
    request: Request,
    x_webhook_token: str = Header(None),
):
    """
    Adjust TP/SL stops for the open position of a given strategy.
    Auth: same token as webhook (X-Webhook-Token header or body 'token' field).
    Body: {tp_price?, sl_price?, token?}
    """
    from app.executor_client import call_executor_modify_stops

    pool = get_pool()
    try:
        raw_bytes = await request.body()
        body_dict = json.loads(raw_bytes) if raw_bytes else {}
    except Exception:
        body_dict = {}

    strategy = await _get_strategy(pool, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    token_to_verify = x_webhook_token or body_dict.get("token", "")
    if not token_to_verify or not await _verify_token(token_to_verify, strategy["webhook_secret"]):
        raise HTTPException(status_code=403, detail="Invalid token")

    tp_price_raw = body_dict.get("tp_price")
    sl_price_raw = body_dict.get("sl_price")
    if tp_price_raw is None and sl_price_raw is None:
        raise HTTPException(status_code=400, detail="At least one of tp_price or sl_price is required")

    tp_price = float(tp_price_raw) if tp_price_raw is not None else None
    sl_price = float(sl_price_raw) if sl_price_raw is not None else None
    dry_run  = bool(body_dict.get("dry_run", False))

    # Find open position for this strategy
    async with pool.acquire() as conn:
        pos = await conn.fetchrow(
            """
            SELECT id, symbol, side FROM strategy_positions
            WHERE strategy_id = $1 AND status = 'open'
            ORDER BY opened_at DESC LIMIT 1
            """,
            strategy_id,
        )

    if pos is None:
        raise HTTPException(status_code=404, detail="No open position for strategy")

    if dry_run:
        logger.info(
            f"adjust-stops DRY RUN strategy={strategy_id} pos={pos['id']}"
            f" ({pos['symbol']} {pos['side']}) intended tp={tp_price} sl={sl_price} — no exchange call"
        )
        return {
            "success":           True,
            "simulated":         True,
            "position_id":       str(pos["id"]),
            "intended_tp_price": tp_price,
            "intended_sl_price": sl_price,
        }

    account_id = strategy.get("account_id") or ""
    result = await call_executor_modify_stops(
        account_id=account_id,
        symbol=pos["symbol"],
        side=pos["side"],
        tp_price=tp_price,
        sl_price=sl_price,
    )

    if not result.get("success"):
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": result.get("error_msg", "modify-stops failed")},
        )

    logger.info(
        f"adjust-stops strategy={strategy_id} pos={pos['id']} ({pos['symbol']} {pos['side']})"
        f" tp={tp_price} sl={sl_price}"
        f" cancelled={len(result.get('cancelled', []))} placed={len(result.get('placed', []))}"
    )
    return {"success": True, "position_id": str(pos["id"]), **result}


@router.post("/webhook/{strategy_id}", response_model=OrderResponse)
async def receive_webhook(
    strategy_id: str,
    request: Request,
    x_webhook_token: str = Header(None),
):
    start_ms = time.monotonic() * 1000
    pool = get_pool()
    source_ip = _client_ip(request)

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

    # Guard: Max leverage
    max_lev = int(strategy.get("max_leverage", 10) or 10)
    if effective_leverage > max_lev:
        detail = (
            f"Leverage {effective_leverage}x exceeds strategy maximum of "
            f"{max_lev}x for strategy {strategy_id}."
        )
        logger.warning(f"Strategy {strategy_id} leverage {effective_leverage}x exceeds max {max_lev}x")
        await _finalize_signal_log(pool, signal_log_id, 422, "guard_rejected", detail, start_ms)
        raise HTTPException(status_code=422, detail=detail)

    # Reference price resolution — shared by clamp and guaranteed-SL below.
    # For opening signals without a webhook price, fetch the exchange mark price.
    # Backstop: reject if still no price — never place an unsized open.
    _ref_price = float(payload.indicator_price or payload.price or 0)
    if payload.signal in ("open_long", "open_short") and _ref_price <= 0:
        _acct_for_price = strategy.get("account_id") or ""
        _mp = await get_mark_price(_acct_for_price, resolved.execution_symbol)
        _ref_price = float(_mp) if _mp else 0.0
        if _ref_price > 0:
            logger.info(
                f"strategy={strategy_id}: no webhook price; using exchange mark "
                f"price {_ref_price} for {resolved.execution_symbol}"
            )
        else:
            _detail = (
                f"Cannot size open for strategy {strategy_id}: no webhook price and "
                f"exchange mark price unavailable for {resolved.execution_symbol}. "
                f"Order rejected to prevent an unsized entry."
            )
            logger.error(_detail)
            await _finalize_signal_log(pool, signal_log_id, 422, "no_reference_price", _detail, start_ms)
            raise HTTPException(status_code=422, detail=_detail)

    # Guard: Margin-per-trade clamp (opening signals only)
    # Scale down TV-supplied size so notional never exceeds margin × leverage.
    if payload.signal in ("open_long", "open_short"):
        _margin_per_trade = float(strategy.get("margin_per_trade") or 5.0)
        if _ref_price > 0:
            _margin_qty = round((_margin_per_trade * effective_leverage) / _ref_price, 8)
            if float(payload.size) > _margin_qty:
                _original_size = float(payload.size)
                payload.size   = Decimal(str(_margin_qty))
                _meta = dict(payload.signal_metadata or {})
                _meta["size_scaled_to_margin"] = True
                _meta["original_size"]         = _original_size
                _meta["used_size"]             = _margin_qty
                _meta["ref_price_source"]      = (
                    "webhook" if (payload.indicator_price or payload.price) else "exchange_mark"
                )
                payload.signal_metadata        = _meta
                logger.info(
                    f"Strategy {strategy_id} margin clamp: "
                    f"{_original_size} → {_margin_qty} "
                    f"(margin={_margin_per_trade}, lev={effective_leverage}, price={_ref_price})"
                )

    # ── End Risk Guards ───────────────────────────────────────────────

    # ── Guaranteed SL injection (opening signals, not price-stripped) ─────────
    if payload.signal in ("open_long", "open_short") and not resolved.price_stripped:
        _entry_ref = _ref_price
        if _entry_ref <= 0:
            logger.warning(
                f"strategy={strategy_id}: no reference price for guaranteed SL "
                f"— proceeding with payload sl_price={sl_price}"
            )
        else:
            _open_side   = "long" if payload.signal == "open_long" else "short"
            _strategy_sl = float(sl_price) if sl_price is not None else None
            _sl_final, _sl_source = compute_guaranteed_sl(
                _entry_ref, effective_leverage, _open_side, _strategy_sl
            )
            _sl_dist_pct     = abs(_sl_final - _entry_ref) / _entry_ref * 100
            sl_price         = Decimal(str(_sl_final))
            payload.sl_price = sl_price
            _meta = dict(payload.signal_metadata or {})
            _meta["sl_source"]       = _sl_source
            _meta["sl_distance_pct"] = round(_sl_dist_pct, 4)
            _meta["entry_ref"]       = _entry_ref
            payload.signal_metadata  = _meta
            logger.info(
                f"Guaranteed SL: strategy={strategy_id} side={_open_side} "
                f"entry={_entry_ref} sl={_sl_final} source={_sl_source} "
                f"distance={_sl_dist_pct:.3f}%"
            )

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


async def _create_strategy_position(pool, payload: WebhookPayload, strategy: dict, opening_order_id: uuid.UUID, result: OrderResult, effective_leverage: int, effective_margin_mode: str = "isolated", fill_size=None) -> None:
    """Create a new strategy position record in the database."""
    async with pool.acquire() as conn:
        entry_price = result.actual_fill_price or payload.price or payload.indicator_price or 0
        # Use exchange-confirmed fill size when available (avoids lot-rounding drift).
        db_size = fill_size if fill_size is not None else payload.size

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
                strategy_id, exchange, symbol, pair_id, side, entry_price, size,
                leverage, margin_mode, opening_order_id, status, opened_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, 'open', NOW()
            )
            """,
            strategy['id'],
            strategy.get('exchange', 'auto'),
            f"{payload.base_asset}-{payload.quote_asset}",
            pair_id,
            pos_side,
            entry_price,
            db_size,
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
        async with conn.transaction():
            if is_full:
                updated = await conn.fetchrow(
                    """
                    UPDATE strategy_positions
                    SET status           = 'closed',
                        closing_price    = $1,
                        pnl_realized     = CASE WHEN $2::numeric IS NOT NULL THEN $2::numeric
                                                ELSE pnl_realized END,
                        closing_order_id = $3,
                        close_reason     = $4,
                        closed_at        = NOW(),
                        updated_at       = NOW()
                    WHERE id = $5 AND status = 'open'
                    RETURNING id
                    """,
                    fill_price_f,
                    realized_pnl_f,
                    closing_order_id,
                    reason,
                    pos['id'],
                )
            else:
                updated = await conn.fetchrow(
                    """
                    UPDATE strategy_positions
                    SET size       = size - $1,
                        updated_at = NOW()
                    WHERE id = $2 AND status = 'open'
                    RETURNING id
                    """,
                    eff_close_size,
                    pos['id'],
                )

            if updated is not None and closing_order_id is not None:
                await conn.execute(
                    "UPDATE orders SET closes_position_id = $1 WHERE id = $2",
                    pos['id'],
                    closing_order_id,
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

    if is_full and realized_pnl_f is not None:
        await _book_realized_pnl(pool, strategy['id'], realized_pnl_f)

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


async def sync_position_pnl(pool) -> None:
    """Propagate realized PnL from closing orders to positions, and book the strategy's
    allocation on the pnl_realized NULL->value transition (the external-close booking point).
    Idempotent: a row is booked once, when it first goes NULL -> value."""
    async with pool.acquire() as conn:
        # (1) First attribution (NULL -> value): set AND book.
        newly = await conn.fetch(
            """
            UPDATE strategy_positions sp
            SET pnl_realized = sub.total,
                updated_at   = NOW()
            FROM (
                SELECT closes_position_id AS pid, COALESCE(SUM(pnl), 0) AS total
                FROM orders
                WHERE closes_position_id IS NOT NULL AND pnl IS NOT NULL
                GROUP BY closes_position_id
            ) sub
            WHERE sp.id = sub.pid
              AND sp.pnl_realized IS NULL
            RETURNING sp.id, sp.strategy_id, sp.pnl_realized
            """
        )
        # (2) Corrections (already-booked, value changed): update only, do NOT re-book.
        await conn.execute(
            """
            UPDATE strategy_positions sp
            SET pnl_realized = sub.total,
                updated_at   = NOW()
            FROM (
                SELECT closes_position_id AS pid, COALESCE(SUM(pnl), 0) AS total
                FROM orders
                WHERE closes_position_id IS NOT NULL AND pnl IS NOT NULL
                GROUP BY closes_position_id
            ) sub
            WHERE sp.id = sub.pid
              AND sp.pnl_realized IS NOT NULL
              AND sp.pnl_realized IS DISTINCT FROM sub.total
            """
        )
    for r in newly:
        await _book_realized_pnl(pool, str(r['strategy_id']), r['pnl_realized'])


async def _process_order(
    pool, order_id: uuid.UUID, payload: WebhookPayload, strategy: dict, resolved,
    price, tp_price, sl_price, effective_leverage: int, effective_margin_mode: str = "isolated",
    signal_log_id: Optional[int] = None, start_ms: float = 0.0,
    account_id: str = "", account_label: str = "", strategy_id: str = "",
):
    try:
        # Record last signal timestamp (best-effort, non-blocking)
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE strategies SET last_signal_at = NOW() WHERE id = $1",
                    strategy['id'],
                )
        except Exception as e:
            logger.warning(f"Failed to update last_signal_at for {strategy['id']}: {e}")

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

            return
        # ── End flat signal handler ───────────────────────────────────────

        # ── Resolve account_id from strategy record ──────────────────────────
        account_id = strategy.get("account_id") or "acc_blofin_demo_default"

        pos_symbol = resolved.execution_symbol

        # ── Close signals: route through the safe reduce-only close path ──────
        # close_strategy_position(skip_exchange=False) calls the adapter's
        # close_position() which uses a dedicated close endpoint (full) or
        # reduce-only order (partial) — never an uncapped market order.
        if payload.signal in ("close_long", "close_short"):
            pos_side = "long" if payload.signal == "close_long" else "short"
            close_result = await close_strategy_position(
                pool, strategy,
                symbol=resolved.execution_symbol,
                side=pos_side,
                close_size=payload.size,      # clamped to open size inside routine
                closing_order_id=order_id,
                skip_exchange=False,
            )

            order_result = None
            if close_result.get("success"):
                fp = close_result.get("actual_fill_price")
                rp = close_result.get("realized_pnl")
                order_result = OrderResult(
                    success=True,
                    status="filled",
                    exchange_order_id=close_result.get("exchange_order_id"),
                    actual_fill_price=Decimal(str(fp)) if fp is not None else None,
                    realized_pnl=Decimal(str(rp)) if rp is not None else None,
                )

            await _update_order_status(
                pool, order_id,
                close_result.get("status", "route_failed"),
                payload, order_result, account_id, account_label, strategy_id,
            )
            await _finalize_signal_log(
                pool, signal_log_id, 200,
                "filled" if close_result.get("success") else "route_failed",
                close_result.get("error_msg"), start_ms,
            )
            return
        # ── End close handler ────────────────────────────────────────────────

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

        logger.info(f"Order {order_id} processed for strategy {strategy['id']}: {final_status}")

        if result.success and payload.base_asset and payload.quote_asset and payload.size:

            # ── Open path: top-up existing or create new position ─────────────
            # (Close signals returned early above via the unified close handler.)
            if payload.signal in ("open_long", "open_short"):
                pos_side = "long" if payload.signal == "open_long" else "short"
                fill_price_val = (
                    result.actual_fill_price
                    or payload.price
                    or payload.indicator_price
                    or Decimal("0")
                )
                # Use exchange-confirmed fill size if the adapter returned it.
                # BloFin lot-rounding means _to_contracts(payload.size) != payload.size;
                # using the rounded-back base coins keeps DB and exchange in sync.
                fill_size = result.actual_fill_size if result.actual_fill_size else payload.size

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
                        new_size  = old_size + fill_size
                        new_entry = (
                            (old_entry * old_size + Decimal(str(fill_price_val)) * fill_size)
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
                            fill_size=fill_size,
                        )

                # ── Flip: if exchange netted an opposite position, close its DB leg ──
                flip_pnl = exec_result.get("pnl") or exec_result.get("realized_pnl")
                if flip_pnl is not None and float(flip_pnl) != 0:
                    opposite_side = "short" if pos_side == "long" else "long"
                    try:
                        async with pool.acquire() as conn:
                            opp_leg = await conn.fetchrow(
                                """SELECT id FROM strategy_positions
                                   WHERE strategy_id=$1 AND symbol=$2 AND side=$3
                                     AND status='open'""",
                                strategy['id'], pos_symbol, opposite_side,
                            )
                        if opp_leg:
                            await close_strategy_position(
                                pool, strategy,
                                symbol=pos_symbol,
                                side=opposite_side,
                                skip_exchange=True,
                                realized_pnl=flip_pnl,
                                fill_price=result.actual_fill_price,
                                reason="flip_close",
                            )
                        else:
                            logger.warning(
                                f"Flip PnL={flip_pnl} from executor but no opposite "
                                f"{opposite_side} leg for strategy {strategy['id']} "
                                f"{pos_symbol} — booking directly"
                            )
                            await _book_realized_pnl(pool, strategy['id'], flip_pnl)
                    except Exception as _fe:
                        logger.warning(
                            f"Flip PnL handling failed for strategy {strategy['id']}: {_fe}"
                        )

    except Exception as e:
        logger.exception(f"Error processing order {order_id}: {e}")
        await _update_order_status(pool, order_id, "route_failed", payload, None, account_id, account_label, strategy_id)
        await _finalize_signal_log(pool, signal_log_id, 200, "route_failed", str(e), start_ms)
