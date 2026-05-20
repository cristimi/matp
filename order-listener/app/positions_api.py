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
    positions = await adapter.get_open_positions()
    
    # Enrich with strategy_id
    enriched = []
    async with pool.acquire() as conn:
        for p in positions:
            # Find the latest filled order for this symbol and platform to identify the strategy
            strategy_id = await conn.fetchval(
                """
                SELECT strategy_id FROM orders 
                WHERE symbol = $1 AND platform = $2 AND status = 'filled'
                ORDER BY received_at DESC LIMIT 1
                """,
                p["symbol"], p["platform"]
            )
            p["strategy_id"] = strategy_id
            enriched.append(p)
    return enriched

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
