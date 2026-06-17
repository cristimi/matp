import logging
from datetime import datetime, timezone, timedelta

from app.config import settings
from app.database import get_pool
from app.graph.state import AgentState

logger = logging.getLogger(__name__)

_MIN_SL_TP_PCT = 0.05   # below this, SL/TP sits ~on entry (degenerate)
_MAX_SL_TP_PCT = 50.0   # above this is almost certainly hallucinated

# Maps action → strategy_config cooldown key; None means no cooldown
_ACTION_COOLDOWN: dict[str, str | None] = {
    'open_long':    'cooldown_entry_minutes',
    'open_short':   'cooldown_entry_minutes',
    'partial_close': 'cooldown_entry_minutes',
    'adjust_stops': 'cooldown_stop_adj_minutes',
    'close_long':   None,
    'close_short':  None,
    'hold':         None,
}


def _reject(state: AgentState, reason: str) -> AgentState:
    return {**state, 'gate_passed': False, 'gate_rejection_reason': reason}


async def node_guard(state: AgentState) -> AgentState:
    sc   = state['strategy_config']
    rc   = state['risk_config']
    pool = get_pool()

    # ── 1. LLM signal present ────────────────────────────────────────────
    if state.get('llm_signal') is None:
        return _reject(state, 'llm_failed')

    signal = state['llm_signal']
    action = signal['action']

    # ── 2. Hold — no webhook ─────────────────────────────────────────────
    if action == 'hold':
        return {**state, 'gate_passed': False, 'gate_rejection_reason': 'hold_or_adjust'}

    # ── 3. Confidence threshold ──────────────────────────────────────────
    threshold = float(sc.get('confidence_threshold') or 0.72)
    if signal['confidence'] < threshold:
        return _reject(state, 'confidence_below_threshold')

    # ── 4. Action coherence ──────────────────────────────────────────────
    position_open = state.get('position_open', False)
    if action in ('open_long', 'open_short') and position_open:
        return _reject(state, 'position_already_open')
    if action in ('close_long', 'close_short', 'partial_close') and not position_open:
        return _reject(state, 'no_position_to_close')

    # ── 5. Cooldown ──────────────────────────────────────────────────────
    cooldown_key = _ACTION_COOLDOWN.get(action)
    if cooldown_key:
        cooldown_minutes = int(sc.get(cooldown_key) or 240)
        try:
            async with pool.acquire() as conn:
                last = await conn.fetchval(
                    """
                    SELECT triggered_at FROM ai_signal_log
                    WHERE strategy_id = $1
                      AND proposed_action = $2
                      AND gate_passed = TRUE
                      AND triggered_at >= $3
                    ORDER BY triggered_at DESC
                    LIMIT 1
                    """,
                    state['strategy_id'],
                    action,
                    datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes),
                )
            if last is not None:
                return _reject(state, 'cooldown_active')
        except Exception as exc:
            logger.warning("Cooldown check failed: %s", exc)

    # ── adjust_stops: resolve new prices from signal ─────────────────────
    if action == 'adjust_stops':
        new_tp = signal.get('new_tp_price')
        new_sl = signal.get('new_sl_price')
        if new_tp is None and new_sl is None:
            return _reject(state, 'adjust_stops_no_prices')
        return {
            **state,
            'gate_passed':           True,
            'gate_rejection_reason': None,
            'resolved_size':         None,
            'resolved_sl_price':     float(new_sl) if new_sl is not None else None,
            'resolved_tp_price':     float(new_tp) if new_tp is not None else None,
        }

    # ── Size resolution (opening actions) ───────────────────────────────
    if action in ('open_long', 'open_short'):
        try:
            leverage      = int(sc.get('default_leverage') or 1)
            margin        = float(sc.get('margin_per_trade') or 5.0)
            current_price = float((state.get('ohlcv_data') or {}).get('current_price') or 0)

            if current_price <= 0:
                return _reject(state, 'size_resolution_failed')

            base_qty = round((margin * leverage) / current_price, 4)
            sl_pct   = abs(float(signal['stop_loss_pct']))
            tp_pct   = abs(float(signal['take_profit_pct']))

            if not (_MIN_SL_TP_PCT <= sl_pct <= _MAX_SL_TP_PCT) or \
               not (_MIN_SL_TP_PCT <= tp_pct <= _MAX_SL_TP_PCT):
                logger.warning(
                    "node_guard reject %s: sl_pct=%s tp_pct=%s outside [%s, %s]",
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

        except Exception as exc:
            logger.error("Size resolution failed: %s", exc)
            return _reject(state, 'size_resolution_failed')

    # ── Partial close: size from position_size * size_pct ───────────────
    if action == 'partial_close':
        position_size = state.get('position_size')
        if not position_size:
            return _reject(state, 'size_resolution_failed')
        size_pct = float(signal.get('size_pct') or 50.0)
        resolved_size = round(float(position_size) * size_pct / 100.0, 6)
        resolved_size = min(resolved_size, float(position_size))
        if resolved_size <= 0:
            return _reject(state, 'size_resolution_failed')
        return {
            **state,
            'gate_passed':           True,
            'gate_rejection_reason': None,
            'resolved_size':         resolved_size,
            'resolved_sl_price':     None,
            'resolved_tp_price':     None,
        }

    # ── Full close actions — use full position size ──────────────────────
    resolved_size = state.get('position_size') or 0.01
    return {
        **state,
        'gate_passed':           True,
        'gate_rejection_reason': None,
        'resolved_size':         resolved_size,
        'resolved_sl_price':     None,
        'resolved_tp_price':     None,
    }
