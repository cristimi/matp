"""
Volatility regime classification (ATR / Bollinger-width percentile rank).

Pure local computation on already-fetched closed candles — no external calls,
modeled on indicators.py::compute_indicators (sync, takes candles, returns dict).

Percentile rank answers "how volatile is now vs the trailing window?":
  - atr_percentile:      ATR(14) latest value's rank vs the trailing window
  - bb_width_percentile: Bollinger(20, 2) relative width latest vs trailing window
  - squeeze_flag:        BB width in the bottom SQUEEZE_THR_PCT of the window —
                         compression that typically precedes expansion

Returns None below the candle floor — honest absence, never a fabricated
neutral regime.
"""

import logging

import numpy as np
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)

MIN_CANDLES     = 60  # ATR(14)+BB(20) warmup plus enough history to rank against
SQUEEZE_THR_PCT = 15  # BB-width percentile below this → squeeze


def _percentile_rank(series: np.ndarray, window: int) -> float | None:
    """Rank of the latest value vs the trailing `window` values (inclusive), 0-100."""
    vals = series[~np.isnan(series)]
    if len(vals) < 2:
        return None
    tail = vals[-window:] if window and len(vals) > window else vals
    return float(np.mean(tail <= tail[-1]) * 100.0)


def compute_volatility_regime(candles: list[dict], percentile_window: int = 200) -> dict | None:
    """
    Classify the current volatility regime from closed candles.

    Returns:
        {'atr_percentile': float, 'bb_width_percentile': float, 'squeeze_flag': bool}
    or None on insufficient data.
    """
    if not candles or len(candles) < MIN_CANDLES:
        return None

    try:
        high  = pd.Series([c['high']  for c in candles], dtype=float)
        low   = pd.Series([c['low']   for c in candles], dtype=float)
        close = pd.Series([c['close'] for c in candles], dtype=float)

        atr_s = ta.atr(high, low, close, length=14)
        if atr_s is None or not atr_s.notna().any():
            return None
        atr_pct = _percentile_rank(atr_s.to_numpy(), percentile_window)

        bb_df = ta.bbands(close, length=20, std=2)
        if bb_df is None or bb_df.empty:
            return None
        upper_col = next((c for c in bb_df.columns if c.startswith('BBU')), None)
        mid_col   = next((c for c in bb_df.columns if c.startswith('BBM')), None)
        lower_col = next((c for c in bb_df.columns if c.startswith('BBL')), None)
        if not (upper_col and mid_col and lower_col):
            return None
        # Relative width (span / mid) so the percentile compares volatility,
        # not price level, across a window where price may have trended.
        mid = bb_df[mid_col].replace(0, np.nan)
        width = ((bb_df[upper_col] - bb_df[lower_col]) / mid).to_numpy()
        bb_pct = _percentile_rank(width, percentile_window)

        if atr_pct is None or bb_pct is None:
            return None

        return {
            'atr_percentile':      round(atr_pct, 1),
            'bb_width_percentile': round(bb_pct, 1),
            'squeeze_flag':        bb_pct < SQUEEZE_THR_PCT,
        }

    except Exception as exc:
        logger.warning("compute_volatility_regime error: %s", exc)
        return None


if __name__ == "__main__":
    import json
    import random

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # High early volatility, compressed late → low current percentiles / squeeze
    candles = []
    p = 65000.0
    for i in range(300):
        sigma = 800 if i < 250 else 60
        p += random.gauss(0, sigma)
        candles.append({
            'timestamp': i,
            'open':   p,
            'high':   p + abs(random.gauss(0, sigma / 2)),
            'low':    p - abs(random.gauss(0, sigma / 2)),
            'close':  p + random.gauss(0, sigma / 4),
            'volume': 100.0,
        })

    print(json.dumps(compute_volatility_regime(candles), indent=2))
