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
    
    # 1. Fetch all positions from DB
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
                COALESCE(closed_at, NULL)::text as "closedAt",
                COALESCE(pnl_unrealized, 0)::text as "unrealizedPnl",
                COALESCE(pnl_realized, 0)::text as "realizedPnl",
                status,
                opened_at
            FROM strategy_positions
            ORDER BY opened_at DESC
            """
        )

    # 2. Identify unique platforms with open positions in DB
    open_platforms_in_db = {p['platform'] for p in positions_db if p['status'] == 'open' and p['platform'] != 'auto'}
    
    # 3. Fetch live data from each involved exchange
    live_data_by_platform = {}
    for platform in open_platforms_in_db:
        adapter_class = ADAPTERS.get(platform)
        if adapter_class:
            try:
                adapter = adapter_class()
                live_data_by_platform[platform] = await adapter.get_open_positions()
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to fetch live positions for {platform}: {e}")
                live_data_by_platform[platform] = []

    # 4. Merge and Deduplicate
    result = []
    
    def normalize(sym): 
        if not sym: return ""
        return sym.replace("-", "").replace(".P", "").replace(".p", "").replace("/", "").upper()
    
    # Create maps for each platform: platform -> symbol_norm -> live_data
    live_maps = {
        plat: {normalize(p['symbol']): p for p in live_positions}
        for plat, live_positions in live_data_by_platform.items()
    }
    
    # Track which live positions have been "claimed" by a DB record to prevent doubles
    claimed_live_positions = set() # Set of (platform, normalized_symbol)
    
    for row in positions_db:
        pos = dict(row)
        platform = pos['platform']
        norm_sym = normalize(pos['symbol'])
        
        # If position is marked 'open' in DB
        if pos['status'] == 'open':
            # 1. Try to find matching live position on the same platform
            live_key = (platform, norm_sym)
            live_map = live_maps.get(platform, {})
            
            if norm_sym in live_map and live_key not in claimed_live_positions:
                # This is the most recent DB record matching a live position
                live = live_map[norm_sym]
                pos['entryPx'] = str(live.get('entryPx', pos['entryPx']))
                pos['markPx'] = str(live.get('markPx', pos['markPx']))
                pos['unrealizedPnl'] = str(live.get('unrealizedPnl', pos['unrealizedPnl']))
                pos['realizedPnl'] = str(live.get('realizedPnl', pos['realizedPnl']))
                if 'liquidationPx' in live:
                    pos['liquidationPx'] = str(live['liquidationPx'])
                
                claimed_live_positions.add(live_key)
            else:
                # This DB record says 'open', but:
                # a) It's an older duplicate (newer one already claimed the live position)
                # b) It's truly orphaned (not found on exchange)
                pos['status'] = 'stale'
        
        result.append(pos)
        
    return result

@router.post("/{symbol}/close")
async def close_position(symbol: str, request_data: dict):
    pool = get_pool()
    
    # Prioritize platform from request, fallback to config
    platform = request_data.get("platform")
    if not platform:
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
