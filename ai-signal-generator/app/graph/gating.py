"""
Routing predicates for the LangGraph pipeline — shared between graph.py's
conditional edges and tests, so the gating logic has one source of truth.
"""


def should_skip_llm_no_range(state) -> bool:
    """
    True when the geometric_range template has no strong-fit pattern to trade and
    no open position to evaluate an exit for. The template's own instructions
    (db/migrations/036_geometric_range_template.sql) already say to output HOLD
    when fit_quality != 'strong' — this just makes that HOLD deterministic instead
    of paying for an LLM call that can only agree with the template.

    position_open is a hard carve-out: exit evaluation must never be skipped,
    regardless of the current geometry read.
    """
    sc = state['strategy_config']
    if sc.get('template_id') != 'geometric_range':
        return False
    if state.get('position_open'):
        return False
    gd = state.get('geometry_data') or {}
    return gd.get('fit_quality') != 'strong'
