"""
Wrapper around the vendored node_analyze.
When strategy_config['dry_signal_mode'] is True, returns a deterministic but
varied synthetic signal without spending LLM credits.

Dry-signal sequence (pure function of candle_ts → deterministic, reproducible):
  - No position open:
      every 12th absolute candle index  → open_long
      every 37th absolute candle index  → open_short   (only if not also every 12th)
      otherwise                         → hold
  - Position open for >= 13 candles     → close_long / close_short (matching side)
  - Position open for < 13 candles      → hold

Confidence is always 0.85 for open/close, above the default 0.72 guard threshold.
Do NOT touch the non-dry path below.
"""
import logging

from app._vendored.node_analyze import node_analyze
from app.graph.state import AgentState

logger = logging.getLogger(__name__)

_TF_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '8h': 28800, '1d': 86400,
}

_CLOSE_AFTER_CANDLES = 13   # close after this many candles held


def _varied_dry_signal(state: AgentState) -> dict:
    """
    Build a deterministic dry signal from candle state.

    Uses absolute candle index (epoch_seconds // tf_seconds) as the seed so
    the sequence is identical on every replay of the same date range.
    """
    simulated_now   = state.get('simulated_now')
    position_open   = state.get('position_open', False)
    position_side   = state.get('position_side')        # 'long' | 'short' | None
    position_opened_at = state.get('position_opened_at')
    timeframe       = state.get('cycle_interval', '1h')
    tf_secs         = _TF_SECONDS.get(timeframe, 3600)

    # ── Position open: decide whether to close ───────────────────────────────
    if position_open and position_opened_at is not None and simulated_now is not None:
        candles_held = int(
            (simulated_now.timestamp() - position_opened_at.timestamp()) / tf_secs
        )
        if candles_held >= _CLOSE_AFTER_CANDLES:
            action = 'close_long' if position_side == 'long' else 'close_short'
            return {
                'action':          action,
                'confidence':      0.85,
                'size_pct':        100.0,
                'stop_loss_pct':   0.0,
                'take_profit_pct': 0.0,
                'reasoning':       f'dry_signal_mode: close after {candles_held} candles',
            }
        # Hold until _CLOSE_AFTER_CANDLES is reached
        return {
            'action': 'hold', 'confidence': 0.50,
            'size_pct': 0.0, 'stop_loss_pct': 0.0, 'take_profit_pct': 0.0,
            'reasoning': f'dry_signal_mode: holding ({candles_held} candles)',
        }

    # ── No position: open on deterministic candle indices ────────────────────
    if simulated_now is not None:
        candle_idx = int(simulated_now.timestamp()) // tf_secs
        if candle_idx % 12 == 0:
            return {
                'action':          'open_long',
                'confidence':      0.85,
                'size_pct':        5.0,
                'stop_loss_pct':   2.0,
                'take_profit_pct': 4.0,
                'reasoning':       f'dry_signal_mode: open_long candle_idx={candle_idx}',
            }
        if candle_idx % 37 == 0:
            return {
                'action':          'open_short',
                'confidence':      0.85,
                'size_pct':        5.0,
                'stop_loss_pct':   2.0,
                'take_profit_pct': 4.0,
                'reasoning':       f'dry_signal_mode: open_short candle_idx={candle_idx}',
            }

    return {
        'action': 'hold', 'confidence': 0.50,
        'size_pct': 0.0, 'stop_loss_pct': 0.0, 'take_profit_pct': 0.0,
        'reasoning': 'dry_signal_mode: hold',
    }


async def node_analyze_sim(state: AgentState) -> AgentState:
    if state.get('strategy_config', {}).get('dry_signal_mode'):
        signal = _varied_dry_signal(state)
        logger.debug(
            "dry_signal_mode: action=%s simulated_now=%s position_open=%s",
            signal['action'], state.get('simulated_now'), state.get('position_open'),
        )
        return {**state, 'llm_signal': signal, 'context_tokens': 0}
    return await node_analyze(state)
