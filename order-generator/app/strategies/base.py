"""
Abstract base class for all trading strategies.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class Candle:
    timestamp: int    # Unix ms
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float


@dataclass
class Signal:
    side:     str              # "buy" | "sell"
    signal:   str              # "open_long" | "close_long" | "open_short" | "close_short"
    size:     Decimal
    tp_price: Optional[Decimal] = None
    sl_price: Optional[Decimal] = None


class BaseStrategy(ABC):
    strategy_id: str
    name:        str
    symbol:      str
    interval:    str   # "1m", "5m", "1h", etc.
    account_id:  str   # references exchange_accounts.id
    platform:    str   # "auto" | "blofin" | "hyperliquid" (legacy, kept for compat)
    enabled:     bool

    def __init__(self, strategy_id: str, name: str, symbol: str,
                 interval: str, account_id: str, platform: str,
                 enabled: bool, params: dict):
        self.strategy_id = strategy_id
        self.name = name
        self.symbol = symbol
        self.interval = interval
        self.account_id = account_id
        self.platform = platform
        self.enabled = enabled
        self.params = params
        self.last_signal_time: Optional[int] = None

    @abstractmethod
    def on_candle(self, candle: Candle) -> Optional[Signal]:
        """Called on each new OHLCV candle. Return a Signal or None."""
        ...

    def __repr__(self) -> str:
        return (
            f"<Strategy id={self.strategy_id} name={self.name} "
            f"symbol={self.symbol} interval={self.interval} enabled={self.enabled}>"
        )
