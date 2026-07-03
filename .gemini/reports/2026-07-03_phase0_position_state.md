# Phase 0 (intrabar prep) — position-state consolidation + phantom-flip proof

Branch: `main`. Signal-engine is shadow-only (writes `shadow_signals`, never POSTs to
order-listener) — no live trading impact. This was a correctness proof + internal
refactor; shadow output is proven byte-for-byte identical before/after.

## Step 1 — Phantom-flip test (passing)

New test: `signal-engine/tests/test_phantom_flip.py`. Reproduces: open short via RSI
crossunder → bracket fully closes via a real `tp1` then `trail` exit (same point-price
mechanics as `_near_tick_loop`) → `mark_flat()` runs → later RSI crossover. Asserts
exactly one `open_long` and no phantom `close_short`.

Run inside the signal-engine container (`python -m tests.test_phantom_flip`):

```
  [PASS] open_short fired during downtrend
  [PASS] bracket fully closed via tp1+trail, mark_flat() ran
  [PASS] a signal fired after the bracket close (later crossover)
  [PASS] exactly one open_long, no phantom close_short

ALL CASES PASSED (post-close signals: ['open_long'])
```

The existing phantom-flip handling (`mark_flat()` on all four bracket-exit paths) was
already correct. Per the prompt's escape hatch, this alone would justify stopping — but
the dual-state design (strategy's own `_position_side` + engine's local `bh` dict,
hand-synced across 5 call sites) is real ongoing risk for the intrabar work ahead, so
Step 2 consolidation was carried out as well.

## Step 2 — Single authoritative position state

Added `PositionState` (dataclass: `side`, `bracket`, `entry_bar_time`) to
`app/strategies/base.py`, owned by the strategy instance as `strategy.position`.
Removed the parallel bookkeeping entirely:

- `TestHarnessStrategy._position_side` → `self.position.side` (written only inside
  `evaluate()` for entries/internal flips).
- `engine.py`'s local `bh: dict` → removed; all four exit paths (near-tick,
  safety-net, catch-up, 1h RSI-modify) and the warmup/entry loops now read and write
  `strategy.position` directly. No second copy exists anywhere.
- `mark_flat()` now clears all three fields (`side`, `bracket`, `entry_bar_time`) in
  one call, replacing what used to be 3 lines duplicated at 5 call sites.
- `Strategy` Protocol extended with `position: PositionState` and `mark_flat()`.
- Module docstrings on `engine.py` and `strategies/base.py` document this as the
  single source of truth for future (intrabar) work to build on.

### Two real bugs found and fixed while proving equivalence (Step 3)

Naively merging the two copies was **not** behavior-preserving — the equivalence
diff (below) caught two genuine timing issues:

1. **Warmup loop clobber**: I initially had the warmup loop call `strategy.mark_flat()`
   on close signals, mirroring the (correct) live entry-loop behavior. But during
   warmup, a close is always paired with an open in the *same* `evaluate()` call (a
   flip) and `evaluate()` has already set `position.side` to the new side by the time
   the engine's loop runs. Calling `mark_flat()` there clobbered it back to `None`.
   Fix: warmup's close branch only clears the engine-owned `bracket`/`entry_bar_time`
   fields, never touches `side` (matching the original `bh`-only clear).

2. **Exit-leg side attribution race**: `_store_exit_leg` used to read the engine's own
   `bh["side"]`, which stayed pinned to the *closing* bracket's side until the engine
   itself replaced it. Once `side` became a single shared field, a same-bar flip
   (`evaluate()` already deciding "short" for the new position) could be visible
   *before* the engine finished attributing the old (long) bracket's exit leg —
   producing `close_short` instead of `close_long` for a stop-out that actually
   belonged to the long bracket. Fix: added a `side` property to `BracketState`
   (`app/exits.py`), derived from its own fixed `direction` at construction time, and
   changed `_store_exit_leg` to read `bracket.side` instead of `strategy.position.side`.
   This is arguably a better single-source-of-truth too — the bracket's side is
   intrinsic to the bracket, not a separately-tracked value that can drift.

## Step 3 — Equivalence proof (identical shadow output)

Harness (scratch-only, not committed): ran the real `app.engine.run_strategy_stream`
end-to-end with Redis/DB replaced by fakes, against a fixed 1h candle fixture (wiggle
warmup + downtrend/uptrend cycles + explicit tp1/trail moves), capturing every
`store_shadow_signal` call as a tuple. Ran once against the pre-refactor code (git
HEAD) and once against the refactored code, byte-diffed the output.

```
TOTAL=20 (both runs)
--- diff before vs after ---
DIFF EMPTY -- IDENTICAL
```

Also re-ran the phantom-flip test (still green) and `python -m app.exits` (the
existing deterministic `BracketState` self-test, unaffected by the new `side`
property):

```
ALL CASES PASSED
```

### Live redeploy verification

Redeployed via `./scripts/redeploy.sh signal-engine`. Container recreated, warmup
replayed 500 real bars, catch-up correctly replayed 1m history through an open
bracket (tp1 + tp2), entered live subscription with no errors:

```
[INFO] app.engine: engine: warmup complete strategy=tv_test_harness bars=500 position=long bracket=active
[INFO] app.redis_reader: redis_reader: loaded 2000 historical bars blofin BTC-USDT 1m
[WARNING] app.engine: engine: catch-up partial — earliest 1m bar t=1782987780000 is after entry+1bar t=1782914400000 strategy=tv_test_harness (bounded data limitation)
[INFO] app.engine: engine: catch-up replaying 2000 1m bars strategy=tv_test_harness from t=1782914400000
[INFO] app.engine: engine: catch-up exit strategy=tv_test_harness reason=tp1 at bar_t=1782987780000 price=61125.8000
[INFO] app.engine: engine: catch-up exit strategy=tv_test_harness reason=tp2 at bar_t=1782987780000 price=61125.8000
[INFO] app.engine: engine: entering live subscription strategy=tv_test_harness BTC-USDT 1h
[INFO] app.engine: engine: near-tick monitor started strategy=tv_test_harness
[INFO] app.engine: engine: 1m safety-net subscribed strategy=tv_test_harness
```

`docker compose logs signal-engine | grep -iE "error|traceback|exception"` — no
matches. `shadow_signals` row count for `tv_test_harness`: 245 (growing normally,
no write errors).

## Files changed

- `signal-engine/app/strategies/base.py` — `PositionState`, `Strategy` protocol update.
- `signal-engine/app/strategies/test_harness.py` — uses `self.position` instead of
  `self._position_side`.
- `signal-engine/app/engine.py` — removed `bh` dict; all paths use `strategy.position`;
  `_store_exit_leg` now takes the bracket explicitly.
- `signal-engine/app/exits.py` — added `BracketState.side` property.
- `signal-engine/tests/test_phantom_flip.py` — new regression test (Step 1).

No intrabar logic was added — this phase only unifies existing state.
