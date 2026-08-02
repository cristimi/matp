"""
Order Executor Service — fully wired from Session 4 onward.
Receives OrderRequest from order-listener.
Routes to correct exchange adapter via AccountRegistry.
Returns OrderResult.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db, get_pool
from app.models import OrderRequest
from app.registry import registry
from app.adapters.base import ExchangeUnavailableError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from app.spread_trade import watcher_loop
    spread_watcher_task = asyncio.create_task(watcher_loop(), name="spread_watcher")
    app.state.spread_watcher_task = spread_watcher_task
    logger.info("Order Executor ready — AccountRegistry active")
    yield
    spread_watcher_task.cancel()
    await registry.close_all()
    logger.info("Order Executor shutting down")


app = FastAPI(
    title="MATP Order Executor",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/execute")
async def execute_order(request: OrderRequest):
    from app.executor import execute
    result = await execute(request)
    return result


@app.post("/accounts/{account_id}/invalidate")
async def invalidate_account(account_id: str):
    await registry.invalidate(account_id)
    return {"invalidated": account_id}


# ── Cross-venue spread trade (docs/design/SPREAD_HARVEST.md phases 2-3) ───────

@app.post("/spread/execute")
async def spread_execute(body: dict):
    """Turn an armed spread plan into a live two-leg position (operator confirm)."""
    from app.spread_trade import execute_plan
    plan_id = body.get("plan_id")
    if not plan_id:
        raise HTTPException(status_code=400, detail="plan_id required")
    try:
        return await execute_plan(plan_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/spread/close")
async def spread_close(body: dict):
    """Close both legs of an open spread position (reason: cooled|abort|manual)."""
    from app.spread_trade import close_spread
    try:
        return await close_spread(position_id=body.get("position_id"),
                                  coin=body.get("coin"),
                                  reason=body.get("reason", "manual"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/spread/positions")
async def spread_positions(limit: int = 20):
    from app.spread_trade import list_positions
    return {"positions": await list_positions(limit)}


import base64
from decimal import Decimal as _Decimal
from typing import Optional as _Optional
from pydantic import BaseModel as PydanticBaseModel

class EncryptRequest(PydanticBaseModel):
    credentials_json: str   # raw JSON string to encrypt

class EncryptResponse(PydanticBaseModel):
    encrypted_b64: str      # base64-encoded encrypted bytes for storage


class ClosePositionRequest(PydanticBaseModel):
    account_id: str
    symbol:     str
    side:       str
    size:       _Optional[_Decimal] = None


@app.post("/close-position")
async def close_position_endpoint(request: ClosePositionRequest):
    """Close an open position on the exchange for the given account."""
    try:
        adapter = await registry.get(request.account_id)
        result  = await adapter.close_position(request.symbol, request.side, size=request.size)
        return result
    except Exception as e:
        logger.error(f"close_position failed: {e}")
        return {
            "success":   False,
            "status":    "route_failed",
            "error_msg": str(e),
        }


class ValidateRequest(PydanticBaseModel):
    exchange:         str
    mode:             str
    credentials_json: str


@app.post("/credentials/validate")
async def validate_credentials(request: ValidateRequest):
    """
    Validate exchange credentials without storing them.
    - Hyperliquid: derives wallet from private_key, checks it matches api_wallet if provided.
    - Blofin: makes a live get_balance() call to verify auth.
    Returns {valid, error?, detail?} where detail is exchange-specific info (wallet, balance).
    """
    import json as _json
    try:
        creds = _json.loads(request.credentials_json)
    except Exception:
        return {"valid": False, "error": "Invalid JSON in credentials"}

    if request.exchange == "hyperliquid":
        private_key    = creds.get("private_key", "").strip()
        expected_wallet = creds.get("api_wallet", "").strip()
        if not private_key:
            return {"valid": False, "error": "private_key is required"}
        try:
            from eth_account import Account as EthAccount
            derived = EthAccount.from_key(private_key).address
            if expected_wallet and derived.lower() != expected_wallet.lower():
                return {
                    "valid": False,
                    "error": (
                        f"Private key derives {derived[:10]}…{derived[-6:]}, "
                        f"but API Wallet Address is {expected_wallet[:10]}…{expected_wallet[-6:]}"
                    ),
                }
            return {"valid": True, "detail": f"Wallet verified: {derived}", "wallet": derived}
        except Exception as e:
            return {"valid": False, "error": f"Invalid private key: {e}"}

    elif request.exchange == "blofin":
        try:
            from app.adapters.blofin import BlofinAdapter
            adapter = BlofinAdapter(creds, request.mode)
            balance = await adapter.get_balance()
            if "error" in balance:
                return {"valid": False, "error": f"Blofin auth failed: {balance['error']}"}
            total = balance.get("total_balance", 0)
            ccy   = balance.get("currency", "USDT")
            return {"valid": True, "detail": f"Connected — balance: {total:.2f} {ccy}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    elif request.exchange == "binance":
        try:
            from app.adapters.binance import BinanceAdapter
            adapter = BinanceAdapter(creds, request.mode)
            try:
                balance = await adapter.get_balance()
                if "error" in balance:
                    return {"valid": False, "error": f"Binance auth failed: {balance['error']}"}
                # Hedge mode would let an "entry" open a second opposite leg and would
                # reject every reduce-only close, so refuse the account at the point
                # it is added rather than at the first trade.
                hedge = await adapter._check_position_mode()
                if hedge:
                    return {"valid": False, "error": f"Binance {hedge}"}
                total = balance.get("total_balance", 0)
                ccy   = balance.get("currency", "USDT")
                return {"valid": True, "detail": f"Connected — balance: {total:.2f} {ccy}"}
            finally:
                await adapter.close()
        except Exception as e:
            return {"valid": False, "error": str(e)}

    return {"valid": False, "error": f"Unsupported exchange for validation: {request.exchange}"}


@app.post("/credentials/encrypt", response_model=EncryptResponse)
async def encrypt_credentials(request: EncryptRequest):
    """
    Encrypt a credentials JSON string using MASTER_KEY.
    Returns base64-encoded ciphertext for storage in exchange_accounts.credentials.

    This endpoint is internal-only (no Nginx route).
    The MASTER_KEY never leaves the executor container.
    """
    from app.credentials import encrypt
    try:
        ciphertext = encrypt(request.credentials_json)
        return EncryptResponse(encrypted_b64=base64.b64encode(ciphertext).decode())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/accounts/{account_id}/positions/history")
async def get_position_history(
    account_id: str, symbol: str, since: int | None = None, side: str | None = None
):
    """Return the most recent closed position details for a symbol (for stale-position recovery).
    `since` (epoch ms) scopes the exchange lookup to a single position's lifetime so PnL is not
    summed across the coin's entire history. `side` (long|short) picks the leg on a hedge
    account, where the same symbol closes two positions with two different PnLs."""
    try:
        adapter = await registry.get(account_id)
        details = await adapter.get_closed_position_details(
            symbol, since_ms=since,
            side=side if side in ("long", "short") else None,
        )
        return details or {}
    except Exception as e:
        logger.error(f"get_position_history failed for {account_id}/{symbol}: {e}")
        return {}


@app.get("/accounts/{account_id}/positions")
async def get_positions(account_id: str):
    """Return open positions for a specific account.

    Three-state contract:
      - 200 + list (possibly empty): a CONFIRMED read. [] means the exchange confirmed
        no open positions.
      - 503: UNKNOWN — could not get a confirmed answer (network/API error). Callers must
        NOT treat this as 'no positions'.
    Never return [] to mask an error — that previously let the reconciler close live
    positions during a transient outage.
    """
    try:
        adapter = await registry.get(account_id)
        positions = await adapter.get_open_positions()
        return positions
    except ExchangeUnavailableError as e:
        logger.warning(f"get_positions UNKNOWN for {account_id}: {e}")
        raise HTTPException(status_code=503, detail=f"exchange positions unavailable: {e}")
    except Exception as e:
        logger.error(f"get_positions failed for {account_id}: {e}")
        raise HTTPException(status_code=503, detail=f"exchange positions unavailable: {e}")


@app.get("/accounts/{account_id}/orders")
async def get_open_orders(account_id: str, symbol: str | None = None):
    """Return resting, non-trigger limit orders for an account (optionally filtered by symbol)."""
    try:
        adapter = await registry.get(account_id)
        orders = await adapter.get_open_orders(symbol)
        return orders
    except Exception as e:
        logger.error(f"get_open_orders failed for {account_id}: {e}")
        return {"success": False, "error_msg": str(e), "orders": []}


class CancelOrderRequest(PydanticBaseModel):
    symbol:   str
    order_id: str


@app.post("/accounts/{account_id}/orders/cancel")
async def cancel_order_endpoint(account_id: str, request: CancelOrderRequest):
    """Cancel a resting limit order by id."""
    try:
        adapter = await registry.get(account_id)
        result = await adapter.cancel_order(request.symbol, request.order_id)
        return result
    except Exception as e:
        logger.error(f"cancel_order failed for {account_id}: {e}")
        return {"success": False, "error": str(e)}


class AmendOrderRequest(PydanticBaseModel):
    symbol:    str
    order_id:  str
    new_price: _Optional[float] = None
    new_size:  _Optional[float] = None


@app.post("/accounts/{account_id}/orders/amend")
async def amend_order_endpoint(account_id: str, request: AmendOrderRequest):
    """Amend a resting limit order's price and/or size."""
    try:
        adapter = await registry.get(account_id)
        result = await adapter.amend_order(
            request.symbol, request.order_id, request.new_price, request.new_size
        )
        return result
    except Exception as e:
        logger.error(f"amend_order failed for {account_id}: {e}")
        return {"success": False, "error": str(e)}


