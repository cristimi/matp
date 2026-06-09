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
from pydantic import BaseModel as PydanticBaseModel

class EncryptRequest(PydanticBaseModel):
    credentials_json: str   # raw JSON string to encrypt

class EncryptResponse(PydanticBaseModel):
    encrypted_b64: str      # base64-encoded encrypted bytes for storage


class ClosePositionRequest(PydanticBaseModel):
    account_id: str
    symbol:     str
    side:       str


@app.post("/close-position")
async def close_position_endpoint(request: ClosePositionRequest):
    """Close an open position on the exchange for the given account."""
    try:
        adapter = await registry.get(request.account_id)
        result  = await adapter.close_position(request.symbol, request.side)
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


@app.get("/accounts/{account_id}/positions")
async def get_positions(account_id: str):
    """Return open positions for a specific account."""
    try:
        adapter = await registry.get(account_id)
        positions = await adapter.get_open_positions()
        return positions
    except Exception as e:
        logger.error(f"get_positions failed for {account_id}: {e}")
        return []


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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "order-executor", "version": "1.0.0"}
