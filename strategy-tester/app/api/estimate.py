"""
Cost estimation endpoint: POST /estimate-cost

Counts tokens locally (char/4 heuristic) by building a representative prompt
from the strategy's DB template and mock market data.
R3: no LLM provider calls are made — pure local arithmetic.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app._vendored.prompt_builder import build_prompt, get_estimated_tokens
from app.database import get_pool
from app.pricing import get_pricing

logger = logging.getLogger(__name__)
router = APIRouter()

_WARMUP_CANDLES = 200   # mirror backtest_engine._WARMUP_CANDLES

_TF_SECONDS: dict[str, int] = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1_800,
    '1h': 3_600, '2h': 7_200, '4h': 14_400, '8h': 28_800, '1d': 86_400,
}

# Typical output token count for a single LLM signal response (JSON)
_OUTPUT_TOKENS_PER_CYCLE = 350


class EstimateRequest(BaseModel):
    strategy_id:  str
    date_from:    date
    date_to:      date
    timeframe:    str = "1h"
    lookback_days: int = 90


def _estimate_candles(date_from: date, date_to: date,
                      lookback_days: int, timeframe: str) -> tuple[int, int]:
    """Return (total_candles, active_candles) using the same arithmetic as the engine."""
    tf_secs = _TF_SECONDS.get(timeframe, 3_600)
    fetch_from = datetime(date_from.year, date_from.month, date_from.day,
                          tzinfo=timezone.utc) - timedelta(days=lookback_days)
    fetch_to   = datetime(date_to.year, date_to.month, date_to.day,
                          tzinfo=timezone.utc)
    span_secs    = (fetch_to - fetch_from).total_seconds()
    total        = max(0, int(span_secs / tf_secs) + 1)
    active       = max(0, total - _WARMUP_CANDLES)
    return total, active


def _mock_state(strategy_config: dict, risk_config: dict, timeframe: str) -> dict:
    """Build a representative AgentState with mock market data for token estimation."""
    use_tech = bool(strategy_config.get('use_technical', True))
    return {
        'strategy_config':  strategy_config,
        'risk_config':      risk_config,
        'cycle_interval':   timeframe,
        'trigger_reason':   'scheduled',
        'position_open':    False,
        'position_side':    None,
        'position_entry_price':        None,
        'position_unrealized_pnl_pct': None,
        'position_opened_at':          None,
        'original_reasoning':          None,
        'ohlcv_data': {
            'current_price':       75_000.0,
            'price_change_24h_pct': 2.5,
            'price_change_7d_pct':  8.3,
        } if use_tech else None,
        'technical_indicators': {
            'rsi_14':               58.3,
            'rsi_interpretation':   'neutral',
            'macd_hist':            123.5,
            'macd_signal_bars':     2,
            'ema_cross_status':     'EMA50 above EMA200 (bullish)',
            'ema_50':               74_500,
            'ema_200':              72_000,
            'bb_interpretation':    'price in upper third of Bollinger Band',
            'vwap_deviation_pct':   0.8,
            'vwap_direction':       'above',
            'atr_14':               850.0,
            'atr_pct_of_price':     1.13,
            'volume_vs_avg_pct':    15.2,
            'support_1':            74_100,
            'resistance_1':         76_800,
        } if use_tech else None,
        'sentiment_data':   None,
        'news_data':        None,
        'market_context':   None,
        'data_fetch_errors': [],
    }


@router.post("/estimate-cost")
async def estimate_cost(body: EstimateRequest):
    if body.date_from >= body.date_to:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")

    pool = get_pool()

    # 1. Load strategy + configs
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.id, s.symbol,
                   aic.template_id, aic.llm_provider, aic.llm_model,
                   aic.use_technical, aic.indicators,
                   aic.confidence_threshold, aic.cooldown_entry_minutes,
                   aic.cooldown_stop_adj_minutes, aic.custom_instructions,
                   arc.max_position_size_pct, arc.max_concurrent_trades
            FROM tester.strategies s
            LEFT JOIN tester.ai_strategy_config aic ON aic.strategy_id = s.id
            LEFT JOIN tester.ai_risk_config arc     ON arc.strategy_id = s.id
            WHERE s.id = $1 AND s.is_deleted = false
            """,
            body.strategy_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {body.strategy_id}")

    sym          = (row['symbol'] or 'BTC-USDT').replace('/', '-')
    base, quote  = (sym.split('-', 1) + ['USDT'])[:2]
    provider     = row['llm_provider'] or 'google'
    model        = row['llm_model']    or 'gemini-2.5-flash'
    timeframe    = body.timeframe

    strategy_config = {
        'base_asset':             base,
        'quote_asset':            quote,
        'use_technical':          bool(row['use_technical'] if row['use_technical'] is not None else True),
        'indicators':             list(row['indicators']) if row['indicators'] else [],
        'template_id':            row['template_id'] or 'trend_following',
        'llm_provider':           provider,
        'llm_model':              model,
        'confidence_threshold':   float(row['confidence_threshold'] or 0.72),
        'cooldown_entry_minutes': int(row['cooldown_entry_minutes']) if row['cooldown_entry_minutes'] is not None else 240,
        'cooldown_stop_adj_minutes': int(row['cooldown_stop_adj_minutes']) if row['cooldown_stop_adj_minutes'] is not None else 30,
        'custom_instructions':    row['custom_instructions'],
        'dry_signal_mode':        False,
    }
    risk_config = {
        'max_position_size_pct': float(row['max_position_size_pct'] or 5.0),
        'max_concurrent_trades': int(row['max_concurrent_trades'] or 1),
    }

    # 2. Build representative prompt (DB call for template — NO LLM call, R3)
    state       = _mock_state(strategy_config, risk_config, timeframe)
    prompt      = await build_prompt(state, pool)
    input_toks  = get_estimated_tokens(prompt)
    output_toks = _OUTPUT_TOKENS_PER_CYCLE

    # 3. Estimate active candles
    total_candles, active_candles = _estimate_candles(
        body.date_from, body.date_to, body.lookback_days, timeframe
    )

    # 4. Pricing lookup (local table — no provider call, R3)
    input_per_1m, output_per_1m = get_pricing(provider, model)

    total_input_tokens  = active_candles * input_toks
    total_output_tokens = active_candles * output_toks

    estimated_cost_usd = (
        total_input_tokens  / 1_000_000 * input_per_1m +
        total_output_tokens / 1_000_000 * output_per_1m
    )

    return {
        'strategy_id':    body.strategy_id,
        'provider':       provider,
        'model':          model,
        'date_from':      str(body.date_from),
        'date_to':        str(body.date_to),
        'timeframe':      timeframe,
        'lookback_days':  body.lookback_days,
        'total_candles':         total_candles,
        'warmup_candles':        _WARMUP_CANDLES,
        'active_candles':        active_candles,
        'tokens_per_cycle': {
            'input':  input_toks,
            'output': output_toks,
        },
        'total_tokens': {
            'input':  total_input_tokens,
            'output': total_output_tokens,
        },
        'pricing': {
            'input_per_1m_usd':  input_per_1m,
            'output_per_1m_usd': output_per_1m,
        },
        'estimated_cost_usd': round(estimated_cost_usd, 6),
        'note': (
            'Estimate uses char/4 token heuristic on a representative prompt. '
            'Actual cost varies with live candle data size and output length.'
        ),
    }