@app.get("/accounts/{account_id}/orders/{order_id}/fee")
async def get_order_fill_fee_endpoint(account_id: str, order_id: str, symbol: str):
    """Fee for an already-filled order id — used by the reconciler for fills it detects
    asynchronously (resting orders picked up post-fill), which never get a synchronous
    fee lookup the way an immediate fill at placement time does."""
    try:
        adapter = await registry.get(account_id)
        fee = await adapter.get_order_fill_fee(symbol, order_id)
        return {"fee": str(fee) if fee is not None else None}
    except Exception as e:
        logger.error(f"get_order_fill_fee failed for {account_id}/{order_id}: {e}")
        return {"fee": None}


@app.get("/accounts/{account_id}/balance")
async def get_account_balance(account_id: str):
    """Return balance for a specific account."""
    try:
        adapter = await registry.get(account_id)
        balance = await adapter.get_balance()
        return balance
    except Exception as e:
        logger.error(f"get_balance failed for {account_id}: {e}")
        return {
            "total_balance": 0.0, "available_balance": 0.0,
            "used_margin": 0.0, "currency": "USDT",
            "error": str(e),
        }


@app.get("/accounts/{account_id}/instrument-specs")
async def get_instrument_specs(account_id: str):
    """Return per-symbol precision specs (tick size / sigfig rule) for an account's exchange."""
    try:
        adapter = await registry.get(account_id)
        specs = await adapter.get_instrument_specs()
        return specs
    except Exception as e:
        logger.error(f"get_instrument_specs failed for {account_id}: {e}")
        return {}


