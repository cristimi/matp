"""
Volume profile computation (POC / value area / HVN / LVN).

Pure local computation on already-fetched closed candles — no external calls,
modeled on indicators.py::compute_indicators (sync, takes candles, returns dict).

Method:
  - Each candle's volume is spread uniformly across the price bins its
    [low, high] range overlaps (a candle traded through its whole range,
    not just at one price).
  - POC (Point of Control) = center of the highest-volume bin.
  - Value area = smallest contiguous bin span around the POC holding
    VALUE_AREA_PCT of total volume (greedy expansion toward the larger
    neighbour, the standard construction).
  - HVNs / LVNs = interior local maxima / minima of the 3-bin-smoothed
    profile, filtered against the mean bin volume so noise bumps in a flat
    region don't register as nodes. POC-adjacent bins are excluded from the
    HVN list (the POC is reported separately).

Returns None on tiny candle counts, zero/flat volume, or a degenerate
price range — honest absence, mirroring geometry's return-{} convention.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

MIN_CANDLES    = 20    # below this a histogram is noise, not a profile
VALUE_AREA_PCT = 0.70  # standard 70% value area
MAX_NODES      = 3     # cap HVN/LVN lists — prompt readability over completeness


def compute_volume_profile(candles: list[dict], num_bins: int = 50) -> dict | None:
    """
    Build a volume-by-price histogram over the candle window.

    Returns:
        {'poc_price', 'value_area_high', 'value_area_low',
         'hvn_levels': [prices], 'lvn_levels': [prices]}
    or None on insufficient/degenerate data.
    """
    if not candles or len(candles) < MIN_CANDLES:
        return None

    try:
        lows    = np.array([c['low']    for c in candles], dtype=float)
        highs   = np.array([c['high']   for c in candles], dtype=float)
        volumes = np.array([c['volume'] for c in candles], dtype=float)

        price_min = float(np.min(lows))
        price_max = float(np.max(highs))
        total_vol = float(np.sum(volumes))

        if total_vol <= 0 or price_max <= price_min:
            return None

        edges     = np.linspace(price_min, price_max, num_bins + 1)
        centers   = (edges[:-1] + edges[1:]) / 2.0
        bin_width = edges[1] - edges[0]
        profile   = np.zeros(num_bins, dtype=float)

        # Spread each candle's volume uniformly over the bins its range covers.
        for lo, hi, vol in zip(lows, highs, volumes):
            if vol <= 0:
                continue
            if hi <= lo:
                idx = min(int((lo - price_min) / bin_width), num_bins - 1)
                profile[idx] += vol
                continue
            first = max(0, min(int((lo - price_min) / bin_width), num_bins - 1))
            last  = max(0, min(int((hi - price_min) / bin_width), num_bins - 1))
            span  = last - first + 1
            profile[first:last + 1] += vol / span

        poc_idx   = int(np.argmax(profile))
        poc_price = float(centers[poc_idx])

        # ── Value area: greedy expansion from POC toward the larger neighbour ──
        target = total_vol * VALUE_AREA_PCT
        lo_idx = hi_idx = poc_idx
        acc    = float(profile[poc_idx])
        while acc < target and (lo_idx > 0 or hi_idx < num_bins - 1):
            below = profile[lo_idx - 1] if lo_idx > 0 else -1.0
            above = profile[hi_idx + 1] if hi_idx < num_bins - 1 else -1.0
            if above >= below:
                hi_idx += 1
                acc    += float(profile[hi_idx])
            else:
                lo_idx -= 1
                acc    += float(profile[lo_idx])

        value_area_low  = float(centers[lo_idx])
        value_area_high = float(centers[hi_idx])

        # ── HVN / LVN: local extrema of the smoothed profile ──────────────────
        kernel   = np.ones(3) / 3.0
        smoothed = np.convolve(profile, kernel, mode='same')
        mean_vol = float(np.mean(smoothed))

        hvn: list[tuple[float, float]] = []  # (volume, price)
        lvn: list[tuple[float, float]] = []
        for i in range(1, num_bins - 1):
            v = float(smoothed[i])
            if v >= smoothed[i - 1] and v >= smoothed[i + 1] and v > mean_vol:
                if abs(i - poc_idx) > 1:  # POC reported separately
                    hvn.append((v, float(centers[i])))
            elif v <= smoothed[i - 1] and v <= smoothed[i + 1] and v < mean_vol:
                lvn.append((v, float(centers[i])))

        hvn.sort(key=lambda t: t[0], reverse=True)   # strongest nodes first
        lvn.sort(key=lambda t: t[0])                 # thinnest gaps first
        hvn_levels = sorted(round(p, 6) for _, p in hvn[:MAX_NODES])
        lvn_levels = sorted(round(p, 6) for _, p in lvn[:MAX_NODES])

        return {
            'poc_price':       round(poc_price, 6),
            'value_area_high': round(value_area_high, 6),
            'value_area_low':  round(value_area_low, 6),
            'hvn_levels':      hvn_levels,
            'lvn_levels':      lvn_levels,
        }

    except Exception as exc:
        logger.warning("compute_volume_profile error: %s", exc)
        return None


if __name__ == "__main__":
    import json
    import random

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    base = 65000.0
    candles = []
    for i in range(250):
        # Bimodal price distribution so the profile has real structure
        center = base + (2500 if i % 3 else -2500)
        p = center + random.gauss(0, 600)
        candles.append({
            'timestamp': i,
            'open':   p,
            'high':   p + abs(random.gauss(0, 300)),
            'low':    p - abs(random.gauss(0, 300)),
            'close':  p + random.gauss(0, 150),
            'volume': random.uniform(50, 500),
        })

    print(json.dumps(compute_volume_profile(candles), indent=2))
