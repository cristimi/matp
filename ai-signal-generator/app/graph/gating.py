"""
Routing predicates for the LangGraph pipeline — shared between graph.py's
conditional edges and tests, so the gating logic has one source of truth.
"""


def should_skip_llm_no_range(state) -> bool:
    """
    True when the geometric_range template has no tradeable-fit pattern (strong or
    moderate) and no open position to evaluate an exit for. The template's own
    instructions (db/migrations/036_geometric_range_template.sql, amended by 051)
    already say to output HOLD when fit_quality is 'weak' — this just makes that
    HOLD deterministic instead of paying for an LLM call that can only agree with
    the template. Moderate fits go through: the template trades them with stricter
    touch counts and a lower confidence cap.

    position_open is a hard carve-out: exit evaluation must never be skipped,
    regardless of the current geometry read.

    open_orders is the same kind of carve-out, added later: this predicate was
    written (2026-07-06) before the resting-limit workflow existed (2026-07-08),
    so it only ever protected open positions. A resting limit still needs the
    template's Phase 3/4/5 management — re-fit amend, apex cancel, breakout
    cancel — and the LLM is the ONLY thing that can cancel or amend one (the
    listener's reconciler syncs status; there is no TTL or stale-order sweep).
    Skipping the cycle stranded orders on structure that no longer existed:
    147 of 331 skipped cycles had a resting order at the time, eth-ai-34d2
    holding two of them across 8 straight cycles on 2026-07-25.
    """
    sc = state['strategy_config']
    if sc.get('template_id') != 'geometric_range':
        return False
    if state.get('position_open'):
        return False
    if state.get('open_orders'):
        return False
    gd = state.get('geometry_data') or {}
    return gd.get('fit_quality') not in ('strong', 'moderate')
