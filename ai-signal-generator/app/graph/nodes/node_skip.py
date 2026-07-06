import logging

from app.graph.state import AgentState

logger = logging.getLogger(__name__)


async def node_skip_geometry(state: AgentState) -> AgentState:
    """
    Deterministic-HOLD terminal for should_skip_llm_no_range: no strong-fit
    geometric range to trade and no open position, so the LLM call is skipped
    entirely rather than run just to confirm the template's own HOLD rule.
    """
    gd          = state.get('geometry_data') or {}
    fit_quality = gd.get('fit_quality')
    shape       = gd.get('shape')

    reasoning = (
        "Geometric Range & Breakout template requires a strong-fit range/pattern; "
        f"none detected (fit_quality={fit_quality}, shape={shape}). "
        "LLM skipped to conserve tokens; auto-HOLD."
    )

    logger.info(
        "strategy=%s geometric_range: no strong-fit range (fit_quality=%s shape=%s) "
        "— skipping LLM, auto-HOLD",
        state.get('strategy_id'), fit_quality, shape,
    )

    return {
        **state,
        'llm_signal': {
            'action':     'hold',
            'confidence': None,
            'reasoning':  reasoning,
        },
        'gate_passed':           False,
        'gate_rejection_reason': 'no_range_llm_skipped',
        'context_tokens':        0,
    }
