"""
AI Signal Generator Service — FastAPI application entry point.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.database import init_db, get_pool, resolve_exchange_id
from app.graph.graph import build_graph
from app.prompt.builder import build_prompt, get_estimated_tokens
from app.scheduler import start_all_schedulers, stop_all_schedulers, AdaptiveScheduler
from app.event_watcher import start_all_event_watchers, start_event_watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _model_probe_loop():
    """Probe all provider models on startup, then refresh every 24 h."""
    from app.models_registry import probe_all_models, clear_cache
    try:
        logger.info("Model probe: starting initial verification for all providers")
        summary = await probe_all_models()
        logger.info("Model probe complete: %s", summary)
    except Exception as exc:
        logger.error("Initial model probe failed: %s", exc)
    while True:
        await asyncio.sleep(86_400)
        try:
            logger.info("Model probe: daily refresh")
            clear_cache()
            summary = await probe_all_models()
            logger.info("Model probe daily refresh complete: %s", summary)
        except Exception as exc:
            logger.error("Daily model probe failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    pool  = get_pool()
    graph = build_graph()

    schedulers    = await start_all_schedulers(pool, graph)
    watcher_tasks = await start_all_event_watchers(pool, graph, schedulers)

    probe_task = asyncio.create_task(_model_probe_loop(), name="model_probe_loop")

    app.state.schedulers    = schedulers
    app.state.watcher_tasks = watcher_tasks
    app.state.graph         = graph
    app.state.probe_task    = probe_task

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    probe_task.cancel()
    await stop_all_schedulers(schedulers)

    for task in watcher_tasks.values():
        task.cancel()
    if watcher_tasks:
        await asyncio.gather(*watcher_tasks.values(), return_exceptions=True)

    logger.info("AI Signal Generator shutdown complete")


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


# ── /internal/models ─────────────────────────────────────────────────────────

@app.get("/internal/models")
async def list_models(provider: str):
    try:
        from app.models_registry import get_available_models
        models = await get_available_models(provider)
        return {
            "provider":       provider,
            "models":         models,
            "key_configured": len(models) > 0 or provider == "anthropic",
        }
    except Exception as exc:
        logger.error("Failed to list models for %s: %s", provider, exc)
        return {"provider": provider, "models": [], "key_configured": False, "error": str(exc)}


@app.post("/internal/models/verify")
async def verify_models(provider: str | None = None, force: bool = False):
    """Re-probe models for a provider (or all providers). Runs in background."""
    from app.models_registry import probe_all_models, clear_cache
    async def _run():
        if force:
            clear_cache(provider)
        summary = await probe_all_models(provider=provider, force=False)
        logger.info("On-demand model probe complete: %s", summary)
    asyncio.create_task(_run())
    return {"status": "started", "provider": provider or "all", "force": force}


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
                a.dry_run,
                a.llm_provider, a.llm_model
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
        try:
            exchange_id = await resolve_exchange_id(conn, strategy["account_id"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # ── Build strategy_config dict ────────────────────────────────────────
    sc = _row_to_dict(strategy)
    sc['exchange_id'] = exchange_id

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
        'geometry_data':       None,
        'open_orders':         None,
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
        'resolved_limit_price':     None,
        'resolved_target_order_id': None,
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


# ── /internal/schedulers ─────────────────────────────────────────────────────

@app.get("/internal/schedulers")
async def list_schedulers():
    schedulers = getattr(app.state, 'schedulers', {})
    result = []
    for sid, sched in schedulers.items():
        result.append({
            'strategy_id':    sid,
            'running':        sched._running,
            'last_trigger':   sched._last_trigger.isoformat() if sched._last_trigger else None,
            'last_interval_s': sched._last_interval,
        })
    return {'schedulers': result, 'count': len(result)}


class TriggerSchedulerRequest(BaseModel):
    trigger_reason: str = 'manual'


@app.post("/internal/schedulers/{strategy_id}/trigger")
async def trigger_scheduler(strategy_id: str, body: TriggerSchedulerRequest):
    schedulers = getattr(app.state, 'schedulers', {})
    sched = schedulers.get(strategy_id)
    if not sched:
        raise HTTPException(status_code=404, detail=f"No running scheduler for strategy {strategy_id}")
    import asyncio
    asyncio.create_task(sched._trigger_cycle(body.trigger_reason))
    return {'strategy_id': strategy_id, 'trigger_reason': body.trigger_reason, 'status': 'queued'}


async def _reconcile_scheduler(strategy_id: str) -> dict:
    """Make the in-memory scheduler/watcher for `strategy_id` match its DB state.

    Should-run = strategy is enabled, not deleted, and has an ai_strategy_config row.
    Starts, reloads, or tears down the scheduler + its event watcher accordingly.
    """
    pool       = get_pool()
    graph      = app.state.graph
    schedulers = app.state.schedulers
    watchers   = app.state.watcher_tasks

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.id
            FROM strategies s
            JOIN ai_strategy_config a ON a.strategy_id = s.id
            WHERE s.id = $1
              AND s.enabled = true
              AND COALESCE(s.is_deleted, false) = false
            """,
            strategy_id,
        )
    should_run = row is not None
    running    = strategy_id in schedulers

    if should_run and not running:
        sched = AdaptiveScheduler(strategy_id, pool, graph)
        await sched.start()
        schedulers[strategy_id] = sched
        watchers[strategy_id]   = start_event_watcher(strategy_id, pool, graph, sched)
        logger.info("reconcile: started scheduler+watcher strategy=%s", strategy_id)
        return {"status": "started", "strategy_id": strategy_id}

    if should_run and running:
        schedulers[strategy_id].interrupt()
        logger.info("reconcile: reloaded (interrupted) strategy=%s", strategy_id)
        return {"status": "reloaded", "strategy_id": strategy_id}

    if (not should_run) and running:
        await schedulers[strategy_id].stop()
        del schedulers[strategy_id]
        task = watchers.pop(strategy_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("reconcile: stopped scheduler+watcher strategy=%s", strategy_id)
        return {"status": "stopped", "strategy_id": strategy_id}

    logger.info("reconcile: noop strategy=%s", strategy_id)
    return {"status": "noop", "strategy_id": strategy_id}


@app.post("/internal/schedulers/{strategy_id}/reconcile")
async def reconcile_scheduler(strategy_id: str):
    return await _reconcile_scheduler(strategy_id)


@app.post("/internal/schedulers/{strategy_id}/config-reload")
async def config_reload(strategy_id: str):
    result = await _reconcile_scheduler(strategy_id)
    return {"status": "ok", "strategy_id": strategy_id, "reconcile": result}


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
