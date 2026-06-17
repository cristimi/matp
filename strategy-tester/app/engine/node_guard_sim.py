"""
Simulation version of node_guard.

Differences from live node_guard:
1. Balance read from state['sim_balance'] — no HTTP call to order-executor.
2. Cooldown uses state['simulated_now'] (candle ts), NOT NOW().
3. Cooldown queries tester.ai_signal_log scoped to backtest_run_id.
4. Daily PnL read from state['sim_pnl_today'] — no DB lookup on public.strategies.
5. partial_close rejected (not supported in v1 sim).
"""
import logging
from datetime import timedelta

from app.database import get_pool
from app.graph.state import AgentState

logger = logging.getLogger(__name__)

_MIN_SL_TP_PCT = 0.05   # below this, SL/TP sits ~on entry (degenerate)
_MAX_SL_TP_PCT = 50.0   # above this is almost certainly hallucinated

_ACTION_COOLDOWN: dict[str, str | None] = {
    'open_long':     'cooldown_entry_minutes',
    'open_short':    'cooldown_entry_minutes',
    'partial_close': 'cooldown_entry_minutes',
    'adjust_stops':  'cooldown_stop_adj_minutes',
    'close_long':    None,
    'close_short':   None,
    'hold':          None,
}


def _reject(state: AgentState, reason: str) -> AgentState:
    return {**state, 'gate_passed': False, 'gate_rejection_reason': reason}


async def node_guard_sim(state: AgentState) -> AgentState:
    sc   = state['strategy_config']
    rc   = state['risk_config']
    pool = get_pool()

    # 1. LLM signal present
    if state.get('llm_signal') is None:
        return _reject(state, 'llm_failed')

    signal = state['llm_signal']
    action = signal['action']

    # 2. partial_close not supported in sim
    if action == 'partial_close':
        return _reject(state, 'partial_close_not_supported_in_sim')

    # 3. Hold / adjust_stops — not an error, just no dispatch
    if action in ('hold', 'adjust_stops'):
        return {**state, 'gate_passed': False, 'gate_rejection_reason': 'hold_or_adjust'}

    # 4. Confidence threshold
    threshold = float(sc.get('confidence_threshold') or 0.72)
    if signal['confidence'] < threshold:
        return _reject(state, 'confidence_below_threshold')

    # 5. Action coherence
    position_open = state.get('position_open', False)
    if action in ('open_long', 'open_short') and position_open:
        return _reject(state, 'position_already_open')
    if action in ('close_long', 'close_short') and not position_open:
        return _reject(state, 'no_position_to_close')

    # 6. Cooldown — uses simulated_now, scoped to backtest_run_id
    cooldown_key = _ACTION_COOLDOWN.get(action)
    if cooldown_key:
        _cv = sc.get(cooldown_key)
        cooldown_minutes = int(_cv) if _cv is not None else 240
        simulated_now    = state.get('simulated_now')
        run_id           = state.get('backtest_run_id')
        if simulated_now is not None and run_id is not None:
            try:
                # Compute cutoff in Python to avoid asyncpg type-inference ambiguity
                # with ($4 - make_interval(mins => $5)) where $4 could be inferred
                # as interval instead of timestamptz.
                cooldown_cutoff = simulated_now - timedelta(minutes=cooldown_minutes)
                async with pool.acquire() as conn:
                    last = await conn.fetchval(
                        """
                        SELECT triggered_at FROM tester.ai_signal_log
                        WHERE backtest_run_id = $1
                          AND strategy_id     = $2
                          AND proposed_action = $3
                          AND gate_passed     = TRUE
                          AND triggered_at   >= $4
                          AND triggered_at    < $5
                        ORDER BY triggered_at DESC
                        LIMIT 1
                        """,
                        run_id, state['strategy_id'], action,
                        cooldown_cutoff, simulated_now,
                    )
                if last is not None:
                    return _reject(state, 'cooldown_active')
            except Exception as exc:
                logger.warning("Sim cooldown check failed: %s", exc)

    # 7. Size resolution for opening actions (uses sim_balance)
    if action in ('open_long', 'open_short'):
        usdt_balance  = float(state.get('sim_balance') or 0.0)
        size_pct      = min(float(signal['size_pct']), float(rc.get('max_position_size_pct') or 5.0))
        usdt_alloc    = usdt_balance * size_pct / 100.0
        current_price = float((state.get('ohlcv_data') or {}).get('current_price') or 0)

        if current_price <= 0:
            return _reject(state, 'size_resolution_failed')

        base_qty = round(usdt_alloc / current_price, 8)
        sl_pct   = abs(float(signal['stop_loss_pct']))
        tp_pct   = abs(float(signal['take_profit_pct']))

        if not (_MIN_SL_TP_PCT <= sl_pct <= _MAX_SL_TP_PCT) or \
           not (_MIN_SL_TP_PCT <= tp_pct <= _MAX_SL_TP_PCT):
            logger.warning(
                "node_guard_sim reject %s: sl_pct=%s tp_pct=%s outside [%s, %s]",
                action, sl_pct, tp_pct, _MIN_SL_TP_PCT, _MAX_SL_TP_PCT,
            )
            return _reject(state, 'sl_tp_pct_out_of_range')

        if action == 'open_long':
            sl_price = round(current_price * (1 - sl_pct / 100.0), 4)
            tp_price = round(current_price * (1 + tp_pct / 100.0), 4)
        else:
            sl_price = round(current_price * (1 + sl_pct / 100.0), 4)
            tp_price = round(current_price * (1 - tp_pct / 100.0), 4)

        return {
            **state,
            'gate_passed':           True,
            'gate_rejection_reason': None,
            'resolved_size':         base_qty,
            'resolved_sl_price':     sl_price,
            'resolved_tp_price':     tp_price,
        }

    # 9. Close actions — use current position size from state
    resolved_size = state.get('position_size') or 0.01
    return {
        **state,
        'gate_passed':           True,
        'gate_rejection_reason': None,
        'resolved_size':         resolved_size,
        'resolved_sl_price':     None,
        'resolved_tp_price':     None,
    }
