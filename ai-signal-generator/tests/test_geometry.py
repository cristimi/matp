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
    # Both rising but at different rates and diverging → no parallel, no convergence.
    # This is same-sign divergence (both boundaries trend up), not a megaphone, so
    # under the broadening definition (strictly opposite-sign slopes; see geometry.py)
    # it deliberately stays no_pattern rather than being reclassified.
    candles = _zigzag_candles(80, lambda i: 110 + 0.3 * i, lambda i: 90 + 0.1 * i)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'no_pattern', f"Got: {result}"


def test_slow_climb_is_not_horizontal():
    """
    Reproduces the ETH-USDT read of 2026-07-26 16:00 (signal 4757).

    Both boundaries rose — upper +0.953/bar, lower +0.720/bar on a ~1889 midline
    over a 42-bar pattern — yet the shape came back 'horizontal_channel', because
    each slope was under 0.05% of price PER BAR. End-to-end that is a 2.1% climb
    with the channel more than doubling in width, which is what the chart drew.
    Judged over the whole pattern, it must not read as horizontal.
    """
    candles = _zigzag_candles(
        80,
        lambda i: 1897.6 + 0.95339202 * (i - 79),
        lambda i: 1880.8 + 0.72031746 * (i - 79),
    )
    result = detect_geometry(candles)
    assert result.get('shape') != 'horizontal_channel', f"Got: {result}"
    assert result.get('upper_drift_pct', 0) > 1.0, f"Got: {result}"
    assert result.get('lower_drift_pct', 0) > 1.0, f"Got: {result}"


def test_shape_does_not_depend_on_the_last_close():
    """
    The same fitted boundaries must classify the same however the last bar closes.

    Slopes used to be normalised against the live close, so an unchanged ETH fit
    read 'no_pattern' at close 1897.73 and 'horizontal_channel' an hour later at
    1913.76 — the divisor had moved across the flat threshold, nothing else.
    """
    def build(last_close_offset: float) -> dict:
        candles = _zigzag_candles(
            80,
            lambda i: 1897.6 + 0.95339202 * (i - 79),
            lambda i: 1880.8 + 0.72031746 * (i - 79),
        )
        # Nudge only the final close; every swing point stays exactly where it was,
        # so the fit — and therefore the shape — must not move either.
        last = candles[-1]
        last['close'] = last['close'] + last_close_offset
        last['high']  = max(last['high'], last['close'])
        last['low']   = min(last['low'],  last['close'])
        return detect_geometry(candles)

    assert build(-16.0)['shape'] == build(+16.0)['shape']


def test_broadening():
    # Upper boundary rising, lower boundary falling → classic broadening/megaphone
    # (opposite-sign slopes), mirrors the reproduced HYPE geometry case.
    candles = _zigzag_candles(80, lambda i: 110 + 0.15 * i, lambda i: 90 - 0.08 * i)
    result  = detect_geometry(candles)
    assert result.get('shape') == 'broadening', f"Got: {result}"
    assert result.get('fit_quality') == 'strong'
    assert result.get('convergence_pct_per_bar', 0) < 0


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


# ── Chart-replay fields ────────────────────────────────────────────────────────
# These describe the fit in time rather than in bar indices, so a chart can redraw
# the boundaries across history instead of pinning them to the final bar.

BAR_MS = 3_600_000  # _zigzag_candles stamps bars one hour apart


def test_chart_replay_fields_present_and_sane():
    candles = _zigzag_candles(80, lambda i: 110.0, lambda i: 90.0)
    result  = detect_geometry(candles)

    assert result.get('shape') == 'horizontal_channel', f"Got: {result}"

    # bar_seconds must match the fixture's one-hour spacing
    assert result['bar_seconds'] == BAR_MS // 1000

    # anchor_ts is x = 0 of the fit — the first candle of the analysed window
    assert result['anchor_ts'] == candles[0]['timestamp']

    # first_swing_ts sits inside the window and at or after the anchor
    assert isinstance(result['first_swing_ts'], int)
    assert candles[0]['timestamp'] <= result['first_swing_ts'] <= candles[-1]['timestamp']

    # A flat channel has near-zero slopes on both boundaries
    assert abs(result['upper_slope']) < 0.01
    assert abs(result['lower_slope']) < 0.01

    # Swings come back as [open_time_ms, price] pairs aligned to the bar grid
    for key, expected in (('swing_highs', 110.0), ('swing_lows', 90.0)):
        points = result[key]
        assert len(points) >= 2, f"{key}: {points}"
        for ts, price in points:
            assert ts % BAR_MS == 0
            assert candles[0]['timestamp'] <= ts <= candles[-1]['timestamp']
            assert abs(price - expected) < 2.0