@app.get("/accounts/{account_id}/instruments")
async def get_instruments(account_id: str):
    """Return all tradeable instrument symbols for this account's exchange."""
    try:
        adapter = await registry.get(account_id)
        instruments = await adapter.list_instruments()
        return {"instruments": instruments}
    except Exception as e:
        logger.error(f"get_instruments failed for {account_id}: {e}")
        return {"instruments": [], "error": str(e)}


@app.get("/accounts/{account_id}/min-order-size/{symbol}")
async def get_min_order_size(account_id: str, symbol: str):
    """Return minimum order size in base asset units for the given symbol."""
    try:
        adapter  = await registry.get(account_id)
        min_size = await adapter.get_min_order_size(symbol)
        return {"symbol": symbol, "min_base_size": min_size}
    except Exception as e:
        logger.error(f"get_min_order_size failed for {account_id}/{symbol}: {e}")
        return {"symbol": symbol, "min_base_size": 0.0, "error": str(e)}


@app.get("/accounts/{account_id}/mark-price/{symbol}")
async def get_mark_price(account_id: str, symbol: str):
    """Return the current exchange mark price for the given symbol."""
    try:
        adapter    = await registry.get(account_id)
        mark_price = await adapter.get_mark_price(symbol)
        return {"symbol": symbol, "mark_price": mark_price}
    except Exception as e:
        logger.error(f"get_mark_price failed for {account_id}/{symbol}: {e}")
        return {"symbol": symbol, "mark_price": None, "error": str(e)}


