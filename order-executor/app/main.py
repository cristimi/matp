"""
Order Executor Service — fully wired from Session 4 onward.
Receives OrderRequest from order-listener.
Routes to correct exchange adapter via AccountRegistry.
Returns OrderResult.
"""
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


@app.post("/accounts/{account_id}/positions/modify-stops")
async def modify_stops(account_id: str, request: ModifyStopsRequest):
    """
    Cancel existing TP/SL trigger orders for a position and place new ones.
    Does not touch the position itself — pure stop management.
    Returns: {success, cancelled, placed}
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

        # 2. List existing trigger orders
        existing = await adapter.list_trigger_orders(request.symbol)
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

        # 4. Place new trigger orders
        trigger_side = "sell" if request.side == "long" else "buy"
        place_result = await adapter.place_trigger_orders(
            symbol       = request.symbol,
            trigger_side = trigger_side,
            size         = position_size,
            tp_price     = request.tp_price,
            sl_price     = request.sl_price,
        )

        return {
            "success":   place_result.get("success", False),
            "cancelled": cancelled,
            "placed":    place_result.get("placed", []),
            "error_msg": place_result.get("error"),
        }

    except Exception as e:
        logger.error(f"modify_stops failed for {account_id}/{request.symbol}: {e}")
        return {"success": False, "error_msg": str(e)}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "order-executor", "version": "1.0.0"}
