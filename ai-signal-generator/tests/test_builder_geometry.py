"""
Unit tests for builder._render_geometry — the GEOMETRIC PATTERN section renderer.

Covers the Phase 2 behavior: any use_geometry-on cycle with geometry_data present
now renders a GEOMETRIC PATTERN section, even when there's no reliable pattern —
labeled honestly as such, with position_in_range_pct flagged unreliable — rather
than being silently dropped. Named shapes and strong unclassified fits are
unaffected. The only remaining empty case is no geometry data / geometry disabled.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.prompt.builder import _render_geometry


def _state(geometry_data: dict, use_geometry: bool = True) -> dict:
    return {
        'strategy_config': {'use_geometry': use_geometry},
        'geometry_data': geometry_data,
    }


def test_no_pattern_weak_renders_honest_no_reliable_pattern_block():
    gd = {
        'shape': 'no_pattern', 'fit_quality': 'weak',
        'upper_boundary': 0.0, 'lower_boundary': 0.0,
        'upper_touches': 0, 'lower_touches': 0,
        'convergence_pct_per_bar': 0.0, 'pattern_age_bars': 0,
        'position_in_range_pct': 50.0,
    }
    rendered = _render_geometry(_state(gd))
    assert rendered != ''
    assert 'No Reliable Pattern' in rendered
    assert 'Fit Quality:          weak' in rendered
    assert 'Upper Boundary:       0.0' in rendered
    assert 'Lower Boundary:       0.0' in rendered
    assert 'UNRELIABLE' in rendered
    assert '50.0%  (UNRELIABLE' in rendered


def test_no_pattern_strong_is_surfaced_as_unclassified():
    gd = {
        'shape': 'no_pattern', 'fit_quality': 'strong',
        'upper_boundary': 121.85, 'lower_boundary': 83.68,
        'upper_touches': 5, 'lower_touches': 5,
        'convergence_pct_per_bar': -0.2073, 'pattern_age_bars': 58,
        'position_in_range_pct': 71.43,
    }
    rendered = _render_geometry(_state(gd))
    assert rendered != ''
    assert 'Unclassified Structure' in rendered
    assert 'No Reliable Pattern' not in rendered
    assert 'UNRELIABLE' not in rendered
    assert 'Fit Quality:          strong' in rendered
    assert 'Upper Boundary:       121.85' in rendered
    assert 'Divergence Rate:      -0.2073' in rendered
    assert 'Position in Range:    71.43%  (0=at lower boundary, 100=at upper)' in rendered


def test_named_shape_renders_title_not_unclassified():
    gd = {
        'shape': 'broadening', 'fit_quality': 'strong',
        'upper_boundary': 121.85, 'lower_boundary': 83.68,
        'upper_touches': 5, 'lower_touches': 5,
        'convergence_pct_per_bar': -0.2073, 'pattern_age_bars': 58,
        'position_in_range_pct': 71.43,
    }
    rendered = _render_geometry(_state(gd))
    assert 'Detected Shape:       Broadening' in rendered
    assert 'Unclassified' not in rendered
    assert 'No Reliable Pattern' not in rendered
    assert 'UNRELIABLE' not in rendered


def test_use_geometry_off_is_omitted():
    gd = {'shape': 'no_pattern', 'fit_quality': 'strong'}
    assert _render_geometry(_state(gd, use_geometry=False)) == ''


def test_empty_geometry_data_is_omitted():
    assert _render_geometry(_state({})) == ''
