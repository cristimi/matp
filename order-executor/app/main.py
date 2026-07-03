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

from app.database import init_db
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
    logger.info("Order Executor ready — AccountRegistry active")
    yield
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
    registry.invalidate(account_id)
    return {"invalidated": account_id}


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
async def get_position_history(account_id: str, symbol: str, since: int | None = None):
    """Return the most recent closed position details for a symbol (for stale-position recovery).
    `since` (epoch ms) scopes the exchange lookup to a single position's lifetime so PnL is not
    summed across the coin's entire history."""
    try:
        adapter = await registry.get(account_id)
        details = await adapter.get_closed_position_details(symbol, since_ms=since)
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


_MODIFY_STOPS_VERIFY_ATTEMPTS   = 3
_MODIFY_STOPS_VERIFY_DELAY_S    = 1.5
_MODIFY_STOPS_PRICE_TOLERANCE   = 0.001  # 0.1% — accommodates exchange tick rounding


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

    `success` is True only if every requested leg (SL, and TP if requested) is
    CONFIRMED resting on the exchange after a verify-read-back+retry loop — never
    trust the adapter's own place call alone (an exchange can accept the signed
    action while rejecting an individual leg). Because cancel-then-place is not
    atomic, a caller must inspect `sl_ok`/`tp_ok` (not just `success`) to know
    whether the position may currently be unprotected.

    Returns: {success, cancelled, placed, sl_ok, tp_ok, sl_oid, tp_oid, attempts, error_msg}
    """
    try:
        adapter = await registry.get(account_id)

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
        existing = await adapter.list_trigger_orders(request.symbol)
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
        # legs not yet confirmed landed (so a retry never re-requests an already-
        # landed leg, which would otherwise stack a duplicate trigger order).
        trigger_side = "sell" if request.side == "long" else "buy"
        remaining_tp = request.tp_price
        remaining_sl = request.sl_price
        all_placed: list = []
        sl_oid = tp_oid = None
        attempts = 0

        while (remaining_tp is not None or remaining_sl is not None) and attempts < _MODIFY_STOPS_VERIFY_ATTEMPTS:
            attempts += 1
            place_result = await adapter.place_trigger_orders(
                symbol       = request.symbol,
                trigger_side = trigger_side,
                size         = position_size,
                tp_price     = remaining_tp,
                sl_price     = remaining_sl,
            )
            all_placed.extend(place_result.get("placed", []))

            verify = await adapter.list_trigger_orders(request.symbol)
            if verify is None:
                logger.warning(
                    f"modify-stops {account_id}/{request.symbol}: verify read-back UNKNOWN "
                    f"on attempt {attempts}/{_MODIFY_STOPS_VERIFY_ATTEMPTS} — retrying"
                )
            else:
                if remaining_sl is not None:
                    sl_leg = _find_landed_leg(verify, "sl", remaining_sl)
                    if sl_leg:
                        sl_oid = sl_leg.get("oid")
                        remaining_sl = None
                if remaining_tp is not None:
                    tp_leg = _find_landed_leg(verify, "tp", remaining_tp)
                    if tp_leg:
                        tp_oid = tp_leg.get("oid")
                        remaining_tp = None

            if (remaining_tp is not None or remaining_sl is not None) and attempts < _MODIFY_STOPS_VERIFY_ATTEMPTS:
                logger.warning(
                    f"modify-stops {account_id}/{request.symbol}: attempt {attempts}/"
                    f"{_MODIFY_STOPS_VERIFY_ATTEMPTS} did not land every requested leg "
                    f"(sl_pending={remaining_sl is not None}, tp_pending={remaining_tp is not None}) — retrying"
                )
                await asyncio.sleep(_MODIFY_STOPS_VERIFY_DELAY_S)

        sl_ok = None if request.sl_price is None else (remaining_sl is None)
        tp_ok = None if request.tp_price is None else (remaining_tp is None)
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
            "error_msg": error_msg,
        }

    except Exception as e:
        logger.error(f"modify_stops failed for {account_id}/{request.symbol}: {e}")
        return {"success": False, "error_msg": str(e)}


@app.get("/accounts/{account_id}/trigger-orders/{symbol}")
async def get_trigger_orders(account_id: str, symbol: str):
    """Return open TP/SL trigger orders for a symbol.

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
        orders = await adapter.list_trigger_orders(symbol)
        return orders
    except Exception as e:
        logger.error(f"get_trigger_orders failed for {account_id}/{symbol}: {e}")
        return None


@app.get("/health")
async def health():
    return {"status": "ok", "service": "order-executor", "version": "1.0.0"}
