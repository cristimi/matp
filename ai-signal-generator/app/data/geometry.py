"""
Geometric price pattern detection via swing-point trendline analysis.

Thresholds (adjust here if needed; documented per spec):
  SWING_WINDOW      = 3     bars each side for fractal swing detection
  MIN_SWINGS        = 2     minimum swing points to attempt a trendline fit
  MAX_SWINGS        = 4     most recent N swings used in the linear fit
  FLAT_THR_PCT      = 0.05  |slope| < this % of price per bar → classified as flat
  PARALLEL_THR_PCT  = 0.04  |upper_pct − lower_pct| < this → classified as parallel
  CONV_THR_PCT      = 0.01  convergence rate > this % per bar → classified as converging
  TOUCH_TOL_PCT     = 0.60  swing within this % of trendline counts as a touch
  STRONG_R2         = 0.70  both R² ≥ this → fit_quality = "strong"
  MIN_R2_PATTERN    = 0.30  if either R² < this, refuse to classify → no_pattern

Shapes: horizontal_channel, ascending_channel, descending_channel, ascending_triangle,
descending_triangle, rising_wedge, falling_wedge, broadening, no_pattern.
  broadening: upper boundary rising AND lower boundary falling (strictly opposite-sign
  slopes) — the classic widening megaphone. Same-sign-but-diverging series (both
  boundaries trending the same direction at different rates) are not "broadening" and
  remain no_pattern; see the comment at the classification site.

Rationale for thresholds:
- FLAT / PARALLEL: 0.05% per bar means a $100 price moves $0.05 per bar on the boundary
  before it's considered "trending". Tight but prevents mislabelling slow drifts as channels.
- CONV_THR: 0.01% per bar is the minimum convergence rate that would produce a meaningful
  apex within a reasonable number of bars (~1000 bars before completely closed).
- STRONG_R2 = 0.70: standard "good fit" threshold; weak R² is flagged but not blocked.
- MIN_R2_PATTERN = 0.30: below this the trendline is essentially noise — don't classify.
"""
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

SWING_WINDOW     = 3
MIN_SWINGS       = 2
MAX_SWINGS       = 4
FLAT_THR_PCT     = 0.05
PARALLEL_THR_PCT = 0.04
CONV_THR_PCT     = 0.01
TOUCH_TOL_PCT    = 0.60
STRONG_R2        = 0.70
MIN_R2_PATTERN   = 0.30


