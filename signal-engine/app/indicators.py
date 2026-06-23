"""
Deterministic indicator computation for signal-engine.
RSI uses pandas-ta which implements Wilder's RMA smoothing — identical to ta.rsi() in Pine Script.
"""
import numpy as np
import pandas as pd
import pandas_ta as ta


def candles_to_series(candles: list[dict]) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Convert list of {t,o,h,l,c,v} dicts to (open, high, low, close) Series indexed by ms timestamp."""
    df = pd.DataFrame(candles)
    df = df.sort_values("t").reset_index(drop=True)
    idx = pd.to_datetime(df["t"], unit="ms", utc=True)
    return (
        pd.Series(df["o"].astype(float).values, index=idx),
        pd.Series(df["h"].astype(float).values, index=idx),
        pd.Series(df["l"].astype(float).values, index=idx),
        pd.Series(df["c"].astype(float).values, index=idx),
    )


def compute_rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder RMA RSI — matches TradingView ta.rsi(close, length)."""
    return ta.rsi(close, length=length)


def crossover(series: pd.Series, level: float) -> bool:
    """True if the last two values crossed ABOVE `level` (prev < level, curr >= level)."""
    if len(series) < 2:
        return False
    prev = series.iloc[-2]
    curr = series.iloc[-1]
    return (not np.isnan(prev)) and (not np.isnan(curr)) and prev < level and curr >= level


def crossunder(series: pd.Series, level: float) -> bool:
    """True if the last two values crossed BELOW `level` (prev >= level, curr < level)."""
    if len(series) < 2:
        return False
    prev = series.iloc[-2]
    curr = series.iloc[-1]
    return (not np.isnan(prev)) and (not np.isnan(curr)) and prev >= level and curr < level
