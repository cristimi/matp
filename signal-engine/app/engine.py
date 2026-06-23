"""
Signal engine: loads active strategies, warms up from Redis stream history,
then subscribes to closed-bar pub/sub and evaluates each new bar.
Shadow-only: writes to shadow_signals, never POSTs to order-listener.

Exit wiring (Prompt 3b-2):
  - Entry signals come from strategy.evaluate() on each closed 1h bar.
  - active_bracket (BracketState) is created on entry, cleared on exit/flip.
  - Near-tick loop (~1s): feeds forming-1m close as point price to bracket.
  - 1m safety-net: feeds closed 1m bar high/low to catch wicks between polls.
  - RSI condition-modify: on each 1h close, feeds close as point price + RSI
    so the bracket can tighten its stop without ingesting a wide bar.
"""
import asyncio
import logging

import redis.asyncio as aioredis

from app.config import settings
from app.database import get_pool
from app.exits import BracketState
from app.redis_reader import read_stream_history, subscribe_closed_bars, read_forming_candle
from app.shadow_store import store_shadow_signal
from app.strategies.base import Signal

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


async def _store_exit_leg(pool, strategy, mode: str, bh: dict, leg: dict, bar_time_ms: int, price: float) -> None:
    side = bh["side"]
    sig = Signal(
        signal="close_long" if side == "long" else "close_short",
        side=side,
        symbol=strategy.symbol,
        signal_bar_time=bar_time_ms,
        bar_close_price=price,
        exit_reason=leg["exit_reason"],
        size_pct=leg["size_pct"],
    )
    await store_shadow_signal(pool, strategy.strategy_id, strategy.signal_source, sig, mode)


async def _near_tick_loop(redis_client: aioredis.Redis, pool, strategy, mode: str, bh: dict) -> None:
    """Poll forming 1m candle ~every second; feed close as point price to bracket."""
    exchange = settings.ingestion_exchange
    symbol = strategy.symbol
    logger.info("engine: near-tick monitor started strategy=%s", strategy.strategy_id)
    while True:
        try:
            await asyncio.sleep(1)
            bracket: BracketState | None = bh["bracket"]
            if bracket is None or bracket.closed:
                continue
            forming = await read_forming_candle(redis_client, exchange, symbol, "1m")
            if forming is None:
                continue
            p = forming["c"]
            # Point-price update: high=low=price=close (avoids wide-bar corner)
            legs = bracket.update(high=p, low=p, price=p)
            for leg in legs:
                logger.info(
                    "engine: near-tick exit strategy=%s reason=%s size=%.1f price=%.2f",
                    strategy.strategy_id, leg["exit_reason"], leg["size_pct"], p,
                )
                await _store_exit_leg(pool, strategy, mode, bh, leg, forming["t"], p)
            if bracket.closed:
                strategy.mark_flat()
                bh["bracket"] = None
                bh["side"] = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("engine: near-tick error strategy=%s: %s", strategy.strategy_id, exc)


