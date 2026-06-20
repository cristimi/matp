"""
Deterministic test-harness strategy: RSI(14) crossover/crossunder 50.
Entry signals only (exits deferred to Prompt 3).
Bracket spec is stored on shadow_signals for future use.

Bracket spec (fixed):
  TP1: +0.5% of fill, 50% size
  TP2: +1.0% of fill, 50% size
  Stop: -0.7% of fill
  Trail arm: +0.4% above fill before arming, then trail 0.3%
  BE: move stop to fill+0.1% after TP1 hit
  Condition close: if opposite RSI crossover signal fires
  Condition modify: if RSI > 75 (long) or RSI < 25 (short)
"""
import logging
from app.indicators import candles_to_series, compute_rsi, crossover, crossunder
from app.strategies.base import Signal

logger = logging.getLogger(__name__)

STRATEGY_ID = "tv_test_harness"
SIGNAL_SOURCE = "tv_test"
SYMBOL = "BTC-USDT"
TIMEFRAME = "1h"
RSI_LENGTH = 14
RSI_MID = 50.0
# Pine warmup: RSI needs at least length bars; give 5× for RMA convergence
WARMUP_BARS = RSI_LENGTH * 5  # 70


def _bracket_spec(side: str, entry_price: float) -> dict:
    """Build bracket spec jsonb for a given side and reference entry price."""
    direction = 1 if side == "long" else -1
    return {
        "tp1_offset_pct":   0.5,
        "tp1_size_pct":     50,
        "tp2_offset_pct":   1.0,
        "tp2_size_pct":     50,
        "stop_offset_pct":  0.7,
        "trail_arm_pct":    0.4,
        "trail_pct":        0.3,
        "be_offset_pct":    0.1,
        "be_trigger":       "tp1_hit",
        "condition_close":  "opposite_signal",
        "condition_modify_rsi_long":  75,
        "condition_modify_rsi_short": 25,
        "direction":        direction,
        "ref_entry_price":  entry_price,
    }


class TestHarnessStrategy:
    strategy_id = STRATEGY_ID
    symbol      = SYMBOL
    timeframe   = TIMEFRAME
    signal_source = SIGNAL_SOURCE
    warmup_bars = WARMUP_BARS

    # One-position-at-a-time: track synthetic position side in memory.
    # Engine resets this on startup; it is not persisted.
    def __init__(self):
        self._position_side: str | None = None  # "long" | "short" | None

    def evaluate(self, closed_candles: list[dict]) -> list[Signal]:
        if len(closed_candles) < WARMUP_BARS + 1:
            return []

        _, _, _, close = candles_to_series(closed_candles)
        rsi = compute_rsi(close, length=RSI_LENGTH)

        if rsi is None or rsi.isna().all():
            return []

        last_bar = closed_candles[-1]
        bar_time = last_bar["t"]
        bar_close = float(last_bar["c"])

        signals: list[Signal] = []

        cross_up   = crossover(rsi, RSI_MID)
        cross_down = crossunder(rsi, RSI_MID)

        if cross_up and self._position_side != "long":
            if self._position_side == "short":
                signals.append(Signal(
                    signal="close_short",
                    side="short",
                    symbol=SYMBOL,
                    signal_bar_time=bar_time,
                    bar_close_price=bar_close,
                    bracket_spec={},
                ))
                self._position_side = None

            signals.append(Signal(
                signal="open_long",
                side="long",
                symbol=SYMBOL,
                signal_bar_time=bar_time,
                bar_close_price=bar_close,
                bracket_spec=_bracket_spec("long", bar_close),
            ))
            self._position_side = "long"
            logger.info(
                "test_harness: open_long bar_time=%d close=%.2f rsi=%.2f",
                bar_time, bar_close, float(rsi.iloc[-1]),
            )

        elif cross_down and self._position_side != "short":
            if self._position_side == "long":
                signals.append(Signal(
                    signal="close_long",
                    side="long",
                    symbol=SYMBOL,
                    signal_bar_time=bar_time,
                    bar_close_price=bar_close,
                    bracket_spec={},
                ))
                self._position_side = None

            signals.append(Signal(
                signal="open_short",
                side="short",
                symbol=SYMBOL,
                signal_bar_time=bar_time,
                bar_close_price=bar_close,
                bracket_spec=_bracket_spec("short", bar_close),
            ))
            self._position_side = "short"
            logger.info(
                "test_harness: open_short bar_time=%d close=%.2f rsi=%.2f",
                bar_time, bar_close, float(rsi.iloc[-1]),
            )

        return signals
