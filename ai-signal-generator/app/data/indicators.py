"""
Technical indicator computation using pandas-ta.
All computation is synchronous (CPU-bound).
"""

import logging

import numpy as np
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)


def _to_df(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df = df.set_index('timestamp').sort_index()
    return df.rename(columns={
        'open': 'Open', 'high': 'High', 'low': 'Low',
        'close': 'Close', 'volume': 'Volume',
    })


def compute_indicators(candles: list[dict], enabled: list[str]) -> dict:
    """
    Compute technical indicators from a candles list.

    Always computes: ATR(14), support_1, resistance_1.
    Conditionally computes based on `enabled` list:
        RSI, MACD, EMA50, EMA200, EMA cross status, BB, VWAP.

    Returns empty dict on error.
    """
    if not candles or len(candles) < 10:
        return {}

    try:
        df = _to_df(candles)
        close  = df['Close']
        high   = df['High']
        low    = df['Low']
        volume = df['Volume']
        result: dict = {}

        # ── RSI ─────────────────────────────────────────────────────────────
        if 'RSI' in enabled:
            try:
                rsi_s = ta.rsi(close, length=14)
                if rsi_s is not None and rsi_s.notna().any():
                    v = float(rsi_s.iloc[-1])
                    result['rsi_14'] = round(v, 2)
                    if v < 30:
                        result['rsi_interpretation'] = 'oversold'
                    elif v > 70:
                        result['rsi_interpretation'] = 'overbought'
                    else:
                        result['rsi_interpretation'] = 'neutral'
            except Exception as exc:
                logger.warning("RSI failed: %s", exc)

        # ── MACD ─────────────────────────────────────────────────────────────
        if 'MACD' in enabled:
            try:
                macd_df = ta.macd(close)
                if macd_df is not None and not macd_df.empty:
                    hist_col = next((c for c in macd_df.columns if c.startswith('MACDh')), None)
                    if hist_col:
                        hist = macd_df[hist_col].dropna()
                        result['macd_hist'] = round(float(hist.iloc[-1]), 6)
                        signs = (hist > 0).astype(int)
                        crosses = signs.diff().abs()
                        cross_idx = crosses[crosses > 0].index
                        if len(cross_idx):
                            last = cross_idx[-1]
                            bars = int(len(hist) - hist.index.get_loc(last) - 1)
                            result['macd_signal_bars'] = bars
                        else:
                            result['macd_signal_bars'] = 0
            except Exception as exc:
                logger.warning("MACD failed: %s", exc)

        # ── EMA50 / EMA200 / cross status ────────────────────────────────────
        ema50_s = ema200_s = None

        if 'EMA50' in enabled:
            try:
                ema50_s = ta.ema(close, length=50)
                if ema50_s is not None and ema50_s.notna().any():
                    result['ema_50'] = round(float(ema50_s.iloc[-1]), 6)
            except Exception as exc:
                logger.warning("EMA50 failed: %s", exc)

        if 'EMA200' in enabled:
            try:
                ema200_s = ta.ema(close, length=200)
                if ema200_s is not None and ema200_s.notna().any():
                    result['ema_200'] = round(float(ema200_s.iloc[-1]), 6)
            except Exception as exc:
                logger.warning("EMA200 failed: %s", exc)

        if ema50_s is not None and ema200_s is not None:
            try:
                diff = (ema50_s - ema200_s).dropna()
                if len(diff) >= 2:
                    prev_above = diff.iloc[-2] > 0
                    curr_above = diff.iloc[-1] > 0
                    if not prev_above and curr_above:
                        result['ema_cross_status'] = 'golden_cross'
                    elif prev_above and not curr_above:
                        result['ema_cross_status'] = 'death_cross'
                    elif curr_above:
                        result['ema_cross_status'] = 'above'
                    else:
                        result['ema_cross_status'] = 'below'
            except Exception as exc:
                logger.warning("EMA cross failed: %s", exc)

        # ── Bollinger Bands ──────────────────────────────────────────────────
        if 'BB' in enabled:
            try:
                bb_df = ta.bbands(close, length=20, std=2)
                if bb_df is not None and not bb_df.empty:
                    upper_col = next((c for c in bb_df.columns if c.startswith('BBU')), None)
                    mid_col   = next((c for c in bb_df.columns if c.startswith('BBM')), None)
                    lower_col = next((c for c in bb_df.columns if c.startswith('BBL')), None)
                    if upper_col and mid_col and lower_col:
                        bb_upper = float(bb_df[upper_col].iloc[-1])
                        bb_mid   = float(bb_df[mid_col].iloc[-1])
                        bb_lower = float(bb_df[lower_col].iloc[-1])
                        result['bb_upper'] = round(bb_upper, 6)
                        result['bb_mid']   = round(bb_mid,   6)
                        result['bb_lower'] = round(bb_lower, 6)
                        price = float(close.iloc[-1])
                        span  = bb_upper - bb_lower
                        if span > 0:
                            pos = (price - bb_lower) / span
                            if pos > 0.8:
                                result['bb_interpretation'] = 'near upper band (overbought)'
                            elif pos < 0.2:
                                result['bb_interpretation'] = 'near lower band (oversold)'
                            else:
                                result['bb_interpretation'] = 'mid-band (neutral)'
                        else:
                            result['bb_interpretation'] = 'squeeze'
            except Exception as exc:
                logger.warning("BB failed: %s", exc)

        # ── VWAP ────────────────────────────────────────────────────────────
        if 'VWAP' in enabled:
            try:
                typical = (high + low + close) / 3
                vwap = (typical * volume).cumsum() / volume.cumsum()
                vwap_val = float(vwap.iloc[-1])
                price = float(close.iloc[-1])
                result['vwap'] = round(vwap_val, 6)
                result['vwap_deviation_pct'] = round(
                    (price - vwap_val) / vwap_val * 100 if vwap_val else 0.0, 2
                )
                result['vwap_direction'] = 'above' if price >= vwap_val else 'below'
            except Exception as exc:
                logger.warning("VWAP failed: %s", exc)

        # ── ATR (always) ─────────────────────────────────────────────────────
        try:
            atr_s = ta.atr(high, low, close, length=14)
            if atr_s is not None and atr_s.notna().any():
                atr_val = float(atr_s.iloc[-1])
                price   = float(close.iloc[-1])
                result['atr_14'] = round(atr_val, 6)
                result['atr_pct_of_price'] = round(
                    atr_val / price * 100 if price else 0.0, 3
                )
        except Exception as exc:
            logger.warning("ATR failed: %s", exc)

        # ── Support / Resistance (always) ────────────────────────────────────
        try:
            window = min(20, len(close))
            result['support_1']    = round(float(low.rolling(window).min().iloc[-1]),  6)
            result['resistance_1'] = round(float(high.rolling(window).max().iloc[-1]), 6)
        except Exception as exc:
            logger.warning("Support/resistance failed: %s", exc)

        # ── Volume vs average (always) ───────────────────────────────────────
        # Skip volume.iloc[-1]: exchange always returns the current incomplete
        # candle as the last entry. Its volume is a fraction of a full candle,
        # which makes the % look absurdly negative. Use the last CLOSED candle.
        try:
            completed = volume.iloc[:-1]
            vol_window = min(20, len(completed))
            if vol_window > 1:
                avg_vol = float(completed.rolling(vol_window).mean().iloc[-1])
                if avg_vol > 0:
                    result['volume_vs_avg_pct'] = round(
                        (float(completed.iloc[-1]) / avg_vol - 1) * 100, 1
                    )
        except Exception as exc:
            logger.warning("Volume vs avg failed: %s", exc)

        return result

    except Exception as exc:
        logger.warning("compute_indicators error: %s", exc)
        return {}


if __name__ == "__main__":
    import json
    import random
    from datetime import datetime, timezone, timedelta

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    base = 65000.0
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    candles = []
    for i in range(250):
        p = base + random.gauss(0, 800)
        candles.append({
            'timestamp': now_ms - (250 - i) * 3_600_000,
            'open':   p,
            'high':   p + abs(random.gauss(0, 300)),
            'low':    p - abs(random.gauss(0, 300)),
            'close':  p + random.gauss(0, 150),
            'volume': random.uniform(50, 500),
        })

    enabled = ['RSI', 'MACD', 'EMA50', 'EMA200', 'BB', 'VWAP']
    result = compute_indicators(candles, enabled)
    print(json.dumps(result, indent=2))
