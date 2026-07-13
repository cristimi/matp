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
    'open_long':         'cooldown_entry_minutes',
    'open_short':        'cooldown_entry_minutes',
    'place_limit_long':  'cooldown_entry_minutes',
    'place_limit_short': 'cooldown_entry_minutes',
    'partial_close':     'cooldown_entry_minutes',
    'adjust_stops':      'cooldown_stop_adj_minutes',
    'close_long':        None,
    'close_short':       None,
    'cancel_order':      None,
    'amend_order':       None,
    'hold':              None,
}


def _reject(state: AgentState, reason: str) -> AgentState:
    return {**state, 'gate_passed': False, 'gate_rejection_reason': reason}


def _resolve_entry_sizing(sc: dict, entry_price: float, sl_pct: float) -> tuple[float, dict]:
    """
    Position size (base qty) for an opening action, plus audit metadata.

    margin mode (default): notional = margin_per_trade × leverage — the
    historical behavior. The $ lost at the stop is then sl_pct × notional,
    which with structural (tight) stops is typically only 10-25% of the
    allocated margin.

    risk mode: notional = risk_per_trade / sl_frac, so a stop-out loses
    ≈ risk_per_trade dollars regardless of how tight the LLM's stop is.
    Hard-capped at margin_per_trade × leverage — margin_per_trade becomes
    the collateral ceiling, and the order-listener's independent margin
    clamp enforces the exact same bound downstream. When the cap binds,
    the effective risk drops below target (flagged in the metadata).

    sl_pct must already be validated within [_MIN_SL_TP_PCT, _MAX_SL_TP_PCT]
    (guards the risk-mode division).
    """
    leverage     = int(sc.get('default_leverage') or 1)
    margin_cap   = float(sc.get('margin_per_trade') or 5.0)
    cap_notional = margin_cap * leverage
    sl_frac      = sl_pct / 100.0

    if sc.get('sizing_mode') == 'risk' and sc.get('risk_per_trade'):
        target_risk     = float(sc['risk_per_trade'])
        target_notional = target_risk / sl_frac
        notional        = min(target_notional, cap_notional)
        clamped         = target_notional > cap_notional
        meta = {
            'sizing_mode':        'risk',
            'target_risk_usd':    round(target_risk, 2),
            'effective_risk_usd': round(notional * sl_frac, 2),
            'margin_usd':         round(notional / leverage, 2),
            'risk_clamped_by_margin_cap': clamped,
        }
        if clamped:
            logger.warning(
                "risk sizing clamped: target risk $%.2f needs notional %.2f but "
                "margin cap allows %.2f (margin_per_trade=%.2f × lev=%d) — "
                "effective risk $%.2f",
                target_risk, target_notional, cap_notional, margin_cap, leverage,
                notional * sl_frac,
            )
    else:
        notional = cap_notional
        meta = {
            'sizing_mode':    'margin',
            'margin_usd':     round(margin_cap, 2),
            'risk_at_sl_usd': round(notional * sl_frac, 2),
        }

    qty = round(notional / entry_price, 4)
    logger.info(
        "sizing: mode=%s qty=%s notional=%.2f margin=%.2f lev=%d sl_pct=%.3f risk_at_sl=%.2f",
        meta['sizing_mode'], qty, notional, notional / leverage, leverage,
        sl_pct, notional * sl_frac,
    )
    return qty, meta


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
    if action in ('open_long', 'open_short', 'place_limit_long', 'place_limit_short') and position_open:
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
        # Side validation: absolute prices from the LLM used to pass through
        # unchecked — a TP below current price on a long (or SL above it)
        # closes the position instantly at a loss the moment it lands.
        pos_side      = state.get('position_side')
        current_price = float((state.get('ohlcv_data') or {}).get('current_price') or 0)
        if pos_side in ('long', 'short') and current_price > 0:
            if pos_side == 'long':
                wrong = ((new_sl is not None and float(new_sl) >= current_price)
                         or (new_tp is not None and float(new_tp) <= current_price))
            else:
                wrong = ((new_sl is not None and float(new_sl) <= current_price)
                         or (new_tp is not None and float(new_tp) >= current_price))
            if wrong:
                logger.warning(
                    "node_guard reject adjust_stops: wrong-side stop for %s @ %s (sl=%s tp=%s)",
                    pos_side, current_price, new_sl, new_tp,
                )
                return _reject(state, 'stop_wrong_side')
        return {
            **state,
            'gate_passed':           True,
            'gate_rejection_reason': None,
            'resolved_size':         None,
            'resolved_sl_price':     float(new_sl) if new_sl is not None else None,
            'resolved_tp_price':     float(new_tp) if new_tp is not None else None,
        }

    # ── place_limit_long / place_limit_short: resting entry at a boundary ─
    if action in ('place_limit_long', 'place_limit_short'):
        try:
            limit_price = signal.get('limit_price')
            if limit_price is None or float(limit_price) <= 0:
                return _reject(state, 'limit_price_missing')
            limit_price = float(limit_price)

            # Don't stack a second resting entry limit on the same side —
            # the range-working model keeps at most one resting order per side.
            side_wanted = 'buy' if action == 'place_limit_long' else 'sell'
            for o in (state.get('open_orders') or []):
                if o.get('side') == side_wanted:
                    return _reject(state, 'duplicate_resting_order_same_side')

            sl_pct = abs(float(signal['stop_loss_pct']))
            tp_pct = abs(float(signal['take_profit_pct']))
            if not (_MIN_SL_TP_PCT <= sl_pct <= _MAX_SL_TP_PCT) or \
               not (_MIN_SL_TP_PCT <= tp_pct <= _MAX_SL_TP_PCT):
                logger.warning(
                    "node_guard reject %s: sl_pct=%s tp_pct=%s outside [%s, %s]",
                    action, sl_pct, tp_pct, _MIN_SL_TP_PCT, _MAX_SL_TP_PCT,
                )
                return _reject(state, 'sl_tp_pct_out_of_range')

            base_qty, sizing_meta = _resolve_entry_sizing(sc, limit_price, sl_pct)

            if action == 'place_limit_long':
                sl_price = round(limit_price * (1 - sl_pct / 100.0), 4)
                tp_price = round(limit_price * (1 + tp_pct / 100.0), 4)
            else:
                sl_price = round(limit_price * (1 + sl_pct / 100.0), 4)
                tp_price = round(limit_price * (1 - tp_pct / 100.0), 4)

            return {
                **state,
                'gate_passed':              True,
                'gate_rejection_reason':    None,
                'resolved_size':            base_qty,
                'resolved_sl_price':        sl_price,
                'resolved_tp_price':        tp_price,
                'resolved_limit_price':     limit_price,
                'resolved_target_order_id': None,
                'sizing_meta':              sizing_meta,
            }

        except Exception as exc:
            logger.error("Limit size resolution failed: %s", exc)
            return _reject(state, 'size_resolution_failed')

    # ── cancel_order / amend_order: manage a resting order, no sizing ─────
    if action in ('cancel_order', 'amend_order'):
        target_order_id = signal.get('target_order_id')
        if not target_order_id:
            return _reject(state, 'target_order_id_missing')

        resolved_limit_price = None
        new_sl = new_tp = None
        if action == 'amend_order':
            limit_price = signal.get('limit_price')
            if limit_price is None or float(limit_price) <= 0:
                return _reject(state, 'amend_missing_price')
            resolved_limit_price = float(limit_price)
            # Side validation for the carried SL/TP relative to the NEW limit
            # price: an amended buy limit needs sl < limit < tp (sell mirrored).
            # This is the hole the 2026-07-10 ETH trade went through — an
            # amended long ended up with its TP below its own entry price.
            order_side = next(
                (o.get('side') for o in (state.get('open_orders') or [])
                 if str(o.get('order_id')) == str(target_order_id)),
                None,
            )
            amend_sl = signal.get('new_sl_price')
            amend_tp = signal.get('new_tp_price')
            if order_side in ('buy', 'sell'):
                if order_side == 'buy':
                    wrong = ((amend_sl is not None and float(amend_sl) >= resolved_limit_price)
                             or (amend_tp is not None and float(amend_tp) <= resolved_limit_price))
                else:
                    wrong = ((amend_sl is not None and float(amend_sl) <= resolved_limit_price)
                             or (amend_tp is not None and float(amend_tp) >= resolved_limit_price))
                if wrong:
                    logger.warning(
                        "node_guard reject amend_order: wrong-side stop for %s limit %s (sl=%s tp=%s)",
                        order_side, resolved_limit_price, amend_sl, amend_tp,
                    )
                    return _reject(state, 'stop_wrong_side')
            # Carry the re-fitted SL/TP the LLM computed for the new limit price —
            # dropping these here (as before) leaves the order's stored tp_price/
            # sl_price frozen at whatever the ORIGINAL placement set, which drifts
            # further from the entry with every amend and can end up on the wrong
            # side of price by the time the order actually fills (see
            # .gemini/reports/amend-order-stale-tp-sl-fix.md).
            new_sl = signal.get('new_sl_price')
            new_tp = signal.get('new_tp_price')

        return {
            **state,
            'gate_passed':              True,
            'gate_rejection_reason':    None,
            'resolved_size':            None,
            'resolved_sl_price':        float(new_sl) if new_sl is not None else None,
            'resolved_tp_price':        float(new_tp) if new_tp is not None else None,
            'resolved_limit_price':     resolved_limit_price,
            'resolved_target_order_id': str(target_order_id),
        }

    # ── Size resolution (opening actions) ───────────────────────────────
    if action in ('open_long', 'open_short'):
        try:
            current_price = float((state.get('ohlcv_data') or {}).get('current_price') or 0)

            if current_price <= 0:
                return _reject(state, 'size_resolution_failed')

            sl_pct = abs(float(signal['stop_loss_pct']))
            tp_pct = abs(float(signal['take_profit_pct']))

            if not (_MIN_SL_TP_PCT <= sl_pct <= _MAX_SL_TP_PCT) or \
               not (_MIN_SL_TP_PCT <= tp_pct <= _MAX_SL_TP_PCT):
                logger.warning(
                    "node_guard reject %s: sl_pct=%s tp_pct=%s outside [%s, %s]",
                    action, sl_pct, tp_pct, _MIN_SL_TP_PCT, _MAX_SL_TP_PCT,
                )
                return _reject(state, 'sl_tp_pct_out_of_range')

            base_qty, sizing_meta = _resolve_entry_sizing(sc, current_price, sl_pct)

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
                'sizing_meta':           sizing_meta,
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
