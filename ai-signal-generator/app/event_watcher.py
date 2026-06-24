import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.scheduler import AdaptiveScheduler

from app.database import resolve_exchange_id

logger = logging.getLogger(__name__)


async def event_watcher_loop(
    strategy_id: str,
    db_pool,
    graph,
    scheduler: 'AdaptiveScheduler',
):
    """Runs every 5 minutes. Checks enabled trigger conditions and fires immediate cycles."""
    while True:
        await asyncio.sleep(5 * 60)
        try:
            await _check_all_triggers(strategy_id, db_pool, scheduler)
        except Exception as exc:
            logger.error("Event watcher error for %s: %s", strategy_id, exc)


async def _check_all_triggers(strategy_id: str, db_pool, scheduler):
    async with db_pool.acquire() as conn:
        config_row = await conn.fetchrow(
            "SELECT * FROM ai_strategy_config WHERE strategy_id = $1",
            strategy_id,
        )
    if not config_row:
        return

    config = dict(config_row)

    trigger_map = [
        ('trigger_news_high',    'news_high_impact', _check_news_high_impact),
        ('trigger_volume_spike', 'volume_spike',     _check_volume_spike),
        ('trigger_funding_spike', 'funding_spike',   _check_funding_spike),
    ]

    for config_key, trigger_reason, check_fn in trigger_map:
        if not config.get(config_key, False):
            continue
        try:
            if await _was_recently_triggered(strategy_id, trigger_reason, db_pool):
                continue
            triggered = await check_fn(strategy_id, db_pool, config)
            if triggered:
                logger.info(
                    "Event trigger fired strategy=%s reason=%s",
                    strategy_id, trigger_reason,
                )
                await scheduler._trigger_cycle(trigger_reason)
                break  # only one trigger per watcher cycle
        except Exception as exc:
            logger.warning(
                "Event check error strategy=%s trigger=%s: %s",
                strategy_id, trigger_reason, exc,
            )


async def _was_recently_triggered(strategy_id: str, trigger_reason: str, db_pool) -> bool:
    """Returns True if this trigger_reason fired for this strategy in the last 60 minutes."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM ai_signal_log
            WHERE strategy_id = $1
              AND trigger_reason = $2
              AND triggered_at >= NOW() - INTERVAL '60 minutes'
            LIMIT 1
            """,
            strategy_id,
            trigger_reason,
        )
    return row is not None


async def _check_news_high_impact(strategy_id: str, db_pool, config: dict) -> bool:
    """Returns True if high-impact news appeared in the last hour."""
    from app.data.news import fetch_news_digest
    try:
        digest = await fetch_news_digest(lookback_hours=1)
        if not digest:
            return False
        return any(item.get('severity') == 'high' for item in digest.get('items', []))
    except Exception as exc:
        logger.warning("_check_news_high_impact error: %s", exc)
        return False


async def _check_volume_spike(strategy_id: str, db_pool, config: dict) -> bool:
    """Returns True if current 1h candle volume exceeds volume_spike_threshold% above 20-candle average."""
    from app.data.ohlcv import fetch_ohlcv
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT symbol, account_id FROM strategies WHERE id = $1",
                strategy_id,
            )
        if not row:
            return False

        raw_symbol = row['symbol']
        try:
            async with db_pool.acquire() as conn:
                exchange_id = await resolve_exchange_id(conn, row['account_id'])
        except ValueError as exc:
            logger.warning("_check_volume_spike: exchange resolution failed strategy=%s: %s", strategy_id, exc)
            return False

        if '-' in raw_symbol:
            base, quote = raw_symbol.split('-', 1)
        elif '/' in raw_symbol:
            base, quote = raw_symbol.split('/', 1)
        else:
            return False
        ccxt_symbol = f"{base}/{quote}"

        # Fetch enough candles for 20-period average + current
        ohlcv = await fetch_ohlcv(exchange_id, ccxt_symbol, '1h', 3)
        if not ohlcv or not ohlcv.get('candles') or len(ohlcv['candles']) < 21:
            return False

        candles        = ohlcv['candles']
        current_volume = candles[-1]['volume']
        # 20-candle average excluding the current (latest) candle
        avg_volume = sum(c['volume'] for c in candles[-21:-1]) / 20
        if avg_volume <= 0:
            return False

        threshold_pct = float(config.get('volume_spike_threshold') or 300.0)
        return current_volume > avg_volume * (1 + threshold_pct / 100)

    except Exception as exc:
        logger.warning("_check_volume_spike error: %s", exc)
        return False


async def _check_funding_spike(strategy_id: str, db_pool, config: dict) -> bool:
    """Returns True if abs(funding_rate) exceeds funding_spike_threshold."""
    from app.data.sentiment import fetch_funding_rate
    if not config.get('trigger_funding_spike', False):
        return False
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT symbol, account_id FROM strategies WHERE id = $1",
                strategy_id,
            )
        if not row:
            return False

        raw_symbol = row['symbol']
        try:
            async with db_pool.acquire() as conn:
                exchange_id = await resolve_exchange_id(conn, row['account_id'])
        except ValueError as exc:
            logger.warning("_check_funding_spike: exchange resolution failed strategy=%s: %s", strategy_id, exc)
            return False

        if '-' in raw_symbol:
            base, quote = raw_symbol.split('-', 1)
        elif '/' in raw_symbol:
            base, quote = raw_symbol.split('/', 1)
        else:
            return False
        ccxt_symbol = f"{base}/{quote}"

        funding = await fetch_funding_rate(exchange_id, ccxt_symbol)
        if not funding:
            return False

        rate      = float(funding.get('rate') or 0)
        threshold = float(config.get('funding_spike_threshold') or 0.05)
        return abs(rate) > threshold

    except Exception as exc:
        logger.warning("_check_funding_spike error: %s", exc)
        return False


TRIGGER_CHECKS = {
    'news_high_impact': _check_news_high_impact,
    'volume_spike':     _check_volume_spike,
    'funding_spike':    _check_funding_spike,
}


def start_event_watcher(strategy_id: str, db_pool, graph, scheduler) -> asyncio.Task:
    """Spawn one event_watcher_loop task for a single strategy."""
    return asyncio.create_task(
        event_watcher_loop(strategy_id, db_pool, graph, scheduler),
        name=f"event_watcher_{strategy_id}",
    )


async def start_all_event_watchers(db_pool, graph, schedulers: dict) -> dict:
    """Starts one event_watcher_loop per enabled AI strategy that has a running scheduler."""
    tasks: dict[str, asyncio.Task] = {}
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.id
                FROM strategies s
                JOIN ai_strategy_config a ON a.strategy_id = s.id
                WHERE s.enabled = true
                  AND COALESCE(s.is_deleted, false) = false
                """,
            )
        for row in rows:
            sid = row['id']
            scheduler = schedulers.get(sid)
            if not scheduler:
                continue
            tasks[sid] = start_event_watcher(sid, db_pool, graph, scheduler)
        logger.info("Started %d event watcher(s)", len(tasks))
    except Exception as exc:
        logger.error("Failed to start event watchers: %s", exc)
    return tasks
