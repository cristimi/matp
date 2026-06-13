"""
Simulation version of node_dispatch.

Key difference: writes triggered_at = state['simulated_now'] (candle ts),
NOT NOW(). No HTTP webhook is fired — the engine reads sim_action from state.
"""
import logging

from app.database import get_pool
from app.graph.state import AgentState

logger = logging.getLogger(__name__)


def _data_sources_used(sc: dict) -> list[str]:
    sources = []
    if sc.get('use_technical'):     sources.append('technical')
    if sc.get('use_fear_greed'):    sources.append('fear_greed')
    if sc.get('use_funding_rate'):  sources.append('funding_rate')
    if sc.get('use_open_interest'): sources.append('open_interest')
    if sc.get('use_news'):          sources.append('news')
    if sc.get('use_btc_dominance'): sources.append('btc_dominance')
    if sc.get('use_macro'):         sources.append('macro')
    return sources


async def node_dispatch_sim(state: AgentState) -> AgentState:
    pool          = get_pool()
    sc            = state['strategy_config']
    signal        = state.get('llm_signal') or {}
    action        = signal.get('action')
    confidence    = signal.get('confidence')
    reasoning     = signal.get('reasoning')
    simulated_now = state.get('simulated_now')      # ← candle timestamp, NOT NOW()
    run_id        = state.get('backtest_run_id')

    signal_log_id: int | None = None
    async with pool.acquire() as conn:
        signal_log_id = await conn.fetchval(
            """
            INSERT INTO tester.ai_signal_log (
                backtest_run_id, strategy_id, triggered_at, trigger_reason,
                cycle_interval, prompt_template, data_sources_used, context_tokens,
                proposed_action, confidence, reasoning,
                gate_passed, gate_rejection_reason, dry_run,
                llm_provider, llm_model
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            RETURNING id
            """,
            run_id,
            state['strategy_id'],
            simulated_now,
            state.get('trigger_reason', 'replay'),
            state.get('cycle_interval', '1h'),
            sc.get('template_id', 'trend_following'),
            _data_sources_used(sc),
            state.get('context_tokens'),
            action,
            confidence,
            reasoning,
            bool(state.get('gate_passed', False)),
            state.get('gate_rejection_reason'),
            True,                                       # always dry_run in sim
            sc.get('llm_provider', 'google'),
            sc.get('llm_model', 'gemini-2.5-flash'),
        )

    # Gate failed or non-actionable
    if not state.get('gate_passed') or action in ('hold', 'adjust_stops', 'partial_close'):
        return {**state, 'signal_log_id': signal_log_id, 'webhook_fired': False, 'sim_action': None}

    # Gate passed — signal engine to handle the fill (no HTTP call)
    logger.debug(
        "dispatch_sim: strategy=%s action=%s simulated_now=%s",
        state['strategy_id'], action, simulated_now,
    )
    return {
        **state,
        'signal_log_id': signal_log_id,
        'webhook_fired': True,
        'sim_action':    action,
    }
