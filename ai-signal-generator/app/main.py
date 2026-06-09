"""
AI Signal Generator Service — FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.database import init_db, get_pool
from app.prompt.builder import build_prompt, get_estimated_tokens

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="MATP AI Signal Generator",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-signal-generator"}


# ── /internal/preview-prompt ─────────────────────────────────────────────────

class PreviewPromptRequest(BaseModel):
    strategy_id: str
    mock_state: dict


@app.post("/internal/preview-prompt")
async def internal_preview_prompt(body: PreviewPromptRequest):
    try:
        pool   = get_pool()
        prompt = await build_prompt(body.mock_state, pool)
        return {
            "prompt":           prompt,
            "estimated_tokens": get_estimated_tokens(prompt),
        }
    except Exception as exc:
        logger.error("preview-prompt error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── /internal/trigger ────────────────────────────────────────────────────────

class TriggerRequest(BaseModel):
    strategy_id:    str
    trigger_reason: str = "manual_test"


@app.post("/internal/trigger")
async def internal_trigger(body: TriggerRequest):
    from app.graph.graph import build_graph

    pool        = get_pool()
    strategy_id = body.strategy_id

    async with pool.acquire() as conn:
        strategy = await conn.fetchrow(
            """
            SELECT
                s.id, s.name, s.symbol, s.webhook_secret, s.account_id,
                s.platform, s.default_leverage, s.margin_mode, s.pnl_today, s.enabled,
                a.interval_no_position, a.interval_position_open, a.interval_at_risk,
                a.at_risk_threshold_pct,
                a.use_technical, a.use_fear_greed, a.use_funding_rate, a.use_open_interest,
                a.use_news, a.use_btc_dominance, a.use_macro,
                a.indicators, a.lookback_days, a.confidence_threshold,
                a.cooldown_entry_minutes, a.cooldown_increase_minutes,
                a.cooldown_stop_adj_minutes, a.template_id, a.custom_instructions,
                a.dry_run, a.emergency_exit_pct
            FROM strategies s
            JOIN ai_strategy_config a ON a.strategy_id = s.id
            WHERE s.id = $1
            """,
            strategy_id,
        )
        if not strategy:
            raise HTTPException(
                status_code=404,
                detail="Strategy not found or missing ai_strategy_config row",
            )

        risk_row = await conn.fetchrow(
            "SELECT * FROM ai_risk_config WHERE strategy_id = $1",
            strategy_id,
        )
        position = await conn.fetchrow(
            """
            SELECT side, entry_price, size, opened_at, pnl_unrealized
            FROM strategy_positions
            WHERE strategy_id = $1 AND status = 'open'
            ORDER BY opened_at DESC
            LIMIT 1
            """,
            strategy_id,
        )

    # ── Build strategy_config dict ────────────────────────────────────────
    sc = _row_to_dict(strategy)

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

    # Force dry_run for this endpoint
    sc['dry_run'] = True

    # ── Build risk_config dict ────────────────────────────────────────────
    rc = _row_to_dict(risk_row) if risk_row else {
        'max_position_size_pct': 5.0,
        'max_daily_loss_pct':    3.0,
        'max_drawdown_pct':      8.0,
        'max_concurrent_trades': 1,
    }

    # ── Determine cycle_interval ──────────────────────────────────────────
    position_open = position is not None
    cycle_interval = sc.get('interval_no_position', '4h')
    if position_open:
        cycle_interval = sc.get('interval_position_open', '15m')

    # ── Build initial AgentState ──────────────────────────────────────────
    pos_dict = _row_to_dict(position) if position else {}
    initial_state: dict = {
        'strategy_id':    strategy_id,
        'strategy_config': sc,
        'risk_config':    rc,
        'trigger_reason': body.trigger_reason,
        'cycle_interval': cycle_interval,
        'triggered_at':   datetime.now(timezone.utc),
        'position_open':  position_open,
        'position_side':  pos_dict.get('side'),
        'position_entry_price': _maybe_float(pos_dict.get('entry_price')),
        'position_size':  _maybe_float(pos_dict.get('size')),
        'position_unrealized_pnl_pct': None,
        'position_opened_at': pos_dict.get('opened_at'),
        'original_reasoning': None,
        'ohlcv_data':          None,
        'technical_indicators': None,
        'sentiment_data':      None,
        'news_data':           None,
        'market_context':      None,
        'data_fetch_errors':   [],
        'llm_signal':    None,
        'context_tokens': None,
        'gate_passed':           False,
        'gate_rejection_reason': None,
        'resolved_size':         None,
        'resolved_sl_price':     None,
        'resolved_tp_price':     None,
        'webhook_fired':  False,
        'webhook_status': None,
        'order_id':       None,
        'signal_log_id':  None,
    }

    graph = build_graph()
    final = await graph.ainvoke(initial_state)

    return {
        'signal_log_id':          final.get('signal_log_id'),
        'proposed_action':        (final.get('llm_signal') or {}).get('action'),
        'confidence':             (final.get('llm_signal') or {}).get('confidence'),
        'gate_passed':            final.get('gate_passed'),
        'gate_rejection_reason':  final.get('gate_rejection_reason'),
        'webhook_fired':          final.get('webhook_fired', False),
        'dry_run':                True,
        'data_fetch_errors':      final.get('data_fetch_errors', []),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    d = dict(row)
    # Convert Decimal/numeric DB types to plain float for JSON serialisability
    for k, v in d.items():
        if v is not None and not isinstance(v, (int, float, bool, str, list, dict, datetime)):
            try:
                d[k] = float(v)
            except (TypeError, ValueError):
                pass
    return d


def _maybe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
