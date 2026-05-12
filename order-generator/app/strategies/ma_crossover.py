"""
Moving Average Crossover Strategy.
Golden cross (fast MA crosses above slow MA) → open_long
Death cross (fast MA crosses below slow MA) → open_short
"""

from collections import deque
from decimal import Decimal
from typing import Optional

from app.strategies.base import BaseStrategy, Candle, Signal


class MaCrossoverStrategy(BaseStrategy):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        params = self.params
        self.fast_period: int = params.get("fast_period", 9)
        self.slow_period: int = params.get("slow_period", 21)
        self.size:        Decimal = Decimal(str(params.get("size", "0.01")))

        self._closes: deque = deque(maxlen=self.slow_period)
        self._prev_fast: Optional[float] = None
        self._prev_slow: Optional[float] = None

    def _sma(self, n: int) -> Optional[float]:
        closes = list(self._closes)
        if len(closes) < n:
            return None
        return sum(closes[-n:]) / n

    def on_candle(self, candle: Candle) -> Optional[Signal]:
        self._closes.append(candle.close)

        fast = self._sma(self.fast_period)
        slow = self._sma(self.slow_period)

        if fast is None or slow is None:
            return None

        signal = None

        if self._prev_fast is not None and self._prev_slow is not None:
            # Golden cross
            if self._prev_fast <= self._prev_slow and fast > slow:
                signal = Signal(side="buy", signal="open_long", size=self.size)
            # Death cross
            elif self._prev_fast >= self._prev_slow and fast < slow:
                signal = Signal(side="sell", signal="open_short", size=self.size)

        self._prev_fast = fast
        self._prev_slow = slow
        return signal
