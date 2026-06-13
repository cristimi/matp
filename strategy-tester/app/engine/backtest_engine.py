"""
Backtest engine — full candle replay with causality enforcement.

Per-candle ordering (immutable — reordering introduces look-ahead bias):
  1. Execute pending open/close intent at candles[N].open  (filled from N-1 signal)
  2. SL/TP check on existing position using candles[N].high / candles[N].low
     (skipped for positions filled in step 1 of this same iteration — Rule 2)
  3. Build AgentState (window ending at N, simulated_now = candle[N] open ts)
  4. Invoke graph
  5. Record OpenIntent / CloseIntent from graph output (deferred — fills at N+1)
  6. Append per-candle equity_curve row; flush batch when buffer is full
"""
import asyncio
import logging
import math
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.data.historical_ohlcv import fetch_ohlcv_range
from app.data.ohlcv_cache import get_cached_candles, upsert_candles
from app.database import get_pool
from app.graph.graph_sim import build_sim_graph

logger = logging.getLogger(__name__)

_TF_MS = {
    '1m': 60_000, '3m': 180_000, '5m': 300_000, '15m': 900_000,
    '30m': 1_800_000, '1h': 3_600_000, '2h': 7_200_000, '4h': 14_400_000,
    '8h': 28_800_000, '1d': 86_400_000,
}
_WARMUP_CANDLES = 200
_WINDOW_SIZE    = 200
_PROGRESS_EVERY = 50


class BacktestAbortedHighFailureRate(Exception):
    pass


# ── Module-level engine globals (initialised from lifespan) ──────────────────

_run_semaphore: asyncio.Semaphore | None = None
_running_tasks: dict[str, asyncio.Task]  = {}


def init_engine(semaphore: asyncio.Semaphore) -> None:
    global _run_semaphore
    _run_semaphore = semaphore


def get_running_tasks() -> dict[str, asyncio.Task]:
    return _running_tasks


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class SimPosition:
    position_id:            str
    side:                   str           # 'long' | 'short'
    entry_price:            float
    size:                   float         # base quantity
    sl_price:               Optional[float]
    tp_price:               Optional[float]
    fee_open:               float         # already deducted from balance at open
    opened_at:              datetime      # fill candle open timestamp (simulated_now)
    opened_at_candle_index: int           # index into the full candles list
    opening_order_id:       Optional[str]
    leverage:               int = 1


@dataclass
class OpenIntent:
    side:         str
    triggered_at: datetime    # signal candle timestamp
    size:         float       # base qty from node_guard_sim
    sl_price:     Optional[float]
    tp_price:     Optional[float]


@dataclass
class CloseIntent:
    triggered_at: datetime


@dataclass
class _TpSlHit:
    price:  float
    reason: str              # 'tp_hit' | 'sl_hit'


