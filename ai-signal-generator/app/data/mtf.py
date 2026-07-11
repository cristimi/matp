"""
Multi-timeframe structure classification.

One fetch_ohlcv call per timeframe, then pure local classification on each
timeframe's closed candles:
  - ema_posture:     price vs EMA50 vs EMA200 (posture string)
  - trend_direction: EMA50 slope over the recent window, as % of price per bar
  - swing_structure: HH/HL vs LH/LL vs mixed from the last two swing highs and
                     last two swing lows (reuses geometry._find_swings)

Timeframes are fixed per spec §1 — making them a config column is deferred to
ROADMAP Open Question #4.

Per-TF failures degrade honestly: a timeframe that can't be fetched or
classified is skipped; if none survive, return None (never fabricate).
"""

import asyncio
import logging

import numpy as np
import pandas as pd
import pandas_ta as ta

from app.data.compute_executor import executor as compute_executor
from app.data.geometry import _find_swings
from app.data.ohlcv import fetch_ohlcv

logger = logging.getLogger(__name__)

MIN_CANDLES   = 60    # enough for EMA50 + swings; EMA200 posture degrades gracefully
SLOPE_WINDOW  = 20    # bars of EMA50 used for the trend slope
FLAT_THR_PCT  = 0.03  # |EMA50 slope| below this % of price per bar → sideways

# Lookback per timeframe, sized so each TF gets ~500 candles (fetch_ohlcv
# floors the request at 500 anyway; this keeps the daily TF asking for enough).
_TF_LOOKBACK_DAYS = {'1h': 30, '4h': 90, '1d': 400}


def _classify_tf(tf: str, candles: list[dict]) -> dict | None:
    """Pure local classification of one timeframe's closed candles."""
    if not candles or len(candles) < MIN_CANDLES:
        return None

    closes = pd.Series([c['close'] for c in candles], dtype=float)
    highs  = np.array([c['high']   for c in candles], dtype=float)
    lows   = np.array([c['low']    for c in candles], dtype=float)
    price  = float(closes.iloc[-1])
    if price <= 0:
        return None

    # ── EMA posture ──────────────────────────────────────────────────────
    ema50_s  = ta.ema(closes, length=50)
    ema200_s = ta.ema(closes, length=200)
    ema50  = float(ema50_s.iloc[-1])  if ema50_s  is not None and ema50_s.notna().any()  else None
    ema200 = float(ema200_s.iloc[-1]) if ema200_s is not None and ema200_s.notna().any() else None

    if ema50 is not None and ema200 is not None:
        ema_posture = (
            f"price {'above' if price >= ema50 else 'below'} EMA50, "
            f"EMA50 {'above' if ema50 >= ema200 else 'below'} EMA200"
        )
    elif ema50 is not None:
        ema_posture = (
            f"price {'above' if price >= ema50 else 'below'} EMA50 "
            "(EMA200 unavailable — short history)"
        )
    else:
        ema_posture = 'unavailable'

    # ── Trend direction: EMA50 slope over the recent window ─────────────
    trend_direction = 'sideways'
    if ema50_s is not None:
        ema_vals = ema50_s.dropna().to_numpy()
        if len(ema_vals) >= SLOPE_WINDOW:
            window = ema_vals[-SLOPE_WINDOW:]
            slope_pct = (window[-1] - window[0]) / (SLOPE_WINDOW - 1) / price * 100
            if slope_pct > FLAT_THR_PCT:
                trend_direction = 'uptrend'
            elif slope_pct < -FLAT_THR_PCT:
                trend_direction = 'downtrend'

    # ── Swing structure: last two swing highs + last two swing lows ─────
    swing_structure = 'mixed'
    swing_highs, swing_lows = _find_swings(highs, lows)
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh = swing_highs[-1][1] > swing_highs[-2][1]
        hl = swing_lows[-1][1]  > swing_lows[-2][1]
        if hh and hl:
            swing_structure = 'HH/HL'
        elif not hh and not hl:
            swing_structure = 'LH/LL'

    return {
        'tf':              tf,
        'trend_direction': trend_direction,
        'ema_posture':     ema_posture,
        'swing_structure': swing_structure,
    }


async def fetch_mtf_structure(
    exchange_id: str,
    symbol: str,
    timeframes: list[str] = ['1h', '4h', '1d'],
) -> list[dict] | None:
    """
    Classify market structure on each timeframe.

    Returns a list of per-TF dicts
    ({'tf', 'trend_direction', 'ema_posture', 'swing_structure'});
    timeframes that fail to fetch/classify are skipped. None if all fail.
    """
    # Fetch every timeframe concurrently — these were previously awaited one at a
    # time, so a slow/failing exchange call for one TF serialized behind the
    # others (observed: ~20-40s per failing Hyperliquid OHLCV call × 3 TFs).
    loop = asyncio.get_running_loop()

    async def _fetch_one(tf: str) -> dict | None:
        try:
            lookback = _TF_LOOKBACK_DAYS.get(tf, 90)
            ohlcv = await fetch_ohlcv(exchange_id, symbol, tf, lookback)
            closed = ohlcv.get('closed_candles') if ohlcv else None
            # pandas_ta (ta.ema) is synchronous/CPU-bound — off the event loop,
            # same reasoning as node_ingest.py's indicator calls.
            entry = (
                await loop.run_in_executor(compute_executor, _classify_tf, tf, closed)
                if closed else None
            )
            if not entry:
                logger.warning("mtf_structure: no classification for %s %s %s",
                               exchange_id, symbol, tf)
            return entry
        except Exception as exc:
            logger.warning("mtf_structure error [%s %s %s]: %s",
                           exchange_id, symbol, tf, exc)
            return None

    entries = await asyncio.gather(*(_fetch_one(tf) for tf in timeframes))
    results = [e for e in entries if e]

    return results or None


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _test():
        result = await fetch_mtf_structure("binance", "BTC/USDT")
        print(json.dumps(result, indent=2))

    asyncio.run(_test())