def _find_swings(
    highs: np.ndarray,
    lows: np.ndarray,
    window: int = SWING_WINDOW,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """
    Fractal swing detection. A bar is a swing high if its high equals the max
    over [i-window, i+window]. Edge bars within `window` of the series ends are
    excluded. Returns (swing_highs, swing_lows) as lists of (bar_index, price).
    """
    n = len(highs)
    swing_highs: list[tuple[int, float]] = []
    swing_lows:  list[tuple[int, float]] = []
    for i in range(window, n - window):
        lo_idx = i - window
        hi_idx = i + window + 1
        if highs[i] == np.max(highs[lo_idx:hi_idx]):
            swing_highs.append((i, float(highs[i])))
        if lows[i] == np.min(lows[lo_idx:hi_idx]):
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def _polyfit_r2(
    x: np.ndarray,
    y: np.ndarray,
) -> tuple[float, float, float]:
    """Linear fit; returns (slope, intercept, r2). Handles constant y (r2=1.0)."""
    coeffs               = np.polyfit(x, y, 1)
    slope, intercept     = float(coeffs[0]), float(coeffs[1])
    y_pred               = slope * x + intercept
    ss_res               = float(np.sum((y - y_pred) ** 2))
    ss_tot               = float(np.sum((y - float(np.mean(y))) ** 2))
    r2                   = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return slope, intercept, r2


def _count_touches(
    swings: list[tuple[int, float]],
    slope: float,
    intercept: float,
    ref_price: float,
    tol_pct: float = TOUCH_TOL_PCT,
) -> int:
    """Count swing points within tol_pct% of the projected trendline value."""
    tol = ref_price * tol_pct / 100.0
    count = 0
    for idx, sw_price in swings:
        if abs(sw_price - (slope * idx + intercept)) <= tol:
            count += 1
    return count


def detect_geometry(candles: list[dict], lookback: int = 120) -> dict:
    """
    Detect geometric price patterns from OHLCV candles.

    Uses the most recent `lookback` candles. Returns a result dict with:
      shape, upper_boundary, lower_boundary, upper_touches, lower_touches,
      convergence_pct_per_bar, pattern_age_bars, position_in_range_pct, fit_quality.

    Returns {} on insufficient data or unhandled error.
    Returns {'shape': 'no_pattern', ...} when swings are insufficient or lines diverge.
    """
    if not candles or len(candles) < SWING_WINDOW * 2 + 3:
        return {}

    try:
        if lookback and len(candles) > lookback:
            candles = candles[-lookback:]

        highs  = np.array([c['high']  for c in candles], dtype=float)
        lows   = np.array([c['low']   for c in candles], dtype=float)
        closes = np.array([c['close'] for c in candles], dtype=float)

        current_price = float(closes[-1])
        if current_price <= 0:
            return {}

        swing_highs, swing_lows = _find_swings(highs, lows)

        if len(swing_highs) < MIN_SWINGS or len(swing_lows) < MIN_SWINGS:
            return {
                'shape':                   'no_pattern',
                'upper_boundary':          0.0,
                'lower_boundary':          0.0,
                'upper_touches':           len(swing_highs),
                'lower_touches':           len(swing_lows),
                'convergence_pct_per_bar': 0.0,
                'pattern_age_bars':        0,
                'position_in_range_pct':   50.0,
                'fit_quality':             'weak',
            }

        recent_highs = swing_highs[-MAX_SWINGS:]
        recent_lows  = swing_lows[-MAX_SWINGS:]

        x_h = np.array([s[0] for s in recent_highs], dtype=float)
        y_h = np.array([s[1] for s in recent_highs], dtype=float)
        upper_slope, upper_intercept, upper_r2 = _polyfit_r2(x_h, y_h)

        x_l = np.array([s[0] for s in recent_lows], dtype=float)
        y_l = np.array([s[1] for s in recent_lows], dtype=float)
        lower_slope, lower_intercept, lower_r2 = _polyfit_r2(x_l, y_l)

        last_idx       = len(candles) - 1
        upper_boundary = upper_slope * last_idx + upper_intercept
        lower_boundary = lower_slope * last_idx + lower_intercept

        # Slopes normalised as % of current price per bar
        upper_pct = upper_slope / current_price * 100.0
        lower_pct = lower_slope / current_price * 100.0
        # Positive conv_rate → lines converging; negative → diverging
        conv_rate = lower_pct - upper_pct

        def _is_flat(pct: float) -> bool:
            return abs(pct) < FLAT_THR_PCT

        def _is_positive(pct: float) -> bool:
            return pct > FLAT_THR_PCT

        def _is_negative(pct: float) -> bool:
            return pct < -FLAT_THR_PCT

        is_converging = conv_rate > CONV_THR_PCT
        is_parallel   = abs(conv_rate) < PARALLEL_THR_PCT

        # Classify — reject if either trendline fit is essentially noise
        if min(upper_r2, lower_r2) < MIN_R2_PATTERN:
            shape = 'no_pattern'
        elif _is_flat(upper_pct) and _is_flat(lower_pct):
            shape = 'horizontal_channel'
        elif _is_positive(upper_pct) and _is_positive(lower_pct) and is_parallel:
            shape = 'ascending_channel'
        elif _is_negative(upper_pct) and _is_negative(lower_pct) and is_parallel:
            shape = 'descending_channel'
        elif _is_flat(upper_pct) and _is_positive(lower_pct):
            shape = 'ascending_triangle'
        elif _is_negative(upper_pct) and _is_flat(lower_pct):
            shape = 'descending_triangle'
        elif is_converging and _is_positive(upper_pct) and _is_positive(lower_pct):
            shape = 'rising_wedge'
        elif is_converging and _is_negative(upper_pct) and _is_negative(lower_pct):
            shape = 'falling_wedge'
        elif _is_positive(upper_pct) and _is_negative(lower_pct):
            # Broadening / megaphone: upper boundary rising, lower boundary falling.
            # Design decision: "broadening" is defined as strictly opposite-sign
            # slopes (the classic widening-megaphone shape), not merely a negative
            # conv_rate. Same-sign-but-diverging series (e.g. both boundaries
            # rising, upper faster than lower — see test_no_pattern_diverging)
            # don't form a megaphone and are deliberately left as no_pattern.
            shape = 'broadening'
        else:
            shape = 'no_pattern'

        fit_quality   = 'strong' if min(upper_r2, lower_r2) >= STRONG_R2 else 'weak'
        upper_touches = _count_touches(swing_highs, upper_slope, upper_intercept, current_price)
        lower_touches = _count_touches(swing_lows,  lower_slope, lower_intercept, current_price)

        oldest_idx       = min(recent_highs[0][0], recent_lows[0][0])
        pattern_age_bars = last_idx - oldest_idx

        gap = upper_boundary - lower_boundary
        if gap > 0:
            pos_in_range = (current_price - lower_boundary) / gap * 100.0
            pos_in_range = max(0.0, min(100.0, pos_in_range))
        else:
            pos_in_range = 50.0

        return {
            'shape':                   shape,
            'upper_boundary':          round(upper_boundary, 6),
            'lower_boundary':          round(lower_boundary, 6),
            'upper_touches':           upper_touches,
            'lower_touches':           lower_touches,
            'convergence_pct_per_bar': round(conv_rate, 4),
            'pattern_age_bars':        pattern_age_bars,
            'position_in_range_pct':   round(pos_in_range, 2),
            'fit_quality':             fit_quality,
        }

    except Exception as exc:
        logger.warning("detect_geometry error: %s", exc)
        return {}
