"""
Unit tests for should_skip_llm_no_range (app/graph/gating.py) — the Phase 3
predicate that lets the geometric_range template skip the LLM entirely when
there's no tradeable-fit (strong/moderate) range to trade and no open position
to evaluate an exit for.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.graph.gating import should_skip_llm_no_range


def _state(template_id: str, position_open: bool, geometry_data,
           open_orders=None) -> dict:
    return {
        'strategy_config': {'template_id': template_id},
        'position_open':   position_open,
        'geometry_data':   geometry_data,
        'open_orders':     open_orders,
    }


_RESTING = [{'order_id': '56947163834', 'side': 'buy', 'price': 1821.9,
             'status': 'resting'}]


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


def test_geometric_range_moderate_fit_does_not_skip():
    # Moderate fits reach the LLM; the template trades them with stricter
    # touch counts and a lower confidence cap (migration 051).
    gd = {'shape': 'descending_channel', 'fit_quality': 'moderate'}
    assert should_skip_llm_no_range(_state('geometric_range', False, gd)) is False


def test_geometric_range_resting_order_weak_fit_does_not_skip():
    # Safety carve-out: a resting limit still needs Phase 3/4/5 management
    # (re-fit amend, apex cancel, breakout cancel) and the LLM is the only
    # thing that can cancel or amend it. eth-ai-34d2 sat in exactly this
    # state — two limits resting on a dissolved channel — for 8 straight
    # cycles on 2026-07-25.
    gd = {'shape': 'no_pattern', 'fit_quality': 'weak'}
    assert should_skip_llm_no_range(
        _state('geometric_range', False, gd, open_orders=_RESTING)) is False


def test_geometric_range_no_resting_orders_still_skips():
    # The token-saving case is untouched: empty list and None (fetch not
    # requested) both fall through to the weak-fit skip.
    gd = {'shape': 'no_pattern', 'fit_quality': 'weak'}
    assert should_skip_llm_no_range(
        _state('geometric_range', False, gd, open_orders=[])) is True
    assert should_skip_llm_no_range(
        _state('geometric_range', False, gd, open_orders=None)) is True


def test_non_geometric_range_template_does_not_skip():
    gd = {'shape': 'no_pattern', 'fit_quality': 'weak'}
    assert should_skip_llm_no_range(_state('trend_following', False, gd)) is False
