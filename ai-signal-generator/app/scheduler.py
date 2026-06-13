import asyncio
import logging
from datetime import datetime, timezone

import httpx

from app.config import settings

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

        if pos and sc.get('account_id') and sc.get('symbol'):
            pos = await self._recover_external_close(pos, sc['account_id'], sc['symbol'])

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

    async def _recover_external_close(self, pos: dict, account_id: str, symbol: str) -> dict | None:
        """
        If the DB position is open but no longer live on the exchange, query the
        exchange history to recover closing details, write a synthetic closing order,
        and mark the position closed. Returns None so the cycle runs as flat.
        If exchange is unreachable, returns pos unchanged (fail safe).
        """
        try:
            # 1. Check if position is still live
            async with httpx.AsyncClient(timeout=10.0) as client:
                live_resp = await client.get(
                    f"{settings.matp_executor_url}/accounts/{account_id}/positions"
                )
                live_resp.raise_for_status()
                live = live_resp.json()

            live_symbols = {p.get('symbol') for p in live if isinstance(p, dict)}
            if symbol in live_symbols:
                return pos  # still live, nothing to do

        except Exception as exc:
            logger.warning(
                "Scheduler strategy=%s: live position check failed (%s) — skipping recovery",
                self.strategy_id, exc,
            )
            return pos

        # 2. Position is gone — fetch closing details from exchange history
        logger.warning(
            "Scheduler strategy=%s: position %s not found on exchange — recovering from history",
            self.strategy_id, symbol,
        )
        details = None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                hist_resp = await client.get(
                    f"{settings.matp_executor_url}/accounts/{account_id}/positions/history",
                    params={"symbol": symbol},
                )
                hist_resp.raise_for_status()
                details = hist_resp.json() or None
        except Exception as exc:
            logger.warning(
                "Scheduler strategy=%s: history fetch failed (%s) — marking as closed without details",
                self.strategy_id, exc,
            )

        close_reason  = (details or {}).get("close_reason", "Closed on exchange")
        closing_price = (details or {}).get("closing_price")
        pnl_realized  = (details or {}).get("pnl_realized")
        closed_at     = (details or {}).get("closed_at")
        raw           = (details or {}).get("raw")

        # Reject stale history: exchange close must be AFTER the position was opened
        pos_opened_at = pos.get("opened_at")
        if closed_at and pos_opened_at:
            from datetime import datetime as _dt
            try:
                _ca = _dt.fromisoformat(str(closed_at).replace("Z", "+00:00"))
                _oa = pos_opened_at if hasattr(pos_opened_at, 'tzinfo') else _dt.fromisoformat(str(pos_opened_at).replace("Z", "+00:00"))
                if _ca <= _oa:
                    logger.warning(
                        "Scheduler strategy=%s: history closed_at (%s) <= opened_at (%s) — skipping recovery",
                        self.strategy_id, closed_at, pos_opened_at,
                    )
                    return pos
            except Exception:
                pass

        # 3. Write synthetic closing order + update position in one transaction
        try:
            async with self.db_pool.acquire() as conn:
                async with conn.transaction():
                    close_side = "sell" if pos.get("side") == "long" else "buy"
                    signal_val = "liquidation" if close_reason == "Liquidated" else "exchange_close"

                    order_id = await conn.fetchval(
                        """INSERT INTO orders
                               (symbol, side, signal, order_type, size, platform,
                                strategy_id, account_id, status, actual_fill_price,
                                pnl, raw_webhook, raw_response, signal_source,
                                received_at)
                           VALUES ($1,$2,$3,'market',$4,$5,$6,$7,'filled',$8,$9,
                                   '{}'::jsonb,$10::jsonb,'exchange',$11)
                           RETURNING id""",
                        symbol,
                        close_side,
                        signal_val,
                        pos.get("size"),
                        pos.get("exchange", "unknown"),
                        self.strategy_id,
                        account_id,
                        closing_price,
                        pnl_realized,
                        __import__('json').dumps(raw) if raw else None,
                        closed_at,
                    )

                    await conn.execute(
                        """UPDATE strategy_positions
                           SET status        = 'closed',
                               close_reason  = $1,
                               closing_price = $2,
                               pnl_realized  = $3,
                               closed_at     = COALESCE($4, NOW()),
                               closing_order_id = $5
                           WHERE id = $6 AND status = 'open'""",
                        close_reason,
                        closing_price,
                        pnl_realized,
                        closed_at,
                        order_id,
                        pos["id"],
                    )

            logger.info(
                "Scheduler strategy=%s: position %s recovered — %s, pnl=%s",
                self.strategy_id, symbol, close_reason, pnl_realized,
            )
        except Exception as exc:
            logger.error(
                "Scheduler strategy=%s: failed to write recovery to DB (%s)",
                self.strategy_id, exc,
            )

        return None  # treat as flat regardless of DB write outcome

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
