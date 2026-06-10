import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    d = dict(row)
    for k, v in d.items():
        if v is not None and not isinstance(v, (int, float, bool, str, list, dict, datetime)):
            try:
                d[k] = float(v)
            except (TypeError, ValueError):
                pass
    return d


class AdaptiveScheduler:
    """One instance per active AI strategy. Wakes on adaptive interval and triggers a LangGraph cycle."""

    def __init__(self, strategy_id: str, db_pool, graph):
        self.strategy_id    = strategy_id
        self.db_pool        = db_pool
        self.graph          = graph
        self._running       = False
        self._task          = None
        self._last_trigger: datetime | None = None
        self._last_interval: int = 4 * 3600
        self._wakeup: asyncio.Event | None = None

    async def start(self):
        self._running = True
        self._wakeup  = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name=f"scheduler_{self.strategy_id}")
        logger.info("Scheduler started strategy=%s", self.strategy_id)

    async def stop(self):
        self._running = False
        if self._wakeup:
            self._wakeup.set()  # unblock any ongoing sleep so the task can exit
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped strategy=%s", self.strategy_id)

    def interrupt(self) -> None:
        """Wake up the current sleep so the loop re-reads config from DB immediately."""
        if self._wakeup is not None:
            self._wakeup.set()

    async def _sleep(self, seconds: float) -> bool:
        """Sleep for `seconds` or until interrupt() is called.
        Returns True if interrupted early (config reload), False on natural expiry.
        CancelledError propagates unchanged so stop() works correctly."""
        self._wakeup.clear()
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout=seconds)
            return True  # woken early
        except asyncio.TimeoutError:
            return False  # normal expiry

    async def _loop(self):
        logger.info("Scheduler strategy=%s startup — triggering immediate cycle", self.strategy_id)
        await self._trigger_cycle('startup')

        while self._running:
            interval = await self._get_interval()
            self._last_interval = interval
            logger.info(
                "Scheduler strategy=%s interval=%ds (%.1fh)",
                self.strategy_id, interval, interval / 3600,
            )
            interrupted = await self._sleep(interval)
            if not self._running:
                break
            if interrupted:
                logger.info(
                    "Scheduler strategy=%s config reload — triggering immediate cycle",
                    self.strategy_id,
                )
                await self._trigger_cycle('config_reload')
            else:
                await self._trigger_cycle('scheduled')

    async def _get_interval(self) -> int:
        config = await self._load_config()
        if not config:
            return 4 * 60 * 60

        position = await self._get_open_position()
        if not position:
            return self._parse_interval(config['interval_no_position'])

        unrealized_pct = abs(float(position.get('pnl_unrealized_pct') or 0))
        if unrealized_pct >= float(config['at_risk_threshold_pct']):
            return self._parse_interval(config['interval_at_risk'])

        return self._parse_interval(config['interval_position_open'])

    def _parse_interval(self, interval_str: str) -> int:
        """Converts '4h', '15m', '1d', '5m' etc. to seconds."""
        unit  = interval_str[-1]
        value = int(interval_str[:-1])
        return value * {'m': 60, 'h': 3600, 'd': 86400}.get(unit, 3600)

    async def _trigger_cycle(self, trigger_reason: str):
        """Builds initial state and runs the LangGraph graph."""
        try:
            state = await self._build_initial_state(trigger_reason)
            self._last_trigger = datetime.now(timezone.utc)
            logger.info(
                "Triggering cycle strategy=%s reason=%s",
                self.strategy_id, trigger_reason,
            )
            await self.graph.ainvoke(state)
        except Exception as exc:
            logger.error("Scheduler cycle failed for %s: %s", self.strategy_id, exc)

    async def _build_initial_state(self, trigger_reason: str) -> dict:
        """Loads strategy + ai_strategy_config + ai_risk_config + position from DB."""
        async with self.db_pool.acquire() as conn:
            strategy = await conn.fetchrow(
                """
                SELECT s.*, a.*, r.max_position_size_pct,
                       r.max_daily_loss_pct, r.max_drawdown_pct,
                       r.max_concurrent_trades
                FROM strategies s
                JOIN ai_strategy_config a ON a.strategy_id = s.id
                LEFT JOIN ai_risk_config r ON r.strategy_id = s.id
                WHERE s.id = $1 AND s.enabled = true
                """,
                self.strategy_id,
            )
            if not strategy:
                raise ValueError(f"Strategy {self.strategy_id} not found or disabled")

            position = await conn.fetchrow(
                """
                SELECT *
                FROM strategy_positions
                WHERE strategy_id = $1 AND status = 'open'
                ORDER BY opened_at DESC LIMIT 1
                """,
                self.strategy_id,
            )

            last_signal = await conn.fetchrow(
                """
                SELECT reasoning FROM ai_signal_log
                WHERE strategy_id = $1
                  AND proposed_action IN ('open_long','open_short')
                  AND gate_passed = true
                ORDER BY triggered_at DESC LIMIT 1
                """,
                self.strategy_id,
            )

        sc  = _row_to_dict(strategy)
        pos = _row_to_dict(position) if position else None

        # Parse symbol → base_asset / quote_asset
        symbol = sc.get('symbol', 'BTC-USDT')
        if '-' in symbol:
            parts = symbol.split('-', 1)
        elif '/' in symbol:
            parts = symbol.split('/', 1)
        else:
            parts = [symbol, 'USDT']
        sc['base_asset']  = parts[0]
        sc['quote_asset'] = parts[1] if len(parts) > 1 else 'USDT'

        interval_label = self._get_interval_label(sc, pos)

        return {
            'strategy_id':    self.strategy_id,
            'strategy_config': sc,
            'risk_config': {
                'max_position_size_pct': float(sc.get('max_position_size_pct') or 5.0),
                'max_daily_loss_pct':    float(sc.get('max_daily_loss_pct') or 3.0),
                'max_drawdown_pct':      float(sc.get('max_drawdown_pct') or 8.0),
            },
            'trigger_reason':  trigger_reason,
            'cycle_interval':  interval_label,
            'triggered_at':    datetime.now(timezone.utc),
            'position_open':   pos is not None,
            'position_side':   pos.get('side') if pos else None,
            'position_entry_price': float(pos['entry_price']) if pos and pos.get('entry_price') else None,
            'position_size':        float(pos['size'])        if pos and pos.get('size')        else None,
            'position_unrealized_pnl_pct': None,
            'position_opened_at':    pos.get('opened_at') if pos else None,
            'original_reasoning':    last_signal['reasoning'] if last_signal else None,
            'ohlcv_data':            None,
            'technical_indicators':  None,
            'sentiment_data':        None,
            'news_data':             None,
            'market_context':        None,
            'data_fetch_errors':     [],
            'llm_signal':            None,
            'context_tokens':        None,
            'gate_passed':           False,
            'gate_rejection_reason': None,
            'resolved_size':         None,
            'resolved_sl_price':     None,
            'resolved_tp_price':     None,
            'webhook_fired':         False,
            'webhook_status':        None,
            'order_id':              None,
            'signal_log_id':         None,
        }

    def _get_interval_label(self, strategy: dict, position: dict | None) -> str:
        if not position:
            return strategy.get('interval_no_position', '4h')
        return strategy.get('interval_position_open', '15m')

    async def _load_config(self) -> dict | None:
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM ai_strategy_config WHERE strategy_id = $1",
                self.strategy_id,
            )
            return dict(row) if row else None

    async def _get_open_position(self) -> dict | None:
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM strategy_positions
                WHERE strategy_id = $1 AND status = 'open'
                ORDER BY opened_at DESC LIMIT 1""",
                self.strategy_id,
            )
            return dict(row) if row else None


async def start_all_schedulers(db_pool, graph) -> dict:
    """Loads all enabled AI strategies and starts one AdaptiveScheduler per strategy."""
    schedulers: dict[str, AdaptiveScheduler] = {}
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
            scheduler = AdaptiveScheduler(sid, db_pool, graph)
            await scheduler.start()
            schedulers[sid] = scheduler
        logger.info("Started %d scheduler(s): %s", len(schedulers), list(schedulers.keys()))
    except Exception as exc:
        logger.error("Failed to start schedulers: %s", exc)
    return schedulers


async def stop_all_schedulers(schedulers: dict):
    """Stops all running schedulers gracefully."""
    for sid, scheduler in schedulers.items():
        try:
            await scheduler.stop()
        except Exception as exc:
            logger.error("Error stopping scheduler for %s: %s", sid, exc)
    logger.info("All schedulers stopped")
