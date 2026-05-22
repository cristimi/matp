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
    
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM config WHERE key = 'active_platform'")
        platform = row["value"] if row else "blofin"

    # 1. Fetch live data from exchange
    adapter_class = ADAPTERS.get(platform)
    live_positions = []
    if adapter_class:
        adapter = adapter_class()
        live_positions = await adapter.get_open_positions()

    # 2. Fetch history from DB
    async with pool.acquire() as conn:
        positions_db = await conn.fetch(
            """
            SELECT 
                id::text,
                strategy_id,
                exchange as platform,
                symbol,
                side,
                size::text,
                COALESCE(entry_price, 0)::text as "entryPx",
                COALESCE(current_price, 0)::text as "markPx",
                COALESCE(closing_price, 0)::text as "closePx",
                COALESCE(pnl_unrealized, 0)::text as "unrealizedPnl",
                COALESCE(liquidation_price, 0)::text as "liquidationPx",
                status,
                opened_at
            FROM strategy_positions
            WHERE exchange = $1
            ORDER BY opened_at DESC
            """,
            platform
        )

    # 3. Merge: If live data exists for a symbol, use its prices/P&L, else use DB
    result = []
    live_map = {p['symbol']: p for p in live_positions}
    
    for row in positions_db:
        pos = dict(row)
        if pos['status'] == 'open' and pos['symbol'] in live_map:
            live = live_map[pos['symbol']]
            pos['entryPx'] = live['entryPx']
            pos['markPx'] = live['markPx']
            pos['unrealizedPnl'] = live['unrealizedPnl']
            pos['liquidationPx'] = live['liquidationPx']
        result.append(pos)
        
    return result

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