async def _safety_net_loop(redis_client: aioredis.Redis, pool, strategy, mode: str, bh: dict) -> None:
    """Subscribe to closed 1m bars; feed real high/low to catch wicks between near-tick polls."""
    exchange = settings.ingestion_exchange
    symbol = strategy.symbol
    logger.info("engine: 1m safety-net subscribed strategy=%s", strategy.strategy_id)
    try:
        async for bar in subscribe_closed_bars(redis_client, exchange, symbol, "1m"):
            try:
                bracket: BracketState | None = bh["bracket"]
                if bracket is None or bracket.closed:
                    continue
                # Real high/low from the closed 1m bar — catches wicks missed between polls
                legs = bracket.update(high=bar["h"], low=bar["l"])
                for leg in legs:
                    logger.info(
                        "engine: 1m safety-net exit strategy=%s reason=%s size=%.1f",
                        strategy.strategy_id, leg["exit_reason"], leg["size_pct"],
                    )
                    await _store_exit_leg(pool, strategy, mode, bh, leg, bar["t"], bar["c"])
                if bracket.closed:
                    strategy.mark_flat()
                    bh["bracket"] = None
                    bh["side"] = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("engine: 1m safety-net error strategy=%s: %s", strategy.strategy_id, exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("engine: 1m safety-net loop died strategy=%s: %s", strategy.strategy_id, exc)
        raise


async def run_strategy_stream(
    redis_client: aioredis.Redis,
    pool,
    strategy,
    mode: str,
) -> None:
    """Warm up, then run entry-loop + near-tick monitor + 1m safety-net concurrently."""
    exchange = settings.ingestion_exchange
    symbol   = strategy.symbol
    tf       = strategy.timeframe

    # Shared bracket state (engine-side; asyncio single-thread = no lock needed)
    bh: dict = {"bracket": None, "side": None, "entry_bar_time": None}

    # --- Warmup: replay stream history to rebuild position + bracket state ---
    candles = await read_stream_history(
        redis_client, exchange, symbol, tf, count=settings.warmup_candles
    )

    if candles:
        for i in range(len(candles)):
            subset = candles[: i + 1]
            sigs = strategy.evaluate(subset)
            for sig in sigs:
                await store_shadow_signal(pool, strategy.strategy_id, strategy.signal_source, sig, mode)
                # Track bracket on entries/flips — do NOT feed 1h high/low as exit ticks
                if sig.signal in ("open_long", "open_short"):
                    side = "long" if sig.signal == "open_long" else "short"
                    bh["bracket"] = BracketState(sig.bracket_spec, sig.bar_close_price)
                    bh["side"] = side
                    bh["entry_bar_time"] = sig.signal_bar_time
                elif sig.signal in ("close_long", "close_short"):
                    bh["bracket"] = None
                    bh["side"] = None
                    bh["entry_bar_time"] = None
        logger.info(
            "engine: warmup complete strategy=%s bars=%d position=%s bracket=%s",
            strategy.strategy_id, len(candles), strategy._position_side,
            "active" if bh["bracket"] else "none",
        )
    else:
        logger.warning("engine: no warmup data for strategy=%s %s %s", strategy.strategy_id, symbol, tf)

    # --- Catch-up replay: replay 1m bars from entry forward through any open bracket ---
    # Skips RSI condition-modify (no aligned 1h RSI history); un-tightened stop is the
    # conservative choice; condition-modify resumes on the next live 1h close if still open.
    if bh["bracket"] is not None and not bh["bracket"].closed and bh["entry_bar_time"] is not None:
        tf_ms = _TIMEFRAME_MS.get(tf, 3_600_000)
        start_from = bh["entry_bar_time"] + tf_ms  # position open only after the entry bar closes
        m1 = await read_stream_history(redis_client, exchange, symbol, "1m", count=2000)
        replayed = [c for c in m1 if c["t"] >= start_from]
        if replayed and replayed[0]["t"] > start_from:
            logger.warning(
                "engine: catch-up partial — earliest 1m bar t=%d is after entry+1bar t=%d"
                " strategy=%s (bounded data limitation)",
                replayed[0]["t"], start_from, strategy.strategy_id,
            )
        logger.info(
            "engine: catch-up replaying %d 1m bars strategy=%s from t=%d",
            len(replayed), strategy.strategy_id, start_from,
        )
        for bar in replayed:
            if bh["bracket"] is None or bh["bracket"].closed:
                break
            legs = bh["bracket"].update(high=bar["h"], low=bar["l"])
            for leg in legs:
                await _store_exit_leg(pool, strategy, mode, bh, leg, bar["t"], bar["c"])
                logger.info(
                    "engine: catch-up exit strategy=%s reason=%s at bar_t=%d price=%.4f",
                    strategy.strategy_id, leg["exit_reason"], bar["t"], bar["c"],
                )
            if bh["bracket"].closed:
                strategy.mark_flat()
                bh["bracket"] = None
                bh["side"] = None

    logger.info("engine: entering live subscription strategy=%s %s %s", strategy.strategy_id, symbol, tf)

    async def _entry_loop() -> None:
        nonlocal candles
        async for candle in subscribe_closed_bars(redis_client, exchange, symbol, tf):
            candles.append(candle)
            if len(candles) > settings.warmup_candles + 200:
                candles = candles[-(settings.warmup_candles + 200):]

            sigs = strategy.evaluate(candles)
            # last_rsi is now updated on strategy after evaluate()

            # (d) RSI condition-modify on 1h close — point price + RSI, no wide bar
            # Run before processing evaluate's sigs so condition_close clears bracket first.
            bracket = bh["bracket"]
            if bracket is not None and not bracket.closed:
                c = candle["c"]
                rsi_val = strategy.last_rsi
                legs = bracket.update(high=c, low=c, price=c, rsi=rsi_val)
                for leg in legs:
                    logger.info(
                        "engine: 1h RSI-modify exit strategy=%s reason=%s size=%.1f",
                        strategy.strategy_id, leg["exit_reason"], leg["size_pct"],
                    )
                    await _store_exit_leg(pool, strategy, mode, bh, leg, candle["t"], c)
                if bracket.closed:
                    strategy.mark_flat()
                    bh["bracket"] = None
                    bh["side"] = None

            for sig in sigs:
                logger.info(
                    "engine: signal strategy=%s signal=%s bar_time=%d close=%.2f",
                    strategy.strategy_id, sig.signal, sig.signal_bar_time, sig.bar_close_price,
                )
                await store_shadow_signal(pool, strategy.strategy_id, strategy.signal_source, sig, mode)
                if sig.signal in ("open_long", "open_short"):
                    side = "long" if sig.signal == "open_long" else "short"
                    bh["bracket"] = BracketState(sig.bracket_spec, sig.bar_close_price)
                    bh["side"] = side
                    bh["entry_bar_time"] = sig.signal_bar_time
                elif sig.signal in ("close_long", "close_short"):
                    bh["bracket"] = None
                    bh["side"] = None
                    bh["entry_bar_time"] = None
                    strategy.mark_flat()

    tasks = [
        asyncio.create_task(_entry_loop(),    name=f"entry_{strategy.strategy_id}"),
        asyncio.create_task(
            _near_tick_loop(redis_client, pool, strategy, mode, bh),
            name=f"neartick_{strategy.strategy_id}",
        ),
        asyncio.create_task(
            _safety_net_loop(redis_client, pool, strategy, mode, bh),
            name=f"safetynet_{strategy.strategy_id}",
        ),
    ]
    await asyncio.gather(*tasks)


async def run_engine(redis_client: aioredis.Redis, pool) -> None:
    """Load all active strategies and run them concurrently."""
    strategy_pairs = await load_active_strategies(pool)

    if not strategy_pairs:
        logger.info("engine: no active strategies (local_signal_mode='off' for all) — idle")
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


# ---------------------------------------------------------------------------
# probe_exit: one-shot live test — reads forming 1m candle, runs bracket
# calculator, prints result. No DB writes. Run with: python -m app.engine probe_exit
# ---------------------------------------------------------------------------
async def _probe_exit() -> None:
    from app.strategies.test_harness import SYMBOL

    redis_client = aioredis.from_url(
        settings.redis_url, decode_responses=True,
        socket_connect_timeout=5,
    )
    try:
        forming = await read_forming_candle(redis_client, settings.ingestion_exchange, SYMBOL, "1m")
        if forming is None:
            print(f"probe_exit ERROR: no forming 1m candle found in Redis "
                  f"(key: candle:forming:{settings.ingestion_exchange}:{SYMBOL}:1m)")
            return

        p = forming["c"]
        # Entry set so current price is exactly 0.5% above → should hit TP1
        entry = p / 1.005

        spec = {
            "tp1_offset_pct": 0.5,  "tp1_size_pct": 50,
            "tp2_offset_pct": 1.0,  "tp2_size_pct": 50,
            "stop_offset_pct": 0.7,
            "trail_arm_pct": 0.4,   "trail_pct": 0.3,
            "be_offset_pct": 0.1,
            "condition_modify_rsi_long":  75,
            "condition_modify_rsi_short": 25,
            "direction": 1,
        }
        state = BracketState(spec, entry)

        tp1   = entry * 1.005
        stop  = entry * (1 - 0.007)
        trail_arm = entry * 1.004

        print(f"probe_exit: symbol={SYMBOL} forming_t={forming['t']}")
        print(f"probe_exit: live_price={p:.4f}  entry={entry:.4f}")
        print(f"probe_exit: tp1={tp1:.4f}  stop={stop:.4f}  trail_arm={trail_arm:.4f}")

        legs = state.update(high=p, low=p, price=p)
        print(f"probe_exit: legs={legs}")
        print(f"probe_exit: bracket.closed={state.closed}")
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "probe_exit":
        asyncio.run(_probe_exit())
    else:
        print("Usage: python -m app.engine probe_exit")
        sys.exit(1)
