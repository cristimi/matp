"""
Geometric price pattern detection via swing-point trendline analysis.

Thresholds (adjust here if needed; documented per spec):
  SWING_WINDOW      = 3     bars each side for fractal swing detection
  MIN_SWINGS        = 2     minimum swing points to attempt a trendline fit
  MAX_SWINGS        = 4     most recent N swings used in the linear fit
  FLAT_DRIFT_PCT    = 1.0   boundary moves < this % ACROSS THE WHOLE PATTERN → flat
  PARALLEL_DRIFT_PCT= 2.0   the two boundaries' total drifts differ by < this → parallel
  CONV_DRIFT_PCT    = 0.5   channel narrows by more than this % overall → converging
  TOUCH_TOL_PCT     = 0.60  swing within this % of trendline counts as a touch
  STRONG_R2         = 0.70  both R² ≥ this → fit_quality = "strong"
  MODERATE_R2       = 0.50  both R² ≥ this (but < STRONG_R2) → fit_quality = "moderate"
  MIN_R2_PATTERN    = 0.30  if either R² < this, refuse to classify → no_pattern

Shapes: horizontal_channel, ascending_channel, descending_channel, ascending_triangle,
descending_triangle, rising_wedge, falling_wedge, broadening, no_pattern.
  broadening: upper boundary rising AND lower boundary falling (strictly opposite-sign
  slopes) — the classic widening megaphone. Same-sign-but-diverging series (both
  boundaries trending the same direction at different rates) are not "broadening" and
  remain no_pattern; see the comment at the classification site.

Rationale for thresholds:
- FLAT / PARALLEL are judged over the WHOLE pattern, not per bar. A per-bar threshold
  hides the total: 0.05% per bar reads as "flat", but across a 42-bar pattern it is a
  2.1% climb that is plainly sloped on a chart — which is how an ETH fit whose two
  boundaries both rose (upper faster, so the channel more than doubled in width) came
  to be labelled a horizontal_channel. 1.0% end-to-end is the flat/trending line.
- CONV_DRIFT: 0.5% overall narrowing is the least that produces a visible apex; below
  that the two boundaries are effectively parallel over the window being judged.
- Slopes are normalised against the fitted channel's own midline, NOT the last close.
  Dividing by the live close made the label depend on where the last tick landed: the
  same unchanged fit was no_pattern at close 1897.73 and horizontal_channel an hour
  later at 1913.76, because price had moved the divisor across the threshold.
- STRONG_R2 = 0.70: standard "good fit" threshold; weak R² is flagged but not blocked.
- MODERATE_R2 = 0.50: middle tier — structure is real but noisier; the geometric_range
  template trades it only with stricter touch counts and a lower confidence cap.
- MIN_R2_PATTERN = 0.30: below this the trendline is essentially noise — don't classify.
"""
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

SWING_WINDOW       = 3
MIN_SWINGS         = 2
MAX_SWINGS         = 4
FLAT_DRIFT_PCT     = 1.0
PARALLEL_DRIFT_PCT = 2.0
CONV_DRIFT_PCT     = 0.5
TOUCH_TOL_PCT      = 0.60
STRONG_R2          = 0.70
MODERATE_R2        = 0.50
MIN_R2_PATTERN     = 0.30


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


# ── Chart-replay helpers ──────────────────────────────────────────────────────
# The trendline fit uses bar *index* as x, so a consumer holding only the result
# dict cannot redraw the boundaries anywhere except the final bar. The three
# helpers below export the missing index→time mapping (anchor bar, bar duration,
# swing timestamps) so a chart can project either boundary across history.

def _candle_ts(candles: list[dict], idx: int) -> Optional[int]:
    """Open-time (epoch ms) of candles[idx], or None if the series is untimed."""
    if idx < 0 or idx >= len(candles):
        return None
    ts = candles[idx].get('timestamp')
    return int(ts) if ts is not None else None


def _bar_seconds(candles: list[dict]) -> Optional[int]:
    """
    Bar duration in seconds, as the median gap between consecutive open-times.
    Median rather than first-gap so a single missing bar can't distort it.
    Returns None when the series carries no usable timestamps.
    """
    stamps = [c.get('timestamp') for c in candles]
    diffs  = [
        int(b) - int(a)
        for a, b in zip(stamps, stamps[1:])
        if a is not None and b is not None and int(b) > int(a)
    ]
    if not diffs:
        return None
    return int(round(float(np.median(diffs)) / 1000.0))


def _swing_points(
    swings: list[tuple[int, float]],
    candles: list[dict],
) -> list[list]:
    """Swings as [open_time_ms, price] pairs; untimed swings are dropped."""
    points: list[list] = []
    for idx, price in swings:
        ts = _candle_ts(candles, idx)
        if ts is None:
            continue
        points.append([ts, round(float(price), 6)])
    return points


