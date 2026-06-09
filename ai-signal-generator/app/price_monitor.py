import asyncio
import logging
from datetime import datetime, timezone

import ccxt.async_support as ccxt_async
import httpx

from app.webhook.signer import sign_payload

logger = logging.getLogger(__name__)

_EXCHANGE_MAP = {'blofin': 'blofin', 'hyperliquid': 'hyperliquid'}


async def price_monitor_loop(strategy_id: str, db_pool, listener_url: str):
    """
    Runs continuously. Every 60 seconds checks if the open position
    has exceeded the emergency_exit_pct threshold.
    No LLM involved.
    """
    while True:
        await asyncio.sleep(60)
        try:
            await _check_and_exit_if_needed(strategy_id, db_pool, listener_url)
        except Exception as exc:
            logger.error("Price monitor error for %s: %s", strategy_id, exc)


async def _check_and_exit_if_needed(strategy_id: str, db_pool, listener_url: str):
    # 1. Load open position (with strategy columns needed for price fetch)
    async with db_pool.acquire() as conn:
        position = await conn.fetchrow(
            """
            SELECT sp.*, s.platform, s.symbol AS strat_symbol,
                   s.default_leverage, s.webhook_secret
            FROM strategy_positions sp
            JOIN strategies s ON s.id = sp.strategy_id
            WHERE sp.strategy_id = $1 AND sp.status = 'open'
            ORDER BY sp.opened_at DESC LIMIT 1
            """,
            strategy_id,
        )

    # 2. No open position → nothing to monitor
    if not position:
        return

    pos = dict(position)

    # 3. Load ai_strategy_config for thresholds
    async with db_pool.acquire() as conn:
        config_row = await conn.fetchrow(
            "SELECT * FROM ai_strategy_config WHERE strategy_id = $1",
            strategy_id,
        )
    if not config_row:
        return

    config             = dict(config_row)
    emergency_exit_pct = float(config.get('emergency_exit_pct') or 2.5)
    dry_run            = bool(config.get('dry_run', True))

    # 4. Parse symbol → ccxt format + exchange
    raw_symbol = pos.get('strat_symbol') or pos.get('symbol', 'BTC-USDT')
    if '-' in raw_symbol:
        base, quote = raw_symbol.split('-', 1)
    elif '/' in raw_symbol:
        base, quote = raw_symbol.split('/', 1)
    else:
        base, quote = raw_symbol, 'USDT'
    ccxt_symbol = f"{base}/{quote}"

    raw_platform = pos.get('platform') or 'binance'
    exchange_id  = _EXCHANGE_MAP.get(raw_platform, 'binance')

    # 5. Fetch current price
    current_price = await _fetch_current_price(exchange_id, ccxt_symbol)
    if current_price is None:
        return

    # 6. Calculate unrealized PnL
    entry_price = float(pos.get('entry_price') or 0)
    leverage    = float(pos.get('default_leverage') or 1)
    side        = pos.get('side', 'long')

    if entry_price <= 0:
        return

    if side == 'long':
        unrealized_pct = (current_price - entry_price) / entry_price * 100 * leverage
    else:
        unrealized_pct = (entry_price - current_price) / entry_price * 100 * leverage

    # 7. Fire emergency close if threshold exceeded
    if unrealized_pct < -emergency_exit_pct:
        logger.warning(
            "EMERGENCY EXIT strategy=%s side=%s unrealized=%.2f%% threshold=-%.2f%% dry_run=%s",
            strategy_id, side, unrealized_pct, emergency_exit_pct, dry_run,
        )
        await _fire_emergency_close(
            strategy_id=strategy_id,
            side=side,
            size=float(pos.get('size') or 0.01),
            base=base,
            quote=quote,
            webhook_secret=pos.get('webhook_secret', ''),
            config=config,
            unrealized_pct=unrealized_pct,
            dry_run=dry_run,
            db_pool=db_pool,
            listener_url=listener_url,
        )


async def _fetch_current_price(exchange_id: str, symbol: str) -> float | None:
    exchange = None
    try:
        cls = getattr(ccxt_async, exchange_id, None)
        exchange = cls({'enableRateLimit': True}) if cls else ccxt_async.binance({'enableRateLimit': True})
        ticker = await exchange.fetch_ticker(symbol)
        price = float(ticker.get('last') or ticker.get('close') or 0)
        return price if price > 0 else None
    except Exception as exc:
        logger.warning("fetch_current_price error [%s %s]: %s", exchange_id, symbol, exc)
        return None
    finally:
        if exchange:
            try:
                await exchange.close()
            except Exception:
                pass


async def _fire_emergency_close(
    strategy_id: str,
    side: str,
    size: float,
    base: str,
    quote: str,
    webhook_secret: str,
    config: dict,
    unrealized_pct: float,
    dry_run: bool,
    db_pool,
    listener_url: str,
):
    if side == 'long':
        close_side, close_signal = 'sell', 'close_long'
    else:
        close_side, close_signal = 'buy', 'close_short'

    emergency_pct = float(config.get('emergency_exit_pct', 2.5))
    reasoning     = (
        f"Emergency exit: unrealized PnL {unrealized_pct:.2f}% "
        f"exceeded threshold -{emergency_pct:.2f}%"
    )

    payload = {
        'base_asset':  base,
        'quote_asset': quote,
        'side':        close_side,
        'order_type':  'market',
        'size':        str(size),
        'signal':      close_signal,
        'timestamp':   datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'token':       webhook_secret,
        'signal_source': 'ai_engine',
        'signal_metadata': {
            'confidence':     1.0,
            'reasoning':      reasoning,
            'trigger_reason': 'emergency_price_monitor',
            'template_id':    config.get('template_id', 'trend_following'),
            'dry_run':        dry_run,
        },
    }

    # Always log to ai_signal_log
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ai_signal_log (
                    strategy_id, triggered_at, trigger_reason, cycle_interval,
                    proposed_action, confidence, reasoning,
                    gate_passed, dry_run
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                strategy_id,
                datetime.now(timezone.utc),
                'emergency_price_monitor',
                'immediate',
                close_signal,
                1.0,
                reasoning,
                True,
                dry_run,
            )
    except Exception as exc:
        logger.error("Failed to write emergency close to ai_signal_log: %s", exc)

    if dry_run:
        logger.info("DRY RUN — emergency close suppressed for strategy=%s", strategy_id)
        return

    # Fire webhook
    try:
        signature = sign_payload(payload, webhook_secret)
        url = f"{listener_url.rstrip('/')}/webhook/{strategy_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url, json=payload,
                headers={'X-Agent-Signature': signature},
            )
        logger.info(
            "Emergency close webhook fired strategy=%s status=%d",
            strategy_id, resp.status_code,
        )
    except Exception as exc:
        logger.error("Emergency close webhook failed for %s: %s", strategy_id, exc)


async def start_all_price_monitors(db_pool, listener_url: str) -> list:
    """Starts one price_monitor_loop task per enabled AI strategy."""
    tasks = []
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
            task = asyncio.create_task(
                price_monitor_loop(sid, db_pool, listener_url),
                name=f"price_monitor_{sid}",
            )
            tasks.append(task)
        logger.info("Started %d price monitor(s)", len(tasks))
    except Exception as exc:
        logger.error("Failed to start price monitors: %s", exc)
    return tasks
