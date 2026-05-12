"""
REST API for strategy management.
"""

from fastapi import APIRouter, HTTPException

from app.scheduler import strategy_scheduler

router = APIRouter(prefix="/strategies")


@router.get("")
async def list_strategies():
    return strategy_scheduler.list_strategies()


@router.post("/{strategy_id}/enable")
async def enable_strategy(strategy_id: str):
    ok = strategy_scheduler.enable(strategy_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return {"strategy_id": strategy_id, "enabled": True}


@router.post("/{strategy_id}/disable")
async def disable_strategy(strategy_id: str):
    ok = strategy_scheduler.disable(strategy_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return {"strategy_id": strategy_id, "enabled": False}


@router.get("/{strategy_id}/config")
async def get_strategy_config(strategy_id: str):
    s = strategy_scheduler.get_strategy(strategy_id)
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return {
        "id":       s.strategy_id,
        "name":     s.name,
        "symbol":   s.symbol,
        "interval": s.interval,
        "platform": s.platform,
        "enabled":  s.enabled,
        "params":   s.params,
    }
