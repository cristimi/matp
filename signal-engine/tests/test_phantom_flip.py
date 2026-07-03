"""
Phantom-flip regression test (Phase 0, Step 1).

Reproduces the exact scenario from the parity report:
  1. RSI crossunder opens a short (open_short).
  2. The bracket fully closes via legitimate exit legs (tp1, then trail for the
     remainder) -- exactly like `_near_tick_loop` / `_safety_net_loop` would drive
     it in engine.py -- and `strategy.mark_flat()` runs, same as those paths do.
  3. A later RSI crossover fires on a subsequent bar.

Bug under test: before `mark_flat()` existed, the strategy's own `_position_side`
bookkeeping did not know the bracket had already closed the position, so step 3
would emit a *phantom* `close_short` (nothing is open to close) alongside the
correct `open_long`. This test asserts exactly one `open_long` and no
`close_short` is emitted at step 3.

No Redis, no DB, no engine.py -- pure strategy + exits unit test.

Run (inside the signal-engine container, cwd /app):
    python -m tests.test_phantom_flip
"""
import sys

from app.exits import BracketState
from app.strategies.test_harness import TestHarnessStrategy, WARMUP_BARS

HOUR_MS = 3_600_000


def make_candle(t_ms: int, o: float, h: float, l: float, c: float) -> dict:
    return {"t": t_ms, "o": o, "h": h, "l": l, "c": c, "v": 1.0}


def build_candles() -> list[dict]:
    """Deterministic 1h candle series: flat warmup (RSI defined, hovering near 50),
    then a sustained downtrend (drives RSI under 50 -> open_short), then a
    sustained uptrend (drives RSI back over 50 -> open_long)."""
    candles = []
    t = 0
    price = 100.0

    # Warmup: small alternating wiggle so RSI is defined without trending either way.
    for i in range(WARMUP_BARS + 5):
        o = price
        c = price + (0.01 if i % 2 == 0 else -0.01)
        candles.append(make_candle(t, o, max(o, c), min(o, c), c))
        price = c
        t += HOUR_MS

    # Sustained downtrend -> RSI crosses under 50.
    for _ in range(20):
        o = price
        c = price - 1.0
        candles.append(make_candle(t, o, o, c, c))
        price = c
        t += HOUR_MS

    # Sustained uptrend -> RSI crosses back over 50.
    for _ in range(40):
        o = price
        c = price + 1.0
        candles.append(make_candle(t, o, c, o, c))
        price = c
        t += HOUR_MS

    return candles


def drive_bracket_to_close(bracket: BracketState, entry: float, d: int) -> None:
    """Fire tp1, then a trailing-stop exit for the remainder -- the exact
    'tp1 + trail' scenario named in the prompt. Independent of the RSI candle
    feed; mirrors what the near-tick / safety-net loops would do to `bracket`."""
    # Point-price updates (high=low=price), same convention as engine.py's
    # `_near_tick_loop`. Move 1: 0.6% favorable -- clears tp1 (0.5%) and arms the
    # trail (0.4%), short of tp2 (1.0%) and short of the raw stop (0.7% unfavorable).
    # Same sign convention as exits._levels(): favorable = entry * (1 + d*pct/100).
    peak = entry * (1 + d * 0.006)
    legs1 = bracket.update(high=peak, low=peak, price=peak)
    assert any(l["exit_reason"] == "tp1" for l in legs1), f"setup failed: expected tp1, got {legs1}"
    assert not bracket.closed, "setup failed: bracket closed after tp1 alone"

    # Move 2: retrace 0.35% off the peak -- past the 0.3% trailing stop, but not
    # far enough to breach the raw BE stop (0.1% favorable of entry) -> reason="trail".
    p2 = peak * (1 - d * 0.0035)
    legs2 = bracket.update(high=p2, low=p2, price=p2)
    assert any(l["exit_reason"] == "trail" for l in legs2), f"setup failed: expected trail, got {legs2}"
    assert bracket.closed, "setup failed: bracket did not close after trail leg"


def run() -> int:
    strategy = TestHarnessStrategy()
    all_candles = build_candles()

    fed: list[dict] = []
    saw_open_short = False
    position_closed_by_bracket = False
    post_close_signals = None

    for candle in all_candles:
        fed.append(candle)
        sigs = strategy.evaluate(fed)

        if not saw_open_short:
            for sig in sigs:
                if sig.signal == "open_short":
                    saw_open_short = True
                    entry = sig.bar_close_price
                    d = int(sig.bracket_spec["direction"])
                    bracket = BracketState(sig.bracket_spec, entry)
                    drive_bracket_to_close(bracket, entry, d)
                    # Exactly what engine.py's exit paths do when bracket.closed fires.
                    strategy.mark_flat()
                    position_closed_by_bracket = True
                    break
            continue

        if position_closed_by_bracket and post_close_signals is None and sigs:
            post_close_signals = sigs
            break

    failures = 0

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
        if not cond:
            failures += 1

    check("open_short fired during downtrend", saw_open_short)
    check("bracket fully closed via tp1+trail, mark_flat() ran", position_closed_by_bracket)
    check("a signal fired after the bracket close (later crossover)", post_close_signals is not None)

    if post_close_signals is not None:
        kinds = [s.signal for s in post_close_signals]
        check("exactly one open_long, no phantom close_short",
              kinds == ["open_long"], f"got {kinds}")
    else:
        kinds = []
        check("exactly one open_long, no phantom close_short", False, "no signals observed")

    print()
    if failures == 0:
        print(f"ALL CASES PASSED (post-close signals: {kinds})")
    else:
        print(f"{failures} CASE(S) FAILED")
    return failures


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
