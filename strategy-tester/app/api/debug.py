"""
Temporary debug endpoints for Phase verification.
These endpoints are NOT part of the production API.
"""
import logging
import time
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_pool
from app.data.historical_ohlcv import fetch_ohlcv_range
from app.data.ohlcv_cache import get_cached_candles, is_cache_sufficient, upsert_candles
from app.graph.graph_sim import build_sim_graph

logger = logging.getLogger(__name__)
router = APIRouter()


def _normalise_symbol(symbol: str) -> str:
    """Return DB/cache key format: BTC-USDT"""
    return symbol.upper().replace("/", "-").replace("_", "-")


def _ccxt_symbol(normalised: str) -> str:
    """Return ccxt slash format: BTC/USDT from BTC-USDT"""
    return normalised.replace("-", "/", 1)


class FetchOhlcvRequest(BaseModel):
    symbol: str
    timeframe: str = "1h"
    exchange: str = "binance"
    date_from: date
    date_to: date


@router.post("/fetch-ohlcv")
async def debug_fetch_ohlcv(body: FetchOhlcvRequest):
    """
    Fetch historical OHLCV candles, reading from tester.ohlcv_cache when possible.
    On cache miss, fetches from exchange and populates the cache.
    """
    symbol_db = _normalise_symbol(body.symbol)
    symbol_ccxt = _ccxt_symbol(symbol_db)

    ts_from_ms = int(
        datetime(body.date_from.year, body.date_from.month, body.date_from.day,
                 tzinfo=timezone.utc).timestamp() * 1000
    )
    ts_to_ms = int(
        datetime(body.date_to.year, body.date_to.month, body.date_to.day,
                 tzinfo=timezone.utc).timestamp() * 1000
    )

    if ts_from_ms >= ts_to_ms:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")

    pool = get_pool()
    t0 = time.monotonic()

    # Try cache first
    cached = await get_cached_candles(
        pool, symbol_db, body.timeframe, body.exchange, ts_from_ms, ts_to_ms
    )
    if is_cache_sufficient(cached, ts_from_ms, ts_to_ms, body.timeframe):
        return {
            "candles_returned": len(cached),
            "source": "cache",
            "elapsed_ms": round((time.monotonic() - t0) * 1000),
            "symbol": symbol_db,
            "timeframe": body.timeframe,
            "date_from": str(body.date_from),
            "date_to": str(body.date_to),
            "first_candle_ts": cached[0]["timestamp"] if cached else None,
            "last_candle_ts":  cached[-1]["timestamp"] if cached else None,
        }

    # Cache miss — fetch from exchange
    logger.info(
        "Cache miss for %s %s %s [%s, %s] — fetching from exchange",
        symbol_db, body.timeframe, body.exchange, body.date_from, body.date_to,
    )
    candles = await fetch_ohlcv_range(
        body.exchange, symbol_ccxt, body.timeframe, ts_from_ms, ts_to_ms
    )
    upserted = await upsert_candles(pool, symbol_db, body.timeframe, body.exchange, candles)

    return {
        "candles_returned": len(candles),
        "source": "network",
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
        "symbol": symbol_db,
        "timeframe": body.timeframe,
        "date_from": str(body.date_from),
        "date_to": str(body.date_to),
        "first_candle_ts": candles[0]["timestamp"] if candles else None,
        "last_candle_ts":  candles[-1]["timestamp"] if candles else None,
        "cached_now": upserted,
    }


# ── invoke-graph ──────────────────────────────────────────────────────────────

class InvokeGraphRequest(BaseModel):
    strategy_id:      str
    symbol:           str          # e.g. "BTC/USDT"
    timeframe:        str = "1h"
    exchange:         str = "binance"
    simulated_ts:     datetime     # ISO timestamp — the candle to use as simulated_now
    window_size:      int = 50     # candles to pass to node_ingest_replay
    dry_signal:       bool = True  # if True, skip LLM (R3: no credits without permission)
    backtest_run_id:  str | None = None  # reuse an existing run (for cooldown tests)


