# Phase 2 follow-up — same-bar flip/exit ordering fix (mark_flat clobber)

Branch: `main`. Signal-engine is shadow-only — no live-trading impact. Follow-up to
`2026-07-04_phase2_intrabar_entries.md`: fixes a pre-existing bug discovered while
verifying that phase, which also affects the `bar_close` (default) path. Deliberately
committed separately, since it changes `bar_close` behavior on live flips — unlike
Phase 2 itself, which was proven byte-identical for `bar_close`.

## The bug

`Strategy.evaluate()` never emits a standalone close signal — a close is always the
first half of a same-call flip (`[close_X, open_Y]`), and `evaluate()` has already set
`position.side` to the new side (`Y`) by the time the caller processes that returned
list. Two places in `engine.py` called `strategy.mark_flat()` when they saw a close
signal, which resets `position.side` back to `None` — clobbering the value `evaluate()`
had just set:

1. **`_entry_loop`'s own signal-processing loop** (the `for sig in sigs:` block) — on
   processing the `close_X` half of a flip it just received from `evaluate()`, it called
   `mark_flat()`, wiping `side` right before the very next line processes `open_Y`
   (which only sets `bracket`/`entry_bar_time`, never `side`). Net effect: `side` ends up
   `None` after almost every flip, instead of the new side.
2. **The 1h RSI condition-modify block**, which resolves the *previous* bracket using
   this bar's close+RSI and can legitimately exit it (stop/condition-modify) — this
   `mark_flat()` call is correct in isolation, but it ran *after* `evaluate()` had
   already decided a fresh flip for the same bar, so it could also wipe a side that
   `evaluate()` had just set for a brand-new position opened this same bar.

Symptom either way: the next bar's opposite cross sees `position.side == None` instead
of the real side, so `evaluate()`'s flip guard (`if self.position.side == "long": ...
append close_long`) is skipped — the phantom position's close never fires, only the new
open. Same failure class as `tests/test_phantom_flip.py` (Phase 0), but caused by the
engine's own bookkeeping rather than an external bracket exit.

## Reproduction (before fix)

Built a scratch harness driving the real `app.engine.run_strategy_stream` with no
warmup (so every candle goes through the live `_entry_loop`, not the bug-free warmup
loop, which already avoids this via `position.bracket = None` instead of `mark_flat()`
on close). A sequential consistency check over the ordered `shadow_signals` stream
(tracks the believed-open side, allowing multi-bar partial tp1/tp2 exits) found:

```
FAIL: 4 bar(s) had a standalone open with no paired close (phantom flip via clobber).
```

## The fix

`signal-engine/app/engine.py`, `_entry_loop`:

1. On a close signal, clear only `position.bracket` / `position.entry_bar_time` —
   never call `mark_flat()` there (matches the warmup loop's existing, already-correct
   pattern, documented at Phase 0).
2. Reordered the per-bar sequence: a cheap `strategy.evaluate(candles,
   detect_entries=False)` pre-pass refreshes `last_rsi` first, then the RSI
   condition-modify block resolves any existing bracket using this bar's close+RSI
   (so a same-bar `mark_flat()` from an old-bracket exit happens *before* `evaluate()`
   decides anything new), and only then does the real `strategy.evaluate(candles,
   detect_entries=not is_intrabar)` call run for entries/flips.

Module docstring in `engine.py` updated with the full reasoning.

## Verification

New regression test `signal-engine/tests/test_flip_ordering.py`: same no-warmup
harness, replays the full ordered signal stream through a sequential ground-truth
checker (every close must match an actually-open side; no duplicate opens; partial
exits spanning multiple bars correctly keep the position open in between).

```
$ python -m tests.test_flip_ordering
  [PASS] signal stream captured
  [PASS] every close matches an actually-open side, no duplicate opens

ALL CASES PASSED (total signals: 19)
```

Re-ran the existing suite after the fix:

```
$ python -m tests.test_phantom_flip
  [PASS] open_short fired during downtrend
  [PASS] bracket fully closed via tp1+trail, mark_flat() ran
  [PASS] a signal fired after the bracket close (later crossover)
  [PASS] exactly one open_long, no phantom close_short

ALL CASES PASSED (post-close signals: ['open_long'])

$ python -m app.exits
... (8 cases)
ALL CASES PASSED
```

Also re-ran the Phase 2 intrabar end-to-end proof (deterministic fixture through
`run_strategy_stream` with `entry_trigger='intrabar'`) against this fixed `engine.py` —
still passes: exactly one flip fires mid-hour, one-per-hour cap holds, bracket built
from the forming candle's close. This fix and Phase 2's intrabar loop share the same
close-handling pattern now (`_intrabar_entry_loop` already used the fixed pattern from
the start — see the original Phase 2 report).

## Live redeploy verification

```
$ ./scripts/redeploy.sh signal-engine
✓ signal-engine redeployed.
```

```
[INFO] app.engine: engine: warmup complete strategy=tv_test_harness bars=500 position=long bracket=active
[INFO] app.engine: engine: catch-up exit strategy=tv_test_harness reason=tp1 at bar_t=1783026720000 price=61517.7000
[INFO] app.engine: engine: catch-up exit strategy=tv_test_harness reason=tp2 at bar_t=1783026720000 price=61517.7000
[INFO] app.engine: engine: entering live subscription strategy=tv_test_harness BTC-USDT 1h
[INFO] app.engine: engine: near-tick monitor started strategy=tv_test_harness
[INFO] app.engine: engine: 1m safety-net subscribed strategy=tv_test_harness
[INFO] app.engine: engine: intrabar entry loop started strategy=tv_test_harness
```

`docker compose logs signal-engine | grep -iE "error|traceback|exception"` — empty.
`tv_test_harness` remains on `entry_trigger='intrabar'` (unchanged from Phase 2).

## Files changed

- `signal-engine/app/engine.py` — `_entry_loop`'s close-signal handling and per-bar
  ordering (RSI-modify before entry detection); module docstring.
- `signal-engine/tests/test_flip_ordering.py` — new regression test.

No exit calculation logic changed (`app/exits.py` untouched). No exchange calls. Shadow
only.