@app.get("/accounts/{account_id}/maintenance-margin/{symbol}")
async def get_maintenance_margin(
    account_id: str, symbol: str, notional: float, margin_mode: str = "isolated"
):
    """Return the real, tier-aware maintenance-margin rate for `symbol` at the given
    position notional (quote currency). Used by order-listener's guaranteed-SL formula
    in place of a flat hardcoded MMR. maintenance_margin_rate is None if the exchange
    adapter couldn't derive one — callers must fall back to a conservative static value,
    never treat None as 0."""
    try:
        adapter = await registry.get(account_id)
        mmr = await adapter.get_maintenance_margin_rate(symbol, notional, margin_mode)
        return {"symbol": symbol, "notional": notional, "maintenance_margin_rate": mmr}
    except Exception as e:
        logger.error(f"get_maintenance_margin failed for {account_id}/{symbol}: {e}")
        return {"symbol": symbol, "notional": notional, "maintenance_margin_rate": None, "error": str(e)}


@app.get("/accounts/{account_id}/meta")
async def get_account_meta(account_id: str):
    """Return safe public metadata for a specific account."""
    try:
        adapter = await registry.get(account_id)
        meta    = await adapter.get_account_meta()
        return meta
    except Exception as e:
        logger.error(f"get_account_meta failed for {account_id}: {e}")
        return {"error": str(e)}


