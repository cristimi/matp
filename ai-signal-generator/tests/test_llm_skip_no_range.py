"""
Unit tests for should_skip_llm_no_range (app/graph/gating.py) — the Phase 3
predicate that lets the geometric_range template skip the LLM entirely when
there's no strong-fit range to trade and no open position to evaluate an exit for.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.graph.gating import should_skip_llm_no_range


def _state(template_id: str, position_open: bool, geometry_data) -> dict:
    return {
        'strategy_config': {'template_id': template_id},
        'position_open':   position_open,
        'geometry_data':   geometry_data,
    }


def test_geometric_range_no_position_weak_fit_skips():
    gd = {'shape': 'no_pattern', 'fit_quality': 'weak'}
    assert should_skip_llm_no_range(_state('geometric_range', False, gd)) is True


def test_geometric_range_no_position_no_geometry_data_skips():
    assert should_skip_llm_no_range(_state('geometric_range', False, {})) is True
    assert should_skip_llm_no_range(_state('geometric_range', False, None)) is True


def test_geometric_range_position_open_weak_fit_does_not_skip():
    # Safety carve-out: never skip exit evaluation, regardless of geometry.
    gd = {'shape': 'no_pattern', 'fit_quality': 'weak'}
    assert should_skip_llm_no_range(_state('geometric_range', True, gd)) is False


def test_geometric_range_strong_fit_does_not_skip():
    gd = {'shape': 'horizontal_channel', 'fit_quality': 'strong'}
    assert should_skip_llm_no_range(_state('geometric_range', False, gd)) is False


def test_non_geometric_range_template_does_not_skip():
    gd = {'shape': 'no_pattern', 'fit_quality': 'weak'}
    assert should_skip_llm_no_range(_state('trend_following', False, gd)) is False
