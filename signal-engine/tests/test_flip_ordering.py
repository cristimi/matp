"""
Same-bar flip/exit ordering regression test (Phase 2 follow-up).

Drives the real app.engine.run_strategy_stream (fakes Redis/DB; no warmup, so every
candle goes through the live _entry_loop, not the bug-free warmup loop) through a
fixture with wiggle-driven flips, a stop-out that coincides with a fresh cross, and
multi-bar partial (tp1-then-tp2-hours-later) exits.

Bug under test: a close signal from evaluate() is always the close leg of a same-call
flip (evaluate() never emits a standalone close) -- evaluate() has already set
position.side to the new side by the time the caller processes that signal. Two call
sites used to call strategy.mark_flat() there, wiping position.side back to None right
after evaluate() set it: (1) the entry-loop's own signal-processing loop, and (2) the
RSI condition-modify block (which resolves the *previous* bracket and can legitimately
call mark_flat() for its own exit) when it ran *after* evaluate() had already decided a
fresh flip for the same bar. Both are now fixed (see engine.py's module docstring).

This test replays the full ordered shadow_signals stream and checks strict
sequential consistency: every close must match an actually-open side (no phantom
close for a position the engine wrongly believes doesn't exist), no duplicate open
while a side is already open, and partial exits (tp1 now, tp2 bars later) correctly
keep the position "open" in between.

Run (inside the signal-engine container, cwd /app):
    python -m tests.test_flip_ordering
"""
import asyncio
import sys

import app.engine as engine_mod
from app.strategies.test_harness import TestHarnessStrategy, WARMUP_BARS

HOUR_MS = 3_600_000


def make_candle(t_ms, o, h, l, c):
    return {"t": t_ms, "o": o, "h": h, "l": l, "c": c, "v": 1.0}


def build_candles() -> list[dict]:
    """Wiggle warmup (frequent flips) + sustained downtrend (a stop-out coincides with
    a fresh cross) + sustained uptrend + sustained downtrend (multi-bar tp1/tp2 exits)."""
    candles = []
    t = 0
    price = 100.0
    for i in range(WARMUP_BARS + 5):
        o = price
        c = price + (0.01 if i % 2 == 0 else -0.01)
        candles.append(make_candle(t, o, max(o, c), min(o, c), c))
        price = c
        t += HOUR_MS
    for _ in range(20):
        o = price
        c = price - 1.0
        candles.append(make_candle(t, o, o, c, c))
        price = c
        t += HOUR_MS
    for _ in range(40):
        o = price
        c = price + 1.0
        candles.append(make_candle(t, o, c, o, c))
        price = c
        t += HOUR_MS
    for _ in range(20):
        o = price
        c = price - 1.0
        candles.append(make_candle(t, o, o, c, c))
        price = c
        t += HOUR_MS
    return candles


ALL_CANDLES = build_candles()
captured: list[tuple] = []


async def fake_read_stream_history(redis_client, exchange, symbol, tf, count=500):
    return []  # no warmup -- everything goes through the live _entry_loop


async def fake_subscribe_closed_bars(redis_client, exchange, symbol, tf):
    if tf == "1h":
        for c in ALL_CANDLES:
            yield c
    await asyncio.Event().wait()
    yield  # pragma: no cover -- unreachable, keeps this an async generator


async def fake_read_forming_candle(redis_client, exchange, symbol, tf):
    return None


async def fake_store_shadow_signal(pool, strategy_id, signal_source, sig, mode):
    captured.append((sig.signal, sig.signal_bar_time, sig.exit_reason, sig.size_pct))


def check_sequential_consistency(rows: list[tuple]) -> list[str]:
    """Replays the ordered signal stream, tracking the believed-open side and how much
    of it remains (size_pct legs accumulate). Returns a list of problem descriptions."""
    tracked_side = None
    remaining_pct = 0.0
    problems = []
    for signal, bar_time, exit_reason, size_pct in rows:
        if signal in ("open_long", "open_short"):
            want_side = "long" if signal == "open_long" else "short"
            if tracked_side == want_side:
                problems.append(f"duplicate open: already {tracked_side} at bar_time={bar_time}")
            tracked_side = want_side
            remaining_pct = 100.0
        elif signal in ("close_long", "close_short"):
            closing_side = "long" if signal == "close_long" else "short"
            if tracked_side != closing_side:
                problems.append(
                    f"phantom/mismatched close: {signal} at bar_time={bar_time} "
                    f"exit_reason={exit_reason} but tracked_side was {tracked_side!r}"
                )
            else:
                remaining_pct -= size_pct if size_pct is not None else remaining_pct
                if remaining_pct <= 1e-6:
                    tracked_side = None
    return problems


def run() -> int:
    engine_mod.read_stream_history = fake_read_stream_history
    engine_mod.subscribe_closed_bars = fake_subscribe_closed_bars
    engine_mod.read_forming_candle = fake_read_forming_candle
    engine_mod.store_shadow_signal = fake_store_shadow_signal

    strategy = TestHarnessStrategy()  # entry_trigger stays 'bar_close' (class default)

    async def drive() -> None:
        task = asyncio.create_task(engine_mod.run_strategy_stream(None, None, strategy, "shadow"))
        await asyncio.sleep(3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())

    problems = check_sequential_consistency(captured)

    def check(label: str, cond: bool, detail: str = "") -> int:
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
        return 0 if cond else 1

    failures = 0
    failures += check("signal stream captured", len(captured) > 0)
    failures += check(
        "every close matches an actually-open side, no duplicate opens",
        not problems, "; ".join(problems),
    )

    print()
    if failures == 0:
        print(f"ALL CASES PASSED (total signals: {len(captured)})")
    else:
        print(f"{failures} CASE(S) FAILED")
    return failures


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
