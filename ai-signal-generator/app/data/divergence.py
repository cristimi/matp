"""
Momentum divergence detection (price swings vs RSI / MACD histogram).

Pure local computation on already-fetched closed candles — no external calls,
modeled on geometry.py::detect_geometry (sync, swing-based). Reuses geometry's
fractal swing detection (_find_swings).

Classic regular divergence only:
  - bearish: price makes a higher swing high while the oscillator makes a
    lower high at the same swings
  - bullish: price makes a lower swing low while the oscillator makes a
    higher low

MIN_SWING_SEP guards against false pairings from adjacent fractal swings —
two swings closer than this are one market structure, not two comparable
extremes (mirrors geometry's touch-tolerance guarding).

Named momentum_divergence/rsi_divergence deliberately — builder.py already
emits a geometry "Divergence Rate:" label; distinct names keep grep and LLM
reads unambiguous.

Returns None on insufficient data — honest absence.
"""

import logging

import numpy as np
import pandas as pd
import pandas_ta as ta

from app.data.geometry import _find_swings

logger = logging.getLogger(__name__)

MIN_CANDLES   = 60  # MACD needs ~35 bars of warmup; below this the series is noise
MIN_SWING_SEP = 5   # bars — minimum separation for a swing pair to be comparable


def _last_pair(
    swings: list[tuple[int, float]],
    min_sep: int = MIN_SWING_SEP,
) -> tuple[int, float, int, float] | None:
    """Most recent swing paired with the nearest earlier swing ≥ min_sep bars away."""
    if len(swings) < 2:
        return None
    i2, p2 = swings[-1]
    for i1, p1 in reversed(swings[:-1]):
        if i2 - i1 >= min_sep:
            return i1, p1, i2, p2
    return None


def _detect_for_oscillator(
    osc: np.ndarray,
    swing_highs: list[tuple[int, float]],
    swing_lows: list[tuple[int, float]],
    last_idx: int,
) -> tuple[str, int | None]:
    """Return ('bullish'|'bearish'|'none', bars_since) for one oscillator series."""
    bearish_since = bullish_since = None

    pair = _last_pair(swing_highs)
    if pair:
        i1, p1, i2, p2 = pair
        if not (np.isnan(osc[i1]) or np.isnan(osc[i2])):
            if p2 > p1 and osc[i2] < osc[i1]:
                bearish_since = last_idx - i2

    pair = _last_pair(swing_lows)
    if pair:
        i1, p1, i2, p2 = pair
        if not (np.isnan(osc[i1]) or np.isnan(osc[i2])):
            if p2 < p1 and osc[i2] > osc[i1]:
                bullish_since = last_idx - i2

    # Both can trigger in a whipsaw window — report the more recent structure.
    if bearish_since is not None and bullish_since is not None:
        if bullish_since <= bearish_since:
            return 'bullish', bullish_since
        return 'bearish', bearish_since
    if bearish_since is not None:
        return 'bearish', bearish_since
    if bullish_since is not None:
        return 'bullish', bullish_since
    return 'none', None


def detect_momentum_divergence(candles: list[dict], lookback: int = 60) -> dict | None:
    """
    Detect regular RSI and MACD-histogram divergence at price swing points
    within the last `lookback` bars.

    Returns:
        {'rsi_divergence': 'bullish|bearish|none', 'rsi_divergence_bars_since': int|None,
         'macd_divergence': 'bullish|bearish|none', 'macd_divergence_bars_since': int|None}
    or None on insufficient data.
    """
    if not candles or len(candles) < MIN_CANDLES:
        return None

    try:
        highs  = np.array([c['high']  for c in candles], dtype=float)
        lows   = np.array([c['low']   for c in candles], dtype=float)
        closes = pd.Series([c['close'] for c in candles], dtype=float)

        rsi_s = ta.rsi(closes, length=14)
        rsi = rsi_s.to_numpy() if rsi_s is not None else None

        macd_hist = None
        macd_df = ta.macd(closes)
        if macd_df is not None and not macd_df.empty:
            hist_col = next((c for c in macd_df.columns if c.startswith('MACDh')), None)
            if hist_col:
                macd_hist = macd_df[hist_col].to_numpy()

        if rsi is None and macd_hist is None:
            return None

        # Swings are detected over the full series (fractals need context on
        # both sides) but only pairs inside the lookback window are compared —
        # oscillator warmup NaNs live outside that window anyway.
        swing_highs, swing_lows = _find_swings(highs, lows)
        last_idx     = len(candles) - 1
        window_start = max(0, len(candles) - lookback)
        swing_highs  = [s for s in swing_highs if s[0] >= window_start]
        swing_lows   = [s for s in swing_lows  if s[0] >= window_start]

        result: dict = {
            'rsi_divergence':             'none',
            'rsi_divergence_bars_since':  None,
            'macd_divergence':            'none',
            'macd_divergence_bars_since': None,
        }

        if rsi is not None:
            kind, since = _detect_for_oscillator(rsi, swing_highs, swing_lows, last_idx)
            result['rsi_divergence']            = kind
            result['rsi_divergence_bars_since'] = since

        if macd_hist is not None:
            kind, since = _detect_for_oscillator(macd_hist, swing_highs, swing_lows, last_idx)
            result['macd_divergence']            = kind
            result['macd_divergence_bars_since'] = since

        return result

    except Exception as exc:
        logger.warning("detect_momentum_divergence error: %s", exc)
        return None


if __name__ == "__main__":
    import json
    import math

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # Rising price with fading oscillation amplitude → bearish-divergence-shaped tape
    candles = []
    for i in range(120):
        p = 65000 + i * 30 + math.sin(i / 6) * max(200, 1500 - i * 12)
        candles.append({
            'timestamp': i,
            'open': p, 'high': p + 120, 'low': p - 120, 'close': p,
            'volume': 100.0,
        })

    print(json.dumps(detect_momentum_divergence(candles), indent=2))
