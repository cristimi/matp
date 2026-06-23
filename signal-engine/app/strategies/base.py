"""Strategy interface for signal-engine."""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Signal:
    signal: str          # open_long | close_long | open_short | close_short
    side: str            # long | short
    symbol: str
    signal_bar_time: int  # ms epoch of the closed bar's open-time
    bar_close_price: float
    bracket_spec: dict = field(default_factory=dict)
    exit_reason: str | None = None   # tp1 | tp2 | stop | be_stop | trail; None for entries
    size_pct: float | None = None    # fraction of position exited on this leg; None for entries


class Strategy(Protocol):
    strategy_id: str
    symbol: str
    timeframe: str
    signal_source: str
    # Minimum bars needed before first valid signal (warmup)
    warmup_bars: int

    def evaluate(self, closed_candles: list[dict]) -> list[Signal]:
        """
        Evaluate all closed candles and return any signals for the most-recent closed bar.
        Must be called with candles sorted oldest-first. Returns [] if no signal.
        """
        ...
