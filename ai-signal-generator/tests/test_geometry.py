"""
Unit tests for geometry.py swing detection and pattern classification.

Each test builds a synthetic candle series where swing highs and lows exactly follow
a defined trendline, making the expected classification deterministic.

The _zigzag_candles helper generates candles that alternate between an upper and lower
boundary every `half_period` bars. Because the peak/trough candles are at the exact
boundary value with lower surrounding bars/lows, fractal swing detection (window=3)
picks them up cleanly.
"""
import sys
import os

# Allow running from the ai-signal-generator root or from tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from app.data.geometry import detect_geometry


# ── Synthetic candle builder ───────────────────────────────────────────────────

def _zigzag_candles(n_bars: int, upper_fn, lower_fn, half_period: int = 7) -> list[dict]:
    """
    Produce candles that zigzag between upper_fn(i) and lower_fn(i).

    Phase half_period → swing high: high = upper_fn(i).
                        Low is kept tight ABOVE surrounding bar lows so the swing-high
                        candle is not falsely detected as a swing low.
    Phase 0           → swing low: low = lower_fn(i).
                        High is kept tight BELOW surrounding bar highs so the swing-low
                        candle is not falsely detected as a swing high.
    Other phases      → linear transition; highs/lows built with ±4% of span, which
                        stays strictly inside [up - span*0.10, lo + span*0.10] so the
                        10%-buffer candles at the turning points are locally extreme.
    """
    candles = []
    for i in range(n_bars):
        up    = upper_fn(i)
        lo    = lower_fn(i)
        span  = up - lo
        phase = i % (2 * half_period)

        if phase == half_period:
            # Swing high: h = upper boundary.  l is tight but above surrounding lows
            # (surrounding rising/falling legs reach at most up − span*0.143*hp/hp ≈ up−span*0.04*1/frac,
            # but the worst case is the ±1 bars at frac≈0.857 → l ≈ lo + 0.857*span − 0.04*span.
            # Setting l = up − span*0.10 keeps it above that value by ~1.5% of span).
            h = up
            c = up - span * 0.02
            o = up - span * 0.03
            l = up - span * 0.10
        elif phase == 0:
            # Swing low: l = lower boundary.  h is tight but below surrounding highs
            # (surrounding bars reach at most lo + 0.143*span + 0.04*span ≈ lo + span*0.18 at ±1 bars;
            # setting h = lo + span*0.10 stays below that).
            l = lo
            c = lo + span * 0.02
            o = lo + span * 0.03
            h = lo + span * 0.10
        elif phase < half_period:
            frac = phase / half_period
            c = lo + frac * span
            o = c - span * 0.02
            h = c + span * 0.04
            l = c - span * 0.04
        else:
            frac = (phase - half_period) / half_period
            c = up - frac * span
            o = c + span * 0.02
            h = c + span * 0.04
            l = c - span * 0.04

        h = max(h, c, o)
        l = min(l, c, o)

        candles.append({
            'timestamp': i * 3_600_000,
            'open':      float(o),
            'high':      float(h),
            'low':       float(l),
            'close':     float(c),
            'volume':    100.0,
        })
    return candles


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_horizontal_channel():
    candles = _zigzag_candles(80, lambda i: 110.0, lambda i: 90.0)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'horizontal_channel', f"Got: {result}"
    assert result.get('fit_quality') == 'strong'
    assert result.get('upper_touches', 0) >= 2
    assert result.get('lower_touches', 0) >= 2


def test_ascending_channel():
    # Both boundaries rise at the same rate → parallel ascending
    candles = _zigzag_candles(80, lambda i: 110 + 0.15 * i, lambda i: 90 + 0.15 * i)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'ascending_channel', f"Got: {result}"
    assert result.get('fit_quality') == 'strong'


def test_descending_channel():
    # Both boundaries fall at the same rate → parallel descending
    candles = _zigzag_candles(80, lambda i: 130 - 0.15 * i, lambda i: 110 - 0.15 * i)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'descending_channel', f"Got: {result}"
    assert result.get('fit_quality') == 'strong'


def test_ascending_triangle():
    # Flat upper resistance, rising lower support
    candles = _zigzag_candles(80, lambda i: 110.0, lambda i: 80 + 0.2 * i)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'ascending_triangle', f"Got: {result}"
    assert result.get('fit_quality') == 'strong'
    # Upper boundary should be roughly at 110
    assert abs(result['upper_boundary'] - 110.0) < 2.0


def test_descending_triangle():
    # Falling upper resistance, flat lower support
    candles = _zigzag_candles(80, lambda i: 120 - 0.2 * i, lambda i: 80.0)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'descending_triangle', f"Got: {result}"
    assert result.get('fit_quality') == 'strong'


def test_rising_wedge():
    # Both rising, lower rises faster (converging from below)
    candles = _zigzag_candles(80, lambda i: 110 + 0.1 * i, lambda i: 90 + 0.3 * i)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'rising_wedge', f"Got: {result}"
    assert result.get('fit_quality') == 'strong'
    assert result.get('convergence_pct_per_bar', 0) > 0


def test_falling_wedge():
    # Both falling, upper falls faster (converging from above)
    candles = _zigzag_candles(80, lambda i: 130 - 0.3 * i, lambda i: 110 - 0.1 * i)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'falling_wedge', f"Got: {result}"
    assert result.get('fit_quality') == 'strong'
    assert result.get('convergence_pct_per_bar', 0) > 0


def test_no_pattern_diverging():
    # Both rising but at different rates and diverging → no parallel, no convergence
    candles = _zigzag_candles(80, lambda i: 110 + 0.3 * i, lambda i: 90 + 0.1 * i)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'no_pattern', f"Got: {result}"


def test_position_in_range():
    # With a horizontal channel 90-110, a close near 100 should be ~50%
    candles = _zigzag_candles(80, lambda i: 110.0, lambda i: 90.0)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'horizontal_channel'
    pos = result.get('position_in_range_pct', -1)
    assert 0.0 <= pos <= 100.0, f"position_in_range_pct out of range: {pos}"


def test_too_few_candles():
    # Fewer candles than swing_window*2+3 = 9 → empty dict
    candles = [{'timestamp': i * 3_600_000, 'open': 100.0, 'high': 101.0,
                'low': 99.0, 'close': 100.0, 'volume': 100.0} for i in range(5)]
    assert detect_geometry(candles) == {}


def test_insufficient_swings():
    # Just enough candles to run but too few swings (flat line → every candle is a tie,
    # so swing detection may or may not fire — either way must not raise)
    candles = [{'timestamp': i * 3_600_000, 'open': 100.0, 'high': 100.0,
                'low': 100.0, 'close': 100.0, 'volume': 100.0} for i in range(20)]
    result = detect_geometry(candles)
    assert isinstance(result, dict)


def test_empty_candles():
    assert detect_geometry([]) == {}


def test_output_keys_present():
    candles = _zigzag_candles(80, lambda i: 110.0, lambda i: 90.0)
    result  = detect_geometry(candles)
    required = {
        'shape', 'upper_boundary', 'lower_boundary',
        'upper_touches', 'lower_touches', 'convergence_pct_per_bar',
        'pattern_age_bars', 'position_in_range_pct', 'fit_quality',
    }
    assert required.issubset(result.keys()), f"Missing keys: {required - result.keys()}"


def test_fit_quality_values():
    candles = _zigzag_candles(80, lambda i: 110.0, lambda i: 90.0)
    result  = detect_geometry(candles)
    assert result.get('fit_quality') in ('strong', 'weak')