@app.post("/accounts/{account_id}/positions/close")
async def close_position(account_id: str, request: dict):
    """Close a specific position on the exchange."""
    symbol = request.get("symbol")
    side = request.get("side")
    margin_mode = request.get("margin_mode", "isolated")
    if not symbol or not side:
        raise HTTPException(status_code=400, detail="Missing symbol or side")

    try:
        adapter = await registry.get(account_id)
        result = await adapter.close_position(symbol, side, margin_mode=margin_mode)
        return result
    except Exception as e:
        logger.error(f"close_position failed for {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ModifyStopsRequest(PydanticBaseModel):
    symbol:   str
    side:     str                         # position side: "long" | "short"
    tp_price: _Optional[float] = None
    sl_price: _Optional[float] = None
    # An OMITTED leg is PRESERVED at whatever price it is currently resting at, not
    # deleted. Set clear_tp/clear_sl to remove a leg on purpose. See the note on
    # modify_stops() for why this default changed.
    clear_tp: bool = False
    clear_sl: bool = False


_MODIFY_STOPS_VERIFY_ATTEMPTS   = 3
_MODIFY_STOPS_VERIFY_DELAY_S    = 1.5
_MODIFY_STOPS_PRICE_TOLERANCE   = 0.001  # 0.1% — accommodates exchange tick rounding
_MODIFY_STOPS_READ_RETRIES      = 2      # extra list_trigger_orders() retries when a
_MODIFY_STOPS_READ_RETRY_DELAY_S = 1.0   # read-back is UNKNOWN, before treating a leg
                                          # as still-unconfirmed for this attempt (never
                                          # re-place solely because a read was unknown)

# Per-leg placement state in the modify-stops retry loop.
_LEG_PENDING          = "pending"           # needs to be (re)placed
_LEG_AWAITING_CONFIRM = "awaiting_confirm"  # adapter reported it placed (no error);
                                             # not yet confirmed by a successful read-back
_LEG_CONFIRMED        = "confirmed"         # a successful read-back found it resting


def _find_landed_leg(verify: list, tpsl: str, requested_price: float) -> _Optional[dict]:
    """Find a resting trigger in a list_trigger_orders() read-back matching tpsl type
    and (within a small rounding tolerance) the requested price."""
    for t in verify:
        if t.get("tpsl") != tpsl or t.get("triggerPx") is None:
            continue
        try:
            actual = float(t["triggerPx"])
        except (TypeError, ValueError):
            continue
        if requested_price == 0:
            continue
        if abs(actual - requested_price) / abs(requested_price) <= _MODIFY_STOPS_PRICE_TOLERANCE:
            return t
    return None


@app.post("/accounts/{account_id}/positions/modify-stops")
async def modify_stops(account_id: str, request: ModifyStopsRequest):
    """
    Cancel existing TP/SL trigger orders for a position and place new ones.
    Does not touch the position itself — pure stop management.

    ── AN OMITTED LEG IS PRESERVED, NOT DELETED ──────────────────────────────────
    This route cancels EVERY resting trigger before placing, because that is how a
    leg gets resized after a partial close. It used to then place back only the legs
    the caller handed a price for, which meant a request carrying just a stop
    silently destroyed the take-profit.

    That is not hypothetical. sol-ai-6486 held an open short whose TP (72.3783) was
    wiped by an `adjust_stops` the AI issued with a new stop and no target:

        adjust-stops strategy=sol-ai-6486 pos=aeb2bfff (SOL-USDT short)
          tp=None sl=73.5 cancelled=1 placed=1

    leaving a position that could only ever exit via its stop. The AI's
    dispatch_adjust_stops omits tp_price whenever resolved_tp_price is None, so every
    stop-only adjustment did this. See
    .gemini/reports/sol-missing-tp-and-rr-zone-borders.md.

    So: a leg with no price in the request is re-placed at the price it is CURRENTLY
    resting at, read back from the exchange in step 2. Removing a leg is now an
    explicit act — set clear_tp / clear_sl. Every other caller in the codebase
    already passes both legs explicitly (the partial-close resize, the post-fill
    re-anchor, the liquidation-safety guard), so this changes nothing for them.

    `success` is True only if every effective leg (SL, and TP if one is requested or
    preserved) is CONFIRMED resting on the exchange after a verify-read-back+retry
    loop — never trust the adapter's own place call alone (an exchange can accept the
    signed action while rejecting an individual leg). Because cancel-then-place is
    not atomic, a caller must inspect `sl_ok`/`tp_ok` (not just `success`) to know
    whether the position may currently be unprotected.

    Returns: {success, cancelled, placed, sl_ok, tp_ok, sl_oid, tp_oid, attempts,
              preserved, error_msg}
    """
    try:
        adapter = await registry.get(account_id)

        # On a hedge account both legs of this symbol have their own triggers, and
        # step 3 cancels everything step 2 read. Scope every trigger call to the leg
        # being modified or moving the long's stop would delete the short's. On a net
        # account the exchange labels the single position "net", so passing a
        # long/short filter there would match nothing — hence None.
        # Named leg_side, not leg: the retry loop below already binds `leg` to each
        # placed trigger, and reusing that name here silently fed a trigger dict
        # into the verify read as its position filter.
        leg_side = request.side if getattr(adapter, "hedge", False) else None

        # 1. Resolve position size (needed for trigger order sizing)
        positions = await adapter.get_open_positions()
        target = next(
            (p for p in positions
             if p.symbol == request.symbol and p.side == request.side),
            None,
        )
        if not target:
            return {
                "success":   False,
                "error_msg": f"No open {request.side} position for {request.symbol}",
            }
        position_size = float(target.size)

        # 2. List existing trigger orders — a confirmed read is required before we
        # cancel anything. An unknown (None) read means we cannot safely proceed:
        # we'd be cancelling stops we can't actually see, on doubt. Nothing has been
        # touched yet, so failing here leaves the position exactly as it was.
        existing = await adapter.list_trigger_orders(request.symbol, position_side=leg_side)
        if existing is None:
            logger.error(
                f"modify-stops {account_id}/{request.symbol}: could not confirm existing "
                f"trigger orders (read failure) — refusing to proceed"
            )
            return {
                "success":   False,
                "cancelled": [],
                "placed":    [],
                "sl_ok":     None,
                "tp_ok":     None,
                "error_msg": (
                    "Could not confirm existing trigger orders before cancel — refusing "
                    "to proceed blindly. Position stops are UNCHANGED (nothing was cancelled)."
                ),
            }
        logger.info(
            f"modify-stops {account_id}/{request.symbol}: found {len(existing)} trigger orders"
        )

        # 2b. Resolve the EFFECTIVE price for each leg before anything is cancelled.
        # A leg the caller did not price is carried forward at whatever it is resting
        # at now, so cancel-then-place cannot silently drop it. `existing` is a
        # CONFIRMED read (an unknown one returned above), so an absent leg here really
        # is absent rather than unreadable.
        existing_tp = existing_sl = None
        for trig in existing:
            px = trig.get("triggerPx")
            if px is None:
                continue
            try:
                px_f = float(px)
            except (TypeError, ValueError):
                logger.warning(
                    f"modify-stops {account_id}/{request.symbol}: unparseable triggerPx "
                    f"{px!r} on oid={trig.get('oid')} — ignoring for preservation"
                )
                continue
            if trig.get("tpsl") == "tp":
                existing_tp = px_f
            elif trig.get("tpsl") == "sl":
                existing_sl = px_f

        eff_tp = request.tp_price
        if eff_tp is None and not request.clear_tp:
            eff_tp = existing_tp
        eff_sl = request.sl_price
        if eff_sl is None and not request.clear_sl:
            eff_sl = existing_sl

        preserved = []
        if request.tp_price is None and eff_tp is not None:
            preserved.append({"tpsl": "tp", "triggerPx": eff_tp})
        if request.sl_price is None and eff_sl is not None:
            preserved.append({"tpsl": "sl", "triggerPx": eff_sl})
        if preserved:
            logger.info(
                f"modify-stops {account_id}/{request.symbol}: preserving "
                + ", ".join(f"{p['tpsl']}={p['triggerPx']}" for p in preserved)
                + " (not priced by caller, carried forward instead of dropped)"
            )

        # Nothing to place and nothing asked to be cleared — cancelling here would
        # strip the position for no reason. Bail out before touching anything.
        if eff_tp is None and eff_sl is None and not (request.clear_tp or request.clear_sl):
            logger.info(
                f"modify-stops {account_id}/{request.symbol}: no legs requested and none "
                f"resting — nothing to do, leaving the position untouched"
            )
            return {
                "success":   True,
                "cancelled": [],
                "placed":    [],
                "sl_ok":     None,
                "tp_ok":     None,
                "sl_oid":    None,
                "tp_oid":    None,
                "attempts":  0,
                "preserved": [],
                "error_msg": None,
            }

        # 3. Cancel them
        cancelled = []
        for trig in existing:
            oid = trig["oid"]
            cancel_result = await adapter.cancel_order(request.symbol, oid)
            cancelled.append({"oid": oid, "tpsl": trig.get("tpsl"), **cancel_result})
            if cancel_result.get("success"):
                logger.info(f"Cancelled trigger oid={oid} ({trig.get('tpsl')}) for {request.symbol}")
            else:
                logger.warning(f"Cancel failed oid={oid}: {cancel_result.get('error')}")

        # 4. Place new trigger orders, verifying by read-back and retrying only the
        # legs genuinely confirmed ABSENT (so a retry never re-requests a leg the
        # adapter already placed, which would otherwise stack a duplicate trigger
        # order). A leg moves: pending -> (place call) -> awaiting_confirm -> either
        # confirmed (a successful read-back finds it) or back to pending (a
        # SUCCESSFUL read-back shows it genuinely missing). An UNKNOWN read-back
        # (None) never causes that demotion — it only pauses confirmation, so a
        # transient read failure can never trigger a duplicate re-place.
        trigger_side = "sell" if request.side == "long" else "buy"
        sl_state = _LEG_PENDING if eff_sl is not None else None
        tp_state = _LEG_PENDING if eff_tp is not None else None
        all_placed: list = []
        sl_oid = tp_oid = None
        attempts = 0

        while (sl_state == _LEG_PENDING or tp_state == _LEG_PENDING) and attempts < _MODIFY_STOPS_VERIFY_ATTEMPTS:
            attempts += 1
            place_tp = eff_tp if tp_state == _LEG_PENDING else None
            place_sl = eff_sl if sl_state == _LEG_PENDING else None
            place_result = await adapter.place_trigger_orders(
                symbol       = request.symbol,
                trigger_side = trigger_side,
                size         = position_size,
                tp_price     = place_tp,
                sl_price     = place_sl,
                position_side= leg_side,
            )
            placed_this_attempt = place_result.get("placed", [])
            all_placed.extend(placed_this_attempt)

            for leg in placed_this_attempt:
                has_error = "error" in leg
                if leg.get("tpsl") == "sl" and place_sl is not None:
                    sl_state = _LEG_PENDING if has_error else _LEG_AWAITING_CONFIRM
                elif leg.get("tpsl") == "tp" and place_tp is not None:
                    tp_state = _LEG_PENDING if has_error else _LEG_AWAITING_CONFIRM

            # Confirm by read-back — retry the READ itself (not the place) on an
            # UNKNOWN result, so a transient read failure never gets mistaken for a
            # genuinely-missing leg.
            verify = None
            for read_attempt in range(_MODIFY_STOPS_READ_RETRIES + 1):
                verify = await adapter.list_trigger_orders(request.symbol, position_side=leg_side)
                if verify is not None:
                    break
                if read_attempt < _MODIFY_STOPS_READ_RETRIES:
                    logger.warning(
                        f"modify-stops {account_id}/{request.symbol}: verify read-back "
                        f"UNKNOWN on attempt {attempts}/{_MODIFY_STOPS_VERIFY_ATTEMPTS} "
                        f"(read retry {read_attempt + 1}/{_MODIFY_STOPS_READ_RETRIES})"
                    )
                    await asyncio.sleep(_MODIFY_STOPS_READ_RETRY_DELAY_S)

            if verify is None:
                logger.warning(
                    f"modify-stops {account_id}/{request.symbol}: verify read-back still "
                    f"UNKNOWN after {_MODIFY_STOPS_READ_RETRIES} extra read(s) on attempt "
                    f"{attempts}/{_MODIFY_STOPS_VERIFY_ATTEMPTS} — leaving any "
                    f"awaiting-confirm leg as-is (not re-placing on an unknown read)"
                )
            else:
                if sl_state in (_LEG_AWAITING_CONFIRM, _LEG_PENDING) and eff_sl is not None:
                    sl_leg = _find_landed_leg(verify, "sl", eff_sl)
                    if sl_leg:
                        sl_oid = sl_leg.get("oid")
                        sl_state = _LEG_CONFIRMED
                    elif sl_state == _LEG_AWAITING_CONFIRM:
                        # Adapter reported it placed, but a CONFIRMED read-back shows
                        # it genuinely absent — safe to retry placing next attempt.
                        sl_state = _LEG_PENDING
                if tp_state in (_LEG_AWAITING_CONFIRM, _LEG_PENDING) and eff_tp is not None:
                    tp_leg = _find_landed_leg(verify, "tp", eff_tp)
                    if tp_leg:
                        tp_oid = tp_leg.get("oid")
                        tp_state = _LEG_CONFIRMED
                    elif tp_state == _LEG_AWAITING_CONFIRM:
                        tp_state = _LEG_PENDING

            if (sl_state == _LEG_PENDING or tp_state == _LEG_PENDING) and attempts < _MODIFY_STOPS_VERIFY_ATTEMPTS:
                logger.warning(
                    f"modify-stops {account_id}/{request.symbol}: attempt {attempts}/"
                    f"{_MODIFY_STOPS_VERIFY_ATTEMPTS} left a leg genuinely unplaced "
                    f"(sl={sl_state}, tp={tp_state}) — retrying"
                )
                await asyncio.sleep(_MODIFY_STOPS_VERIFY_DELAY_S)

        # Reported against the EFFECTIVE legs: a preserved TP that failed to land is a
        # real failure the caller must hear about — under the old behaviour that leg
        # would simply have vanished and still reported success.
        sl_ok = None if eff_sl is None else (sl_state == _LEG_CONFIRMED)
        tp_ok = None if eff_tp is None else (tp_state == _LEG_CONFIRMED)
        success = (sl_ok is not False) and (tp_ok is not False)

        error_msg = None
        if sl_ok is False:
            error_msg = (
                f"SL leg did NOT land after {attempts} attempt(s) — "
                f"position may be UNPROTECTED. tp_ok={tp_ok}"
            )
            logger.error(f"modify-stops {account_id}/{request.symbol}: {error_msg}")
        elif tp_ok is False:
            error_msg = f"TP leg did not land after {attempts} attempt(s) (SL ok={sl_ok})."
            logger.warning(f"modify-stops {account_id}/{request.symbol}: {error_msg}")

        return {
            "success":   success,
            "cancelled": cancelled,
            "placed":    all_placed,
            "sl_ok":     sl_ok,
            "tp_ok":     tp_ok,
            "sl_oid":    sl_oid,
            "tp_oid":    tp_oid,
            "attempts":  attempts,
            "preserved": preserved,
            "error_msg": error_msg,
        }

    except Exception as e:
        logger.error(f"modify_stops failed for {account_id}/{request.symbol}: {e}")
        return {"success": False, "error_msg": str(e)}


@app.get("/accounts/{account_id}/trigger-orders/{symbol}")
async def get_trigger_orders(account_id: str, symbol: str, side: str | None = None):
    """Return open TP/SL trigger orders for a symbol.

    On a hedge account pass `side` (long|short) — without it the caller gets both
    legs' triggers mixed together and cannot tell whose stop is whose.

    This is the only authoritative source for the CURRENTLY-active SL on a position:
    strategy_positions has no sl_price column, and the /adjust-stops management route
    can change a position's live stop without writing anything back to the DB — so any
    DB-recorded intent (e.g. orders.sl_price from the opening fill) can silently go
    stale. Used by order-listener's after-fill liquidation-safety guard.

    Returns the adapter's result verbatim: a list (possibly empty) is a CONFIRMED
    read; `null` means the read itself failed (exchange/network error) — callers
    must not treat `null` as 'no stops', only as 'unknown, nothing to check this pass'."""
    try:
        adapter = await registry.get(account_id)
        leg_side = side if (side in ("long", "short") and getattr(adapter, "hedge", False)) else None
        orders = await adapter.list_trigger_orders(symbol, position_side=leg_side)
        return orders
    except Exception as e:
        logger.error(f"get_trigger_orders failed for {account_id}/{symbol}: {e}")
        return None


class SetPositionModeRequest(PydanticBaseModel):
    position_mode: str        # "net" | "hedge"


@app.get("/accounts/{account_id}/position-mode")
async def get_position_mode(account_id: str):
    """Report the account's position mode as stored AND as the exchange sees it.

    They can only diverge if a human flipped the mode in the exchange's own app,
    which would make every subsequent order fail (a net account rejects
    positionSide=long, a hedge account rejects positionSide=net). This endpoint is
    how that gets spotted before the next trade rather than during it.

    `live` is null when the exchange could not be read, or when the exchange has no
    concept of position modes — neither case means agreement, hence `agrees: null`.
    """
    try:
        adapter = await registry.get(account_id)
        stored = getattr(adapter, "position_mode", "net")
        live = None
        if hasattr(adapter, "get_position_mode"):
            live = await adapter.get_position_mode()
        return {
            "account_id":    account_id,
            "position_mode": stored,
            "live":          live,
            "agrees":        None if live is None else (live == stored),
        }
    except Exception as e:
        logger.error(f"get_position_mode failed for {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/accounts/{account_id}/position-mode")
async def set_position_mode(account_id: str, request: SetPositionModeRequest):
    """Switch an account between net and hedge, on the exchange first.

    Order matters and is not negotiable: flip it on the exchange, read it back, and
    only persist to the DB once the exchange agrees. Writing the column first would
    leave the executor signing hedge-shaped orders at a net-mode account — every one
    rejected — if the exchange refused the switch.

    The exchange refuses while anything is open, which is the safety property that
    makes this switch survivable at all: a live netted position can never be
    silently reinterpreted as one leg of a hedge pair.
    """
    mode = request.position_mode
    if mode not in ("net", "hedge"):
        raise HTTPException(status_code=400, detail=f"position_mode must be net or hedge, got {mode!r}")

    try:
        adapter = await registry.get(account_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

    if not hasattr(adapter, "set_position_mode"):
        raise HTTPException(
            status_code=400,
            detail=f"{type(adapter).__name__} has no position mode to set — only BloFin does",
        )

    result = await adapter.set_position_mode(mode)
    if not result.get("success"):
        return {"success": False, "position_mode": None, "error": result.get("error")}

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE exchange_accounts SET position_mode = $1, updated_at = NOW() WHERE id = $2",
                mode, account_id,
            )
    except Exception as e:
        # The exchange is already switched; the column is not. Say so loudly rather
        # than reporting success — the next restart would reload the stale mode.
        logger.error(f"set_position_mode: exchange switched to {mode} but DB write failed: {e}")
        return {
            "success": False,
            "position_mode": mode,
            "error": (f"Exchange is now in {mode} mode but the database write failed ({e}). "
                      f"Set exchange_accounts.position_mode='{mode}' by hand before trading."),
        }

    logger.info(f"Account {account_id} position mode set to {mode} (exchange confirmed)")
    return {"success": True, "position_mode": mode, "error": None}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "order-executor", "version": "1.0.0"}