def detect_geometry(candles: list[dict], lookback: int = 120) -> dict:
    """
    Detect geometric price patterns from OHLCV candles.

    Uses the most recent `lookback` candles. Returns a result dict with:
      shape, upper_boundary, lower_boundary, upper_touches, lower_touches,
      convergence_pct_per_bar, pattern_age_bars, position_in_range_pct, fit_quality.

    Plus the chart-replay fields, which describe the fit in time rather than in
    bar indices so a consumer can redraw the boundaries across history:
      upper_slope, lower_slope   price change per bar of each fitted trendline
      anchor_ts                  open-time (ms) of the bar used as x = 0 in the fit
      bar_seconds                bar duration in seconds (median gap)
      first_swing_ts             open-time (ms) of the oldest swing in the fit —
                                 where a drawn trendline should start
      swing_highs, swing_lows    every detected swing as [open_time_ms, price]

    The three timestamp fields and bar_seconds are None when the candles carry no
    'timestamp' key; the geometry itself is unaffected.

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
            # No fit was attempted, so the slopes are 0 and there is no first
            # swing to anchor a line to — but anchor_ts/bar_seconds still describe
            # the window, which is what a chart needs to place the (empty) result.
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
                'upper_slope':             0.0,
                'lower_slope':             0.0,
                'anchor_ts':               _candle_ts(candles, 0),
                'bar_seconds':             _bar_seconds(candles),
                'first_swing_ts':          None,
                'swing_highs':             _swing_points(swing_highs, candles),
                'swing_lows':              _swing_points(swing_lows, candles),
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

        # How many bars the fit actually spans. Classification is judged across
        # this whole window, so it is needed before the shape is decided.
        oldest_idx       = min(recent_highs[0][0], recent_lows[0][0])
        pattern_age_bars = last_idx - oldest_idx

        # Reference price for normalising the slopes: the fitted channel's own
        # midline at the last bar, not the last close. See the module docstring —
        # dividing by the live close let an unchanged fit change shape whenever
        # price drifted across a threshold.
        ref_price = (upper_boundary + lower_boundary) / 2.0
        if ref_price <= 0:
            ref_price = current_price

        # Per-bar slopes, kept for the reported convergence_pct_per_bar field.
        upper_pct = upper_slope / ref_price * 100.0
        lower_pct = lower_slope / ref_price * 100.0
        # Positive conv_rate → lines converging; negative → diverging
        conv_rate = lower_pct - upper_pct

        # What the eye actually sees: how far each boundary travels end-to-end.
        # span is floored at 1 so a degenerate single-bar fit still classifies
        # instead of collapsing every drift to zero and reading as horizontal.
        span        = max(pattern_age_bars, 1)
        upper_drift = upper_pct * span
        lower_drift = lower_pct * span
        conv_drift  = lower_drift - upper_drift

        def _is_flat(drift: float) -> bool:
            return abs(drift) < FLAT_DRIFT_PCT

        def _is_positive(drift: float) -> bool:
            return drift > FLAT_DRIFT_PCT

        def _is_negative(drift: float) -> bool:
            return drift < -FLAT_DRIFT_PCT

        is_converging = conv_drift > CONV_DRIFT_PCT
        is_parallel   = abs(conv_drift) < PARALLEL_DRIFT_PCT

        # Classify — reject if either trendline fit is essentially noise
        if min(upper_r2, lower_r2) < MIN_R2_PATTERN:
            shape = 'no_pattern'
        elif _is_flat(upper_drift) and _is_flat(lower_drift):
            shape = 'horizontal_channel'
        elif _is_positive(upper_drift) and _is_positive(lower_drift) and is_parallel:
            shape = 'ascending_channel'
        elif _is_negative(upper_drift) and _is_negative(lower_drift) and is_parallel:
            shape = 'descending_channel'
        elif _is_flat(upper_drift) and _is_positive(lower_drift):
            shape = 'ascending_triangle'
        elif _is_negative(upper_drift) and _is_flat(lower_drift):
            shape = 'descending_triangle'
        elif is_converging and _is_positive(upper_drift) and _is_positive(lower_drift):
            shape = 'rising_wedge'
        elif is_converging and _is_negative(upper_drift) and _is_negative(lower_drift):
            shape = 'falling_wedge'
        elif _is_positive(upper_drift) and _is_negative(lower_drift):
            # Broadening / megaphone: upper boundary rising, lower boundary falling.
            # Design decision: "broadening" is defined as strictly opposite-sign
            # slopes (the classic widening-megaphone shape), not merely a negative
            # conv_rate. Same-sign-but-diverging series (e.g. both boundaries
            # rising, upper faster than lower — see test_no_pattern_diverging)
            # don't form a megaphone and are deliberately left as no_pattern.
            shape = 'broadening'
        else:
            shape = 'no_pattern'

        min_r2 = min(upper_r2, lower_r2)
        if min_r2 >= STRONG_R2:
            fit_quality = 'strong'
        elif min_r2 >= MODERATE_R2:
            fit_quality = 'moderate'
        else:
            fit_quality = 'weak'
        upper_touches = _count_touches(swing_highs, upper_slope, upper_intercept, current_price)
        lower_touches = _count_touches(swing_lows,  lower_slope, lower_intercept, current_price)

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
            # End-to-end travel of each boundary, as % of the fitted midline. These
            # are the numbers the shape was decided on — without them a label that
            # disagrees with the drawn lines cannot be explained after the fact.
            'upper_drift_pct':         round(upper_drift, 4),
            'lower_drift_pct':         round(lower_drift, 4),
            'pattern_age_bars':        pattern_age_bars,
            'position_in_range_pct':   round(pos_in_range, 2),
            'fit_quality':             fit_quality,
            # Boundary at bar i (i counted from the anchor bar, x=0 in the fit):
            #   upper = upper_boundary + upper_slope * (i - last_idx)
            # so a chart can draw the real sloped line, not two flat levels.
            'upper_slope':             round(upper_slope, 8),
            'lower_slope':             round(lower_slope, 8),
            'anchor_ts':               _candle_ts(candles, 0),
            'bar_seconds':             _bar_seconds(candles),
            'first_swing_ts':          _candle_ts(candles, oldest_idx),
            'swing_highs':             _swing_points(swing_highs, candles),
            'swing_lows':              _swing_points(swing_lows, candles),
        }

    except Exception as exc:
        logger.warning("detect_geometry error: %s", exc)
        return {}
