"""
APScheduler-based strategy runner.
Loads strategies from YAML configs, polls OHLCV data via CCXT,
and dispatches signals to the Order Listener.
"""

import asyncio
import glob
import logging
import os
from datetime import datetime, timezone
from typing import Dict

import ccxt.async_support as ccxt
import httpx
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.strategies.base import BaseStrategy, Candle
from app.strategies.rsi_strategy import RsiStrategy
from app.strategies.ma_crossover import MaCrossoverStrategy
from app.database import get_pool

logger = logging.getLogger(__name__)

STRATEGY_CLASSES = {
    "RsiStrategy": RsiStrategy,
    "MaCrossoverStrategy": MaCrossoverStrategy,
}

LISTENER_URL = os.getenv("LISTENER_WEBHOOK_URL", "http://order-listener:8001/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DATA_FEED_EXCHANGE = os.getenv("DATA_FEED_EXCHANGE", "binance")
CONFIG_DIR = os.getenv("STRATEGY_CONFIG_DIR", "/app/strategies_config")

INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}


class StrategyScheduler:
    def __init__(self):
        self._scheduler = AsyncIOScheduler()
        self._strategies: Dict[str, BaseStrategy] = {}
        self._exchange = None

    async def start(self):
        self._exchange = getattr(ccxt, DATA_FEED_EXCHANGE)({"enableRateLimit": True})
        self._load_strategies()
        self._schedule_all()
        self._scheduler.start()
        logger.info(f"Scheduler started with {len(self._strategies)} strategies.")

    def shutdown(self):
        self._scheduler.shutdown(wait=False)
        if self._exchange:
            asyncio.create_task(self._exchange.close())

    def _load_strategies(self):
        pattern = os.path.join(CONFIG_DIR, "*.yaml")
        for path in glob.glob(pattern):
            try:
                with open(path) as f:
                    cfg = yaml.safe_load(f)
                cls_name = cfg.get("class")
                cls = STRATEGY_CLASSES.get(cls_name)
                if not cls:
                    logger.warning(f"Unknown strategy class '{cls_name}' in {path}")
                    continue
                strategy = cls(
                    strategy_id=cfg["strategy_id"],
                    name=cfg.get("name", cls_name),
                    symbol=cfg["symbol"],
                    interval=cfg["interval"],
                    platform=cfg.get("platform", "auto"),
                    enabled=cfg.get("enabled", True),
                    params=cfg.get("params", {}),
                )
                self._strategies[strategy.strategy_id] = strategy
                logger.info(f"Loaded strategy: {strategy}")
            except Exception as e:
                logger.error(f"Failed to load strategy from {path}: {e}")

    def _schedule_all(self):
        for strategy in self._strategies.values():
            if not strategy.enabled:
                continue
            interval_secs = INTERVAL_SECONDS.get(strategy.interval, 300)
            self._scheduler.add_job(
                self._run_strategy,
                "interval",
                seconds=interval_secs,
                id=strategy.strategy_id,
                args=[strategy.strategy_id],
                next_run_time=datetime.now(timezone.utc),
            )
            logger.info(
                f"Scheduled {strategy.strategy_id} every {interval_secs}s"
            )

    async def _run_strategy(self, strategy_id: str):
        strategy = self._strategies.get(strategy_id)
        if not strategy or not strategy.enabled:
            return

        try:
            ohlcv = await self._exchange.fetch_ohlcv(
                strategy.symbol, strategy.interval, limit=50
            )
            if not ohlcv:
                return

            last_row = ohlcv[-1]
            candle = Candle(
                timestamp=last_row[0],
                open=last_row[1],
                high=last_row[2],
                low=last_row[3],
                close=last_row[4],
                volume=last_row[5],
            )

            signal = strategy.on_candle(candle)

            if signal:
                strategy.last_signal_time = candle.timestamp
                await self._emit_signal(strategy, signal)

        except Exception as e:
            logger.error(f"Error running strategy {strategy_id}: {e}")

    async def _emit_signal(self, strategy: BaseStrategy, signal):
        # 1. Fetch webhook secret from DB (Need to be able to access DB here)
        # Note: StrategyScheduler might need access to database.
        # For simplicity of this change, assume secret is available from strategy object or env if not yet in DB.
        # The prompt says: "Query the database for strategy.webhook_secret"
        
        # Accessing database in scheduler is a change. Let's assume we add DB pool to Scheduler.
        
        # Assuming we have access to database pool in strategy_scheduler
        pool = get_pool()
        async with pool.acquire() as conn:
            secret = await conn.fetchval(
                "SELECT webhook_secret FROM strategies WHERE id = $1", 
                strategy.strategy_id
            )

        webhook_url = f"{LISTENER_URL.rstrip('/webhook')}/webhook/{strategy.strategy_id}"
        
        payload = {
            "symbol":     strategy.symbol,
            "side":       signal.side,
            "signal":     signal.signal,
            "orderType":  "market",
            "size":       str(signal.size),
            "platform":   strategy.platform,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }
        if signal.tp_price:
            payload["tpPrice"] = str(signal.tp_price)
        if signal.sl_price:
            payload["slPrice"] = str(signal.sl_price)

        headers = {"X-Webhook-Token": secret}
        
        async with httpx.AsyncClient(timeout=10) as client:
            for attempt in range(3):
                try:
                    response = await client.post(webhook_url, json=payload, headers=headers)
                    if response.status_code == 200:
                        logger.info(f"Signal emitted: {strategy.strategy_id} → {signal.signal} {strategy.symbol}")
                        return
                    else:
                        logger.warning(f"Listener rejected signal (attempt {attempt+1}): {response.status_code} {response.text}")
                except Exception as e:
                    logger.warning(f"Failed to emit signal (attempt {attempt+1}): {e}")
                
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt) # 1s, 2s
            
            logger.error(f"Failed to emit signal after 3 attempts: {strategy.strategy_id}")

    # --- Management methods (used by REST API) ---

    def list_strategies(self):
        return [
            {
                "id":               s.strategy_id,
                "name":             s.name,
                "symbol":           s.symbol,
                "interval":         s.interval,
                "platform":         s.platform,
                "enabled":          s.enabled,
                "last_signal_time": s.last_signal_time,
            }
            for s in self._strategies.values()
        ]

    def enable(self, strategy_id: str):
        s = self._strategies.get(strategy_id)
        if not s:
            return False
        s.enabled = True
        if not self._scheduler.get_job(strategy_id):
            interval_secs = INTERVAL_SECONDS.get(s.interval, 300)
            self._scheduler.add_job(
                self._run_strategy, "interval",
                seconds=interval_secs, id=strategy_id, args=[strategy_id],
            )
        return True

    def disable(self, strategy_id: str):
        s = self._strategies.get(strategy_id)
        if not s:
            return False
        s.enabled = False
        job = self._scheduler.get_job(strategy_id)
        if job:
            job.remove()
        return True

    def get_strategy(self, strategy_id: str):
        return self._strategies.get(strategy_id)


strategy_scheduler = StrategyScheduler()