@dataclass
class _EngineState:
    run_id:            str
    strategy_id:       str
    symbol:            str              # DB/cache format: BTC-USDT
    initial_balance:   float
    current_balance:   float
    slippage_pct:      float
    fee_pct:           float
    open_position:     Optional[SimPosition] = None
    pending_open:      Optional[OpenIntent]  = None
    pending_close:     Optional[CloseIntent] = None
    llm_failures:      int   = 0
    candles_processed: int   = 0
    total_signals:     int   = 0
    gate_passed_count: int   = 0
    peak_mark_balance: float = 0.0
    equity_buffer:     list  = field(default_factory=list)
    # per-trade records for final metrics: (net_pnl, side, closed_at, fees)
    closed_trade_records: list = field(default_factory=list)
    total_fees_paid:   float = 0.0
    long_count:        int   = 0
    short_count:       int   = 0


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _ms_to_dt(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def _candle_ts(candle: dict) -> datetime:
    """Open timestamp of a candle (this is what simulated_now is set to)."""
    return _ms_to_dt(candle['timestamp'])


def _compute_mark_pnl(pos: SimPosition, candle_close: float) -> float:
    if pos.side == 'long':
        return (candle_close - pos.entry_price) * pos.size * pos.leverage
    return (pos.entry_price - candle_close) * pos.size * pos.leverage


def _check_tp_sl(pos: SimPosition, candle: dict) -> Optional[_TpSlHit]:
    """Return the hit that occurred first using the entry-distance heuristic (Rule 3)."""
    tp_hit = sl_hit = False

    if pos.side == 'long':
        if pos.tp_price and candle['high'] >= pos.tp_price:
            tp_hit = True
        if pos.sl_price and candle['low'] <= pos.sl_price:
            sl_hit = True
    else:  # short
        if pos.tp_price and candle['low'] <= pos.tp_price:
            tp_hit = True
        if pos.sl_price and candle['high'] >= pos.sl_price:
            sl_hit = True

    if not tp_hit and not sl_hit:
        return None
    if tp_hit and not sl_hit:
        return _TpSlHit(price=pos.tp_price, reason='tp_hit')
    if sl_hit and not tp_hit:
        return _TpSlHit(price=pos.sl_price, reason='sl_hit')

    # Both hit on same candle — Rule 3: closer level to entry wins
    sl_dist = abs(pos.entry_price - pos.sl_price)
    tp_dist = abs(pos.entry_price - pos.tp_price)
    if sl_dist <= tp_dist:
        return _TpSlHit(price=pos.sl_price, reason='sl_hit')
    return _TpSlHit(price=pos.tp_price, reason='tp_hit')


def _apply_close_slippage(price: float, side: str, slippage_pct: float) -> float:
    """Slippage worsens close price for both sides (conservative)."""
    if side == 'long':
        return price * (1 - slippage_pct / 100)
    return price * (1 + slippage_pct / 100)


def _gross_pnl(pos: SimPosition, close_price: float) -> float:
    if pos.side == 'long':
        return (close_price - pos.entry_price) * pos.size * pos.leverage
    return (pos.entry_price - close_price) * pos.size * pos.leverage


# ── DB write helpers ──────────────────────────────────────────────────────────

async def _write_open_order(conn, eng: _EngineState, fill_price: float,
                             fill_ts: datetime, intent: OpenIntent) -> str:
    order_id = str(uuid.uuid4())
    symbol_slash = eng.symbol.replace('-', '/', 1)
    await conn.execute(
        """
        INSERT INTO tester.orders (
            id, backtest_run_id, candle_timestamp, symbol, side, signal,
            size, price, actual_fill_price, fee, strategy_id, status
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'filled')
        """,
        order_id, eng.run_id, fill_ts, symbol_slash,
        intent.side,
        f'open_{intent.side}',
        intent.size, fill_price, fill_price,
        fill_price * intent.size * eng.fee_pct / 100,
        eng.strategy_id,
    )
    return order_id


async def _write_open_position(conn, eng: _EngineState, pos: SimPosition,
                                fill_ts: datetime) -> None:
    symbol_slash = eng.symbol.replace('-', '/', 1)
    await conn.execute(
        """
        INSERT INTO tester.strategy_positions (
            id, backtest_run_id, strategy_id, symbol, side,
            entry_price, size, fee_open, status, opening_order_id, opened_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'open',$9,$10)
        """,
        pos.position_id, eng.run_id, eng.strategy_id, symbol_slash,
        pos.side, pos.entry_price, pos.size, pos.fee_open,
        pos.opening_order_id, fill_ts,
    )


async def _write_close_order(conn, eng: _EngineState, pos: SimPosition,
                              fill_price: float, fill_ts: datetime,
                              net_pnl: float, fee_close: float) -> str:
    order_id = str(uuid.uuid4())
    symbol_slash = eng.symbol.replace('-', '/', 1)
    await conn.execute(
        """
        INSERT INTO tester.orders (
            id, backtest_run_id, candle_timestamp, symbol, side, signal,
            size, price, actual_fill_price, pnl, fee, strategy_id, status
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'filled')
        """,
        order_id, eng.run_id, fill_ts,
        symbol_slash,
        'sell' if pos.side == 'long' else 'buy',
        f'close_{pos.side}',
        pos.size, fill_price, fill_price, net_pnl, fee_close,
        eng.strategy_id,
    )
    return order_id


async def _write_close_position(conn, pos: SimPosition, close_order_id: str,
                                 fill_price: float, fill_ts: datetime,
                                 net_pnl: float, fee_close: float,
                                 reason: str) -> None:
    await conn.execute(
        """
        UPDATE tester.strategy_positions
        SET status='closed', closing_price=$1, closing_order_id=$2,
            pnl_realized=$3, fee_close=$4, close_reason=$5, closed_at=$6,
            current_price=$1, updated_at=NOW()
        WHERE id=$7
        """,
        fill_price, close_order_id, net_pnl, fee_close, reason, fill_ts,
        pos.position_id,
    )


async def _flush_equity_buffer(pool, run_id: str, buffer: list) -> None:
    if not buffer:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO tester.equity_curve
                (backtest_run_id, candle_ts, realized_balance, mark_balance,
                 trade_pnl, drawdown_pct)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (backtest_run_id, candle_ts) DO NOTHING
            """,
            buffer,
        )
    buffer.clear()


async def _update_progress(pool, run_id: str, processed: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tester.backtest_runs SET candles_processed=$1, updated_at=NOW() WHERE id=$2",
            processed, run_id,
        )


# ── Position open / close actions ────────────────────────────────────────────

async def _execute_pending_open(eng: _EngineState, candle: dict,
                                 candle_idx: int, pool) -> bool:
    """Fill pending_open at candle's open price. Returns True if filled."""
    if eng.pending_open is None:
        return False
    intent = eng.pending_open
    eng.pending_open = None

    raw_fill = candle['open']
    if intent.side == 'long':
        fill_price = raw_fill * (1 + eng.slippage_pct / 100)
    else:
        fill_price = raw_fill * (1 - eng.slippage_pct / 100)

    fee_open = fill_price * intent.size * eng.fee_pct / 100
    eng.current_balance -= fee_open      # §6.6: fee charged at fill

    fill_ts = _candle_ts(candle)
    pos_id  = str(uuid.uuid4())

    async with pool.acquire() as conn:
        async with conn.transaction():
            order_id = await _write_open_order(conn, eng, fill_price, fill_ts, intent)
            pos = SimPosition(
                position_id=pos_id,
                side=intent.side,
                entry_price=fill_price,
                size=intent.size,
                sl_price=intent.sl_price,
                tp_price=intent.tp_price,
                fee_open=fee_open,
                opened_at=fill_ts,
                opened_at_candle_index=candle_idx,
                opening_order_id=order_id,
            )
            await _write_open_position(conn, eng, pos, fill_ts)

    eng.open_position = pos
    logger.debug("OPEN %s %.4f @ %.2f  run=%s", pos.side, pos.size, fill_price, eng.run_id)
    return True


async def _execute_close(eng: _EngineState, candle: dict, fill_price: float,
                          fill_ts: datetime, reason: str, pool,
                          trade_pnl_out: list) -> None:
    """Close the current open position and update engine balance."""
    pos = eng.open_position
    assert pos is not None

    slipped = _apply_close_slippage(fill_price, pos.side, eng.slippage_pct)
    gross   = _gross_pnl(pos, slipped)
    fee_c   = slipped * pos.size * eng.fee_pct / 100
    net_pnl = gross - fee_c               # fee_open already deducted at open

    eng.current_balance += net_pnl
    eng.total_fees_paid  += pos.fee_open + fee_c

    true_net = gross - pos.fee_open - fee_c   # for metrics only

    async with pool.acquire() as conn:
        async with conn.transaction():
            close_order_id = await _write_close_order(
                conn, eng, pos, slipped, fill_ts, true_net, fee_c)
            await _write_close_position(
                conn, pos, close_order_id, slipped, fill_ts, true_net, fee_c, reason)

    eng.closed_trade_records.append({
        'net_pnl': true_net,
        'side':    pos.side,
        'closed_at': fill_ts,
    })
    trade_pnl_out.append(true_net)

    if pos.side == 'long':
        eng.long_count += 1
    else:
        eng.short_count += 1

    logger.debug("CLOSE %s @ %.2f  reason=%s  net_pnl=%.4f  balance=%.4f",
                 pos.side, slipped, reason, true_net, eng.current_balance)

    eng.open_position  = None
    eng.pending_close  = None


async def _execute_pending_close(eng: _EngineState, candle: dict,
                                  pool, trade_pnl_out: list) -> bool:
    """Fill pending_close at candle's open price. Returns True if executed."""
    if eng.pending_close is None or eng.open_position is None:
        eng.pending_close = None
        return False
    fill_ts = _candle_ts(candle)
    await _execute_close(eng, candle, candle['open'], fill_ts, 'llm_close', pool, trade_pnl_out)
    return True


# ── Aggregate metrics ─────────────────────────────────────────────────────────

def _compute_metrics(eng: _EngineState, equity_series: list[float]) -> dict:
    trades = eng.closed_trade_records
    n      = len(trades)

    wins   = [t['net_pnl'] for t in trades if t['net_pnl'] > 0]
    losses = [t['net_pnl'] for t in trades if t['net_pnl'] <= 0]

    total_pnl     = sum(t['net_pnl'] for t in trades)
    total_pnl_pct = total_pnl / eng.initial_balance * 100 if eng.initial_balance else 0

    win_rate      = len(wins) / n * 100 if n else 0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(wins) > 0 else (
                    None if not wins else None)

    avg_win  = statistics.mean(wins)   if wins   else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0

    # Max drawdown from mark_balance series
    max_dd = 0.0
    peak   = equity_series[0] if equity_series else eng.initial_balance
    for b in equity_series:
        if b > peak:
            peak = b
        if peak > 0:
            dd = (peak - b) / peak * 100
            if dd > max_dd:
                max_dd = dd

    # Sharpe (trade-return based approximation)
    returns = [t['net_pnl'] / eng.initial_balance for t in trades]
    sharpe  = None
    if len(returns) >= 2:
        mean_r = statistics.mean(returns)
        std_r  = statistics.stdev(returns)
        if std_r > 0:
            sharpe = round(mean_r / std_r * math.sqrt(252), 4)

    return {
        'total_trades':   n,
        'winning_trades': len(wins),
        'losing_trades':  len(losses),
        'win_rate':       round(win_rate, 2),
        'total_pnl':      round(total_pnl, 8),
        'total_pnl_pct':  round(total_pnl_pct, 4),
        'profit_factor':  round(profit_factor, 4) if profit_factor else None,
        'max_drawdown_pct': round(max_dd, 4),
        'sharpe_approx':  sharpe,
        'long_count':     eng.long_count,
        'short_count':    eng.short_count,
        'avg_win':        round(avg_win, 8),
        'avg_loss':       round(avg_loss, 8),
        'largest_win':    round(max(wins), 8) if wins else 0.0,
        'largest_loss':   round(min(losses), 8) if losses else 0.0,
        'total_fees_paid': round(eng.total_fees_paid, 8),
    }


# ── Main loop ─────────────────────────────────────────────────────────────────

async def _execute_run(pool, run_id: str) -> None:
    # Load run + strategy rows
    async with pool.acquire() as conn:
        run = await conn.fetchrow(
            "SELECT * FROM tester.backtest_runs WHERE id=$1", run_id)
        if run is None:
            logger.error("run_backtest: run %s not found", run_id)
            return
        strategy = await conn.fetchrow(
            """
            SELECT s.*, aic.cooldown_entry_minutes, aic.cooldown_stop_adj_minutes,
                   aic.confidence_threshold, aic.use_technical, aic.indicators,
                   aic.template_id, aic.llm_provider, aic.llm_model, aic.custom_instructions,
                   arc.max_position_size_pct, arc.max_concurrent_trades
            FROM tester.strategies s
            LEFT JOIN tester.ai_strategy_config aic ON aic.strategy_id = s.id
            LEFT JOIN tester.ai_risk_config arc     ON arc.strategy_id = s.id
            WHERE s.id = $1
            """,
            run['strategy_id'],
        )
        # Mark running
        await conn.execute(
            "UPDATE tester.backtest_runs SET status='running', started_at=NOW(), updated_at=NOW() WHERE id=$1",
            run_id,
        )

    timeframe   = run['timeframe']
    tf_ms       = _TF_MS.get(timeframe, 3_600_000)
    symbol_db   = strategy['symbol']          # stored as BTC-USDT
    symbol_ccxt = symbol_db.replace('-', '/', 1)
    exchange    = 'binance'

    initial_balance = float(run['initial_balance'])
    slippage_pct    = float(run['slippage_pct'])
    fee_pct         = float(run['fee_pct'])

    # ── Fetch / cache candles ─────────────────────────────────────────────────
    from datetime import date as date_cls
    date_from: date_cls = run['date_from']
    date_to:   date_cls = run['date_to']
    from datetime import timedelta
    lookback_days = int(run['lookback_days']) if run['lookback_days'] is not None else 90

    fetch_from_dt = datetime(
        date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc
    ) - timedelta(days=lookback_days)
    fetch_to_dt   = datetime(date_to.year,   date_to.month,   date_to.day,   tzinfo=timezone.utc)

    ts_from_ms = int(fetch_from_dt.timestamp() * 1000)
    ts_to_ms   = int(fetch_to_dt.timestamp()   * 1000)

    logger.info("run=%s fetching candles %s %s %s → %s",
                run_id, symbol_db, timeframe, fetch_from_dt.date(), fetch_to_dt.date())

    candles = await get_cached_candles(pool, symbol_db, timeframe, exchange, ts_from_ms, ts_to_ms)
    if len(candles) < _WARMUP_CANDLES + 10:
        # Not enough in cache — fetch from exchange
        raw = await fetch_ohlcv_range(exchange, symbol_ccxt, timeframe, ts_from_ms, ts_to_ms)
        await upsert_candles(pool, symbol_db, timeframe, exchange, raw)
        candles = await get_cached_candles(pool, symbol_db, timeframe, exchange, ts_from_ms, ts_to_ms)

    if len(candles) < _WARMUP_CANDLES + 10:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tester.backtest_runs SET status='failed', error_message=$1, completed_at=NOW(), updated_at=NOW() WHERE id=$2",
                f"Insufficient OHLCV data: only {len(candles)} candles (need >{_WARMUP_CANDLES})", run_id,
            )
        return

    warmup_end   = _WARMUP_CANDLES
    active_range = candles[warmup_end:]
    total        = len(active_range)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tester.backtest_runs SET total_candles=$1, updated_at=NOW() WHERE id=$2",
            total, run_id,
        )

    # ── Engine state ──────────────────────────────────────────────────────────
    eng = _EngineState(
        run_id=run_id,
        strategy_id=str(run['strategy_id']),
        symbol=symbol_db,
        initial_balance=initial_balance,
        current_balance=initial_balance,
        slippage_pct=slippage_pct,
        fee_pct=fee_pct,
        peak_mark_balance=initial_balance,
    )

    # strategy_config dict injected into each graph invocation
    base_asset, quote_asset = (symbol_db.split('-', 1) + ['USDT'])[:2]
    strategy_config = {
        'base_asset':             base_asset,
        'quote_asset':            quote_asset,
        'use_technical':          bool(strategy['use_technical'] if strategy['use_technical'] is not None else True),
        'indicators':             list(strategy['indicators']) if strategy['indicators'] else ['RSI', 'MACD', 'EMA50', 'EMA200', 'BB', 'VWAP'],
        'template_id':            strategy['template_id'] or 'trend_following',
        'llm_provider':           strategy['llm_provider'] or 'google',
        'llm_model':              strategy['llm_model'] or 'gemini-2.5-flash',
        'confidence_threshold':   float(strategy['confidence_threshold'] or 0.72),
        'cooldown_entry_minutes': int(strategy['cooldown_entry_minutes']) if strategy['cooldown_entry_minutes'] is not None else 240,
        'cooldown_stop_adj_minutes': int(strategy['cooldown_stop_adj_minutes']) if strategy['cooldown_stop_adj_minutes'] is not None else 30,
        'custom_instructions':    strategy['custom_instructions'],
        'dry_signal_mode':        bool(run.get('dry_signal_mode', False)),
    }
    risk_config = {
        'max_position_size_pct': float(strategy['max_position_size_pct'] or 5.0),
        'max_concurrent_trades': int(strategy['max_concurrent_trades'] or 1),
    }

    graph = build_sim_graph()
    equity_series: list[float] = []   # mark_balance per candle (for final drawdown)

    # ── Main candle loop ──────────────────────────────────────────────────────
    for rel_idx, candle in enumerate(active_range):
        abs_idx  = warmup_end + rel_idx
        candle_ts = _candle_ts(candle)
        trade_pnl_this_candle: list[float] = []

        # ── Step 1: execute pending open/close at candle's open ───────────────
        just_opened = await _execute_pending_open(eng, candle, abs_idx, pool)
        if not just_opened:
            await _execute_pending_close(eng, candle, pool, trade_pnl_this_candle)

        # ── Step 2: SL/TP check (skip if position was just opened this candle)
        if (eng.open_position is not None
                and not just_opened
                and eng.open_position.opened_at_candle_index < abs_idx):
            hit = _check_tp_sl(eng.open_position, candle)
            if hit is not None:
                # Assertion: opened_at_candle_index must be < abs_idx (Rule 2)
                assert eng.open_position.opened_at_candle_index < abs_idx, (
                    f"Rule-2 violation: position opened at candle {eng.open_position.opened_at_candle_index} "
                    f"would close on same candle {abs_idx}"
                )
                await _execute_close(
                    eng, candle, hit.price, candle_ts, hit.reason, pool,
                    trade_pnl_this_candle,
                )

        # ── Step 3: build AgentState ──────────────────────────────────────────
        window_start = max(0, abs_idx - _WINDOW_SIZE + 1)
        candle_window = candles[window_start : abs_idx + 1]

        # sim_pnl_today: sum of net_pnl from trades closed in last 24 simulated hours
        cutoff_24h = candle_ts.timestamp() - 86400
        pnl_today = sum(
            t['net_pnl'] / eng.initial_balance * 100
            for t in eng.closed_trade_records
            if t['closed_at'].timestamp() >= cutoff_24h
        )

        initial_state = {
            'strategy_id':      eng.strategy_id,
            'strategy_config':  strategy_config,
            'risk_config':      risk_config,
            'trigger_reason':   'replay',
            'cycle_interval':   timeframe,
            'triggered_at':     candle_ts,
            # position context from engine
            'position_open':    eng.open_position is not None,
            'position_side':    eng.open_position.side if eng.open_position else None,
            'position_entry_price': eng.open_position.entry_price if eng.open_position else None,
            'position_size':    eng.open_position.size if eng.open_position else None,
            'position_unrealized_pnl_pct': None,
            'position_opened_at': eng.open_position.opened_at if eng.open_position else None,
            'original_reasoning': None,
            'ohlcv_data':           None,
            'technical_indicators': None,
            'sentiment_data':       None,
            'news_data':            None,
            'market_context':       None,
            'data_fetch_errors':    [],
            'llm_signal':           None,
            'context_tokens':       None,
            'gate_passed':          False,
            'gate_rejection_reason': None,
            'resolved_size':        None,
            'resolved_sl_price':    None,
            'resolved_tp_price':    None,
            'webhook_fired':        False,
            'webhook_status':       None,
            'order_id':             None,
            'signal_log_id':        None,
            # sim-specific
            'simulated_now':        candle_ts,
            'backtest_run_id':      run_id,
            'sim_balance':          eng.current_balance,
            'sim_pnl_today':        pnl_today,
            'replay_candle_window': candle_window,
            'sim_action':           None,
        }

        # ── Step 4: invoke graph ──────────────────────────────────────────────
        eng.total_signals += 1
        try:
            final_state = await graph.ainvoke(initial_state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            eng.llm_failures += 1
            logger.warning("run=%s graph invoke failed at candle %d: %s", run_id, abs_idx, exc)
            # Check failure threshold (only after 100 candles)
            if eng.candles_processed >= 100:
                rate = eng.llm_failures / eng.candles_processed
                if rate > settings.tester_llm_failure_threshold:
                    raise BacktestAbortedHighFailureRate(
                        f"LLM failure rate {rate:.1%} exceeds threshold "
                        f"{settings.tester_llm_failure_threshold:.1%} after "
                        f"{eng.candles_processed} candles"
                    )
            eng.candles_processed += 1
            # Still write equity row with no trade_pnl
            mark = eng.current_balance + (
                _compute_mark_pnl(eng.open_position, candle['close'])
                if eng.open_position else 0.0
            )
            eng.peak_mark_balance = max(eng.peak_mark_balance, mark)
            dd = (eng.peak_mark_balance - mark) / eng.peak_mark_balance * 100 if eng.peak_mark_balance > 0 else 0
            equity_series.append(mark)
            eng.equity_buffer.append((run_id, candle_ts, eng.current_balance, mark, None, round(dd, 4)))
            if len(eng.equity_buffer) >= settings.tester_equity_insert_batch:
                await _flush_equity_buffer(pool, run_id, eng.equity_buffer)
            continue

        # ── Step 5: record intent (deferred — fills at next candle) ──────────
        if final_state.get('gate_passed'):
            eng.gate_passed_count += 1
            action = final_state.get('sim_action')
            if action in ('open_long', 'open_short') and eng.open_position is None:
                eng.pending_open = OpenIntent(
                    side='long' if action == 'open_long' else 'short',
                    triggered_at=candle_ts,
                    size=float(final_state.get('resolved_size') or 0),
                    sl_price=final_state.get('resolved_sl_price'),
                    tp_price=final_state.get('resolved_tp_price'),
                )
            elif action in ('close_long', 'close_short') and eng.open_position is not None:
                eng.pending_close = CloseIntent(triggered_at=candle_ts)

        # ── Step 6: per-candle equity row ─────────────────────────────────────
        mark = eng.current_balance + (
            _compute_mark_pnl(eng.open_position, candle['close'])
            if eng.open_position else 0.0
        )
        eng.peak_mark_balance = max(eng.peak_mark_balance, mark)
        dd = (eng.peak_mark_balance - mark) / eng.peak_mark_balance * 100 if eng.peak_mark_balance > 0 else 0
        trade_pnl = trade_pnl_this_candle[0] if trade_pnl_this_candle else None
        equity_series.append(mark)
        eng.equity_buffer.append(
            (run_id, candle_ts, eng.current_balance, mark, trade_pnl, round(dd, 4))
        )

        eng.candles_processed += 1

        if len(eng.equity_buffer) >= settings.tester_equity_insert_batch:
            await _flush_equity_buffer(pool, run_id, eng.equity_buffer)

        if eng.candles_processed % _PROGRESS_EVERY == 0:
            await _update_progress(pool, run_id, eng.candles_processed)

    # ── End of loop: close any open position at last candle's close ───────────
    if eng.open_position is not None and active_range:
        last_candle = active_range[-1]
        last_ts     = _candle_ts(last_candle)
        dummy: list = []
        await _execute_close(
            eng, last_candle, last_candle['close'], last_ts, 'run_end', pool, dummy
        )

    # Flush remaining equity rows
    await _flush_equity_buffer(pool, run_id, eng.equity_buffer)

    # ── Compute and persist aggregate metrics ─────────────────────────────────
    metrics = _compute_metrics(eng, equity_series)
    llm_rate = round(eng.llm_failures / eng.total_signals * 100, 2) if eng.total_signals else 0.0

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE tester.backtest_runs SET
                status='completed',
                candles_processed=$1, total_candles=$2,
                total_signals=$3, gate_passed=$4,
                llm_failures=$5, llm_failure_rate=$6,
                total_trades=$7, winning_trades=$8, losing_trades=$9,
                win_rate=$10, total_pnl=$11, total_pnl_pct=$12,
                profit_factor=$13, max_drawdown_pct=$14, sharpe_approx=$15,
                long_count=$16, short_count=$17,
                avg_win=$18, avg_loss=$19,
                largest_win=$20, largest_loss=$21,
                total_fees_paid=$22,
                completed_at=NOW(), updated_at=NOW()
            WHERE id=$23
            """,
            eng.candles_processed, total,
            eng.total_signals, eng.gate_passed_count,
            eng.llm_failures, llm_rate,
            metrics['total_trades'], metrics['winning_trades'], metrics['losing_trades'],
            metrics['win_rate'], metrics['total_pnl'], metrics['total_pnl_pct'],
            metrics['profit_factor'], metrics['max_drawdown_pct'], metrics['sharpe_approx'],
            metrics['long_count'], metrics['short_count'],
            metrics['avg_win'], metrics['avg_loss'],
            metrics['largest_win'], metrics['largest_loss'],
            metrics['total_fees_paid'],
            run_id,
        )

    logger.info(
        "run=%s COMPLETED: %d candles, %d trades, pnl=%.2f%%, max_dd=%.2f%%",
        run_id, eng.candles_processed,
        metrics['total_trades'], metrics['total_pnl_pct'], metrics['max_drawdown_pct'],
    )


async def run_backtest(run_id: str) -> None:
    """Entry point for asyncio.create_task. Acquires semaphore, executes run."""
    pool = get_pool()
    sem  = _run_semaphore
    if sem is None:
        logger.error("run_backtest called before init_engine()")
        return
    try:
        async with sem:
            await _execute_run(pool, run_id)
    except asyncio.CancelledError:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tester.backtest_runs SET status='cancelled', completed_at=NOW(), updated_at=NOW() WHERE id=$1",
                run_id,
            )
        logger.info("run=%s cancelled", run_id)
    except BacktestAbortedHighFailureRate as exc:
        async with pool.acquire() as conn:
            rate = 0.0
            row = await conn.fetchrow("SELECT llm_failures, total_signals FROM tester.backtest_runs WHERE id=$1", run_id)
            if row and row['total_signals']:
                rate = round(row['llm_failures'] / row['total_signals'] * 100, 2)
            await conn.execute(
                """UPDATE tester.backtest_runs SET status='aborted_high_failure_rate',
                   llm_failure_rate=$1, error_message=$2, completed_at=NOW(), updated_at=NOW()
                   WHERE id=$3""",
                rate, str(exc), run_id,
            )
        logger.warning("run=%s aborted: %s", run_id, exc)
    except Exception as exc:
        logger.exception("run=%s failed: %s", run_id, exc)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tester.backtest_runs SET status='failed', error_message=$1, completed_at=NOW(), updated_at=NOW() WHERE id=$2",
                str(exc), run_id,
            )
    finally:
        _running_tasks.pop(run_id, None)
