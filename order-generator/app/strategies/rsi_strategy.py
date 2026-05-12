"""
RSI Crossover Strategy.
Generates long signals when RSI crosses below oversold,
and short/close signals when RSI crosses above overbought.
"""

from collections import deque
from decimal import Decimal
from typing import Optional

from app.strategies.base import BaseStrategy, Candle, Signal


class RsiStrategy(BaseStrategy):
    """
    Simple RSI strategy:
    - RSI crosses below oversold threshold → open_long signal
    - RSI crosses above overbought threshold → open_short signal
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        params = self.params
        self.period:     int = params.get("period", 14)
        self.oversold:   float = params.get("oversold", 30.0)
        self.overbought: float = params.get("overbought", 70.0)
        self.size:       Decimal = Decimal(str(params.get("size", "0.01")))
        self.leverage:   int = params.get("leverage", 10)

        self._closes: deque = deque(maxlen=self.period + 1)
        self._prev_rsi: Optional[float] = None

    def _compute_rsi(self) -> Optional[float]:
        closes = list(self._closes)
        if len(closes) < self.period + 1:
            return None

        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains) / self.period
        avg_loss = sum(losses) / self.period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def on_candle(self, candle: Candle) -> Optional[Signal]:
        self._closes.append(candle.close)
        rsi = self._compute_rsi()

        if rsi is None:
            self._prev_rsi = rsi
            return None

        signal = None

        if self._prev_rsi is not None:
            # Crossed below oversold → open long
            if self._prev_rsi >= self.oversold > rsi:
                signal = Signal(
                    side="buy",
                    signal="open_long",
                    size=self.size,
                )
            # Crossed above overbought → open short
            elif self._prev_rsi <= self.overbought < rsi:
                signal = Signal(
                    side="sell",
                    signal="open_short",
                    size=self.size,
                )

        self._prev_rsi = rsi
        return signal