def test_slopes_match_the_synthetic_trendlines():
    # Upper rises 0.15/bar, lower rises 0.30/bar (the rising-wedge fixture shape)
    candles = _zigzag_candles(80, lambda i: 110 + 0.15 * i, lambda i: 90 + 0.30 * i)
    result  = detect_geometry(candles)

    assert abs(result['upper_slope'] - 0.15) < 0.02, f"upper_slope={result['upper_slope']}"
    assert abs(result['lower_slope'] - 0.30) < 0.02, f"lower_slope={result['lower_slope']}"


def test_slope_projects_boundary_back_to_the_first_swing():
    # Projecting the boundary from the last bar back to first_swing_ts must land on
    # the trendline value there — this is exactly what the chart does when drawing.
    candles = _zigzag_candles(80, lambda i: 110 + 0.15 * i, lambda i: 90 + 0.15 * i)
    result  = detect_geometry(candles)

    bars_back = (result['first_swing_ts'] - candles[-1]['timestamp']) // BAR_MS
    projected = result['upper_boundary'] + result['upper_slope'] * bars_back

    first_swing_idx = (result['first_swing_ts'] - candles[0]['timestamp']) // BAR_MS
    expected        = 110 + 0.15 * first_swing_idx

    assert abs(projected - expected) < 2.0, f"projected={projected} expected={expected}"


def test_anchor_ts_follows_the_lookback_slice():
    # 200 bars with lookback=120 → the fit window starts at candle index 80, and
    # anchor_ts must point there, not at the first candle handed in.
    candles = _zigzag_candles(200, lambda i: 110.0, lambda i: 90.0)
    result  = detect_geometry(candles, lookback=120)

    assert result['anchor_ts'] == candles[-120]['timestamp']
    assert result['bar_seconds'] == BAR_MS // 1000


def test_insufficient_swings_still_carries_window_fields():
    # Strictly rising highs and lows produce no fractal swings at all, so no fit is
    # attempted — but the window description must still be present for the chart.
    candles = [
        {'timestamp': i * BAR_MS, 'open': 100.0 + i, 'high': 101.0 + i,
         'low': 99.0 + i, 'close': 100.0 + i, 'volume': 100.0}
        for i in range(20)
    ]
    result = detect_geometry(candles)

    assert result['shape'] == 'no_pattern'
    assert result['upper_slope'] == 0.0
    assert result['lower_slope'] == 0.0
    assert result['anchor_ts'] == 0
    assert result['bar_seconds'] == BAR_MS // 1000
    assert result['first_swing_ts'] is None
    assert result['swing_highs'] == []
    assert result['swing_lows'] == []


def test_untimed_candles_leave_chart_fields_empty():
    # Geometry itself must keep working when the caller supplies no timestamps.
    candles = _zigzag_candles(80, lambda i: 110.0, lambda i: 90.0)
    for c in candles:
        del c['timestamp']
    result = detect_geometry(candles)

    assert result['shape'] == 'horizontal_channel'
    assert result['anchor_ts'] is None
    assert result['bar_seconds'] is None
    assert result['first_swing_ts'] is None
    assert result['swing_highs'] == []
    assert result['swing_lows'] == []


def test_output_keys_present():
    candles = _zigzag_candles(80, lambda i: 110.0, lambda i: 90.0)
    result  = detect_geometry(candles)
    required = {
        'shape', 'upper_boundary', 'lower_boundary',
        'upper_touches', 'lower_touches', 'convergence_pct_per_bar',
        'pattern_age_bars', 'position_in_range_pct', 'fit_quality',
        # chart-replay fields
        'upper_slope', 'lower_slope', 'anchor_ts', 'bar_seconds',
        'first_swing_ts', 'swing_highs', 'swing_lows',
    }
    assert required.issubset(result.keys()), f"Missing keys: {required - result.keys()}"


def test_fit_quality_values():
    candles = _zigzag_candles(80, lambda i: 110.0, lambda i: 90.0)
    result  = detect_geometry(candles)
    assert result.get('fit_quality') in ('strong', 'moderate', 'weak')
