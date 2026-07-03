"""
Strategy interface for signal-engine.

PositionState is the single authoritative record of a running strategy's current
position (Phase 0 consolidation): one `side`, one active `bracket`, one
`entry_bar_time`. It lives on the strategy instance (`strategy.position`) and is
shared by the engine's entry-loop, near-tick, safety-net, and catch-up code paths
-- there is no second, hand-synced copy anywhere. `Strategy.mark_flat()` is the
only way to clear it back to flat; nothing else should reset `side` to None or
null out `bracket`/`entry_bar_time` directly.
"""
from dataclasses import dataclass, field
from typing import Protocol

from app.exits import BracketState


@dataclass
class PositionState:
    side: str | None = None            # "long" | "short" | None (flat)
    bracket: BracketState | None = None
    entry_bar_time: int | None = None


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
    # Single source of truth for this strategy's current position (see module docstring).
    position: PositionState

    def evaluate(self, closed_candles: list[dict]) -> list[Signal]:
        """
        Evaluate all closed candles and return any signals for the most-recent closed bar.
        Must be called with candles sorted oldest-first. Returns [] if no signal.
        """
        ...

    def mark_flat(self) -> None:
        """Reset `position` to fully flat (side, bracket, entry_bar_time all cleared).
        Called by the engine whenever a bracket exit closes the position outside
        of evaluate() (near-tick, safety-net, catch-up, RSI condition-modify)."""
        ...
