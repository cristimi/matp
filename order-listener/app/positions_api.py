from fastapi import APIRouter, HTTPException
from app.adapters.blofin import BlofinAdapter
from app.adapters.hyperliquid import HyperliquidAdapter
from app.database import get_pool

router = APIRouter(prefix="/positions")

ADAPTERS = {
    "blofin": BlofinAdapter,
    "hyperliquid": HyperliquidAdapter,
}

@router.get("")
async def list_positions():
    pool = get_pool()
    # Get active platform to decide which adapter to use
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM config WHERE key = 'active_platform'")
        platform = row["value"] if row else "blofin"

    adapter_class = ADAPTERS.get(platform)
    if not adapter_class:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    
    adapter = adapter_class()
    return await adapter.get_open_positions()

@router.post("/{symbol}/close")
async def close_position(symbol: str, request_data: dict):
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM config WHERE key = 'active_platform'")
        platform = row["value"] if row else "blofin"

    adapter_class = ADAPTERS.get(platform)
    if not adapter_class:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    
    adapter = adapter_class()
    side = request_data.get("side", "buy")
    result = await adapter.close_position(symbol, side)
    
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error_msg)
        
    return {"status": "success", "result": result.model_dump(mode="json")}
