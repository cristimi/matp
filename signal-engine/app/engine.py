"""
Signal engine: loads active strategies, warms up from Redis stream history,
then subscribes to closed-bar pub/sub and evaluates each new bar.
Shadow-only: writes to shadow_signals, never POSTs to order-listener.
"""
import asyncio
import logging

import redis.asyncio as aioredis

from app.config import settings
from app.database import get_pool
from app.redis_reader import read_stream_history, subscribe_closed_bars
from app.shadow_store import store_shadow_signal

logger = logging.getLogger(__name__)

_TIMEFRAME_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000,
    "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "8h": 28_800_000, "1d": 86_400_000,
}


async def load_active_strategies(pool) -> list:
    """Return strategy instances for all strategies where local_signal_mode != 'off'."""
    from app.strategies.test_harness import TestHarnessStrategy, STRATEGY_ID

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, symbol, interval, local_signal_mode
            FROM public.strategies
            WHERE local_signal_mode <> 'off'
              AND COALESCE(is_deleted, false) = false
            """
        )

    strategies = []
    for row in rows:
        sid = row["id"]
        if sid == STRATEGY_ID:
            strategies.append((TestHarnessStrategy(), row["local_signal_mode"]))
            logger.info("engine: loaded strategy=%s symbol=%s tf=%s mode=%s",
                        sid, row["symbol"], row["interval"], row["local_signal_mode"])
        else:
            logger.warning("engine: unknown strategy id=%s — no implementation registered, skipping", sid)

    return strategies


async def run_strategy_stream(
    redis_client: aioredis.Redis,
    pool,
    strategy,
    mode: str,
) -> None:
    """Warm up a single strategy from stream history, then subscribe to new bars."""
    exchange = settings.ingestion_exchange
    symbol   = strategy.symbol
    tf       = strategy.timeframe

    # Warm up from stream
    candles = await read_stream_history(
        redis_client, exchange, symbol, tf, count=settings.warmup_candles
    )

    # Replay historical bars through strategy to initialize position state
    if candles:
        for i in range(len(candles)):
            subset = candles[: i + 1]
            sigs = strategy.evaluate(subset)
            for sig in sigs:
                await store_shadow_signal(pool, strategy.strategy_id, strategy.signal_source, sig, mode)
        logger.info(
            "engine: warmup complete strategy=%s bars=%d position=%s",
            strategy.strategy_id, len(candles), strategy._position_side,
        )
    else:
        logger.warning("engine: no warmup data for strategy=%s %s %s", strategy.strategy_id, symbol, tf)

    # Subscribe to new closed bars
    logger.info("engine: entering live subscription strategy=%s %s %s", strategy.strategy_id, symbol, tf)
    async for candle in subscribe_closed_bars(redis_client, exchange, symbol, tf):
        candles.append(candle)
        # Keep buffer bounded to avoid memory growth
        if len(candles) > settings.warmup_candles + 200:
            candles = candles[-(settings.warmup_candles + 200):]

        sigs = strategy.evaluate(candles)
        for sig in sigs:
            logger.info(
                "engine: signal strategy=%s signal=%s bar_time=%d close=%.2f",
                strategy.strategy_id, sig.signal, sig.signal_bar_time, sig.bar_close_price,
            )
            await store_shadow_signal(pool, strategy.strategy_id, strategy.signal_source, sig, mode)


async def run_engine(redis_client: aioredis.Redis, pool) -> None:
    """Load all active strategies and run them concurrently."""
    strategy_pairs = await load_active_strategies(pool)

    if not strategy_pairs:
        logger.info("engine: no active strategies (local_signal_mode='off' for all) — idle")
        # Stay alive; strategies may be activated later via DB update (would need restart for now)
        while True:
            await asyncio.sleep(60)
            return

    logger.info("engine: starting %d strategy stream(s)", len(strategy_pairs))
    tasks = [
        asyncio.create_task(
            run_strategy_stream(redis_client, pool, strat, mode),
            name=f"engine_{strat.strategy_id}",
        )
        for strat, mode in strategy_pairs
    ]
    await asyncio.gather(*tasks)