@router.post("/invoke-graph")
async def debug_invoke_graph(body: InvokeGraphRequest):
    """
    Invoke the simulation graph for a single candle.
    Verifies that triggered_at in tester.ai_signal_log equals simulated_ts.
    R3: dry_signal=True by default — no LLM credits spent.
    """
    pool       = get_pool()
    symbol_db  = _normalise_symbol(body.symbol)
    sim_now    = body.simulated_ts.replace(tzinfo=timezone.utc) \
                 if body.simulated_ts.tzinfo is None else body.simulated_ts

    # 1. Load strategy + configs
    async with pool.acquire() as conn:
        strategy_row = await conn.fetchrow(
            """
            SELECT s.id, s.symbol, s.interval,
                   aic.template_id, aic.llm_provider, aic.llm_model,
                   aic.use_technical, aic.indicators,
                   aic.confidence_threshold, aic.cooldown_entry_minutes,
                   aic.cooldown_stop_adj_minutes, aic.dry_run,
                   aic.custom_instructions,
                   arc.max_position_size_pct, arc.max_daily_loss_pct,
                   arc.max_drawdown_pct, arc.max_concurrent_trades
            FROM tester.strategies s
            LEFT JOIN tester.ai_strategy_config aic ON aic.strategy_id = s.id
            LEFT JOIN tester.ai_risk_config arc     ON arc.strategy_id = s.id
            WHERE s.id = $1 AND s.is_deleted = false
            """,
            body.strategy_id,
        )
        if strategy_row is None:
            raise HTTPException(status_code=404, detail=f"Strategy not found: {body.strategy_id}")

        # 2. Reuse provided run_id or create a temporary backtest_run
        sym = strategy_row['symbol'] or symbol_db
        if body.backtest_run_id:
            run_id = body.backtest_run_id
        else:
            run_id = await conn.fetchval(
                """
                INSERT INTO tester.backtest_runs
                    (strategy_id, timeframe, date_from, date_to, status)
                VALUES ($1, $2, $3, $4, 'running')
                RETURNING id::text
                """,
                body.strategy_id, body.timeframe,
                sim_now.date(),
                sim_now.date(),
            )

    # 3. Fetch candle window from cache ending at simulated_ts
    sim_ts_ms = int(sim_now.timestamp() * 1000)
    tf_ms_map = {
        '1m': 60_000, '3m': 180_000, '5m': 300_000, '15m': 900_000,
        '30m': 1_800_000, '1h': 3_600_000, '2h': 7_200_000,
        '4h': 14_400_000, '8h': 28_800_000, '1d': 86_400_000,
    }
    tf_ms = tf_ms_map.get(body.timeframe, 3_600_000)
    window_from_ms = sim_ts_ms - (body.window_size - 1) * tf_ms

    candle_window = await get_cached_candles(
        pool, symbol_db, body.timeframe, body.exchange,
        window_from_ms, sim_ts_ms,
    )
    if not candle_window:
        raise HTTPException(
            status_code=422,
            detail=f"No cached candles for {symbol_db} {body.timeframe} ending at {sim_now}. "
                   "Run POST /debug/fetch-ohlcv first.",
        )

    # 4. Build strategy_config and risk_config dicts
    base_asset, quote_asset = (sym.replace("/", "-").split("-", 1) + ["USDT"])[:2]
    strategy_config = {
        'base_asset':               base_asset,
        'quote_asset':              quote_asset,
        'use_technical':            bool(strategy_row['use_technical'] if strategy_row['use_technical'] is not None else True),
        'indicators':               list(strategy_row['indicators']) if strategy_row['indicators'] else ['RSI', 'MACD', 'EMA50', 'EMA200', 'BB', 'VWAP'],
        'template_id':              strategy_row['template_id'] or 'trend_following',
        'llm_provider':             strategy_row['llm_provider'] or 'google',
        'llm_model':                strategy_row['llm_model'] or 'gemini-2.5-flash',
        'confidence_threshold':     float(strategy_row['confidence_threshold'] or 0.72),
        'cooldown_entry_minutes':    int(strategy_row['cooldown_entry_minutes']) if strategy_row['cooldown_entry_minutes'] is not None else 240,
        'cooldown_stop_adj_minutes': int(strategy_row['cooldown_stop_adj_minutes']) if strategy_row['cooldown_stop_adj_minutes'] is not None else 30,
        'custom_instructions':      strategy_row['custom_instructions'],
        'dry_signal_mode':          body.dry_signal,
    }
    risk_config = {
        'max_position_size_pct': float(strategy_row['max_position_size_pct'] or 5.0),
        'max_daily_loss_pct':    float(strategy_row['max_daily_loss_pct'] or 3.0),
        'max_drawdown_pct':      float(strategy_row['max_drawdown_pct'] or 8.0),
        'max_concurrent_trades': int(strategy_row['max_concurrent_trades'] or 1),
    }

    # 5. Build initial AgentState
    initial_state = {
        'strategy_id':      body.strategy_id,
        'strategy_config':  strategy_config,
        'risk_config':      risk_config,
        'trigger_reason':   'replay',
        'cycle_interval':   body.timeframe,
        'triggered_at':     sim_now,
        'position_open':    False,
        'position_side':    None,
        'position_entry_price':        None,
        'position_size':               None,
        'position_unrealized_pnl_pct': None,
        'position_opened_at':          None,
        'original_reasoning':          None,
        'ohlcv_data':           None,
        'technical_indicators': None,
        'sentiment_data':       None,
        'news_data':            None,
        'market_context':       None,
        'data_fetch_errors':    [],
        'llm_signal':           None,
        'context_tokens':       None,
        'gate_passed':            False,
        'gate_rejection_reason':  None,
        'resolved_size':          None,
        'resolved_sl_price':      None,
        'resolved_tp_price':      None,
        'webhook_fired':   False,
        'webhook_status':  None,
        'order_id':        None,
        'signal_log_id':   None,
        # sim-specific
        'simulated_now':        sim_now,
        'backtest_run_id':      run_id,
        'sim_balance':          1000.0,
        'sim_pnl_today':        0.0,
        'replay_candle_window': candle_window,
        'sim_action':           None,
    }

    # 6. Run the graph
    graph = build_sim_graph()
    final_state = await graph.ainvoke(initial_state)

    # 7. Read back the signal log row to confirm triggered_at
    signal_log_id = final_state.get('signal_log_id')
    triggered_at_db: datetime | None = None
    if signal_log_id is not None:
        async with pool.acquire() as conn:
            triggered_at_db = await conn.fetchval(
                "SELECT triggered_at FROM tester.ai_signal_log WHERE id = $1",
                signal_log_id,
            )

    matches = (
        triggered_at_db is not None
        and abs((triggered_at_db - sim_now).total_seconds()) < 1
    )

    return {
        "backtest_run_id":              run_id,
        "candles_used":                 len(candle_window),
        "gate_passed":                  final_state.get('gate_passed'),
        "gate_rejection_reason":        final_state.get('gate_rejection_reason'),
        "signal_action":                (final_state.get('llm_signal') or {}).get('action'),
        "signal_log_id":                signal_log_id,
        "simulated_now":                sim_now.isoformat(),
        "triggered_at_in_db":           triggered_at_db.isoformat() if triggered_at_db else None,
        "triggered_at_matches_candle":  matches,
        "resolved_size":                final_state.get('resolved_size'),
        "resolved_sl_price":            final_state.get('resolved_sl_price'),
        "resolved_tp_price":            final_state.get('resolved_tp_price'),
    }
