# Phase 2 (intrabar) — intrabar entry evaluation for `entry_trigger='intrabar'`

Branch: `main`. Signal-engine is shadow-only (writes `shadow_signals`, never POSTs to
order-listener, never calls an exchange) — no live-trading impact. This phase adds the
first behavior-changing intrabar path: entries for `entry_trigger='intrabar'` strategies
are now evaluated against the forming 1h candle instead of only at bar close.
`entry_trigger='bar_close'` (every strategy today except the harness) is provably
unaffected — see Gate A.

## Step 0 — Forming-1h key check

```
$ docker compose exec -T redis redis-cli GET candle:forming:blofin:BTC-USDT:1h
{"t": 1783141200000, "o": 62658.4, "h": 62658.4, "l": 62590.0, "c": 62591.5, "v": 1.352}
```

Present and correctly shaped (`t` = the forming hour's own open-time). Phase 2 proceeded.

## Implementation

- `signal-engine/app/strategies/base.py`: `Strategy.evaluate()` gains a
  `detect_entries: bool = True` parameter. `detect_entries=False` updates indicator state
  (`last_rsi`) only — no cross detection, no `position` mutation, always returns `[]`.
- `signal-engine/app/strategies/test_harness.py`: `evaluate()` implements the new
  parameter — returns immediately after setting `last_rsi` when `detect_entries=False`.
- `signal-engine/app/engine.py`:
  - `_entry_loop` (closed-bar path) now calls
    `strategy.evaluate(candles, detect_entries=not is_intrabar)`. For `bar_close`
    strategies `is_intrabar` is `False`, so this call is `evaluate(candles)` — byte-identical
    to before. For `intrabar` strategies it becomes a last-rsi-only refresh; the closed-bar
    loop still appends the bar to `candles` and still runs the 1h RSI condition-modify exit.
  - New `_intrabar_entry_loop` (nested in `run_strategy_stream`, only started when
    `strategy.entry_trigger == 'intrabar'`): polls `read_forming_candle` for the live 1h
    timeframe every ~1s, builds a throwaway `candles + [forming]` snapshot (never mutates
    the shared `candles` list), calls `strategy.evaluate(snapshot)`, and processes any
    returned signals (store + bracket bookkeeping) exactly as the closed-bar loop does for
    its own entries.

## Gate A — prove `bar_close` is untouched

Scratch equivalence harness (not committed): fakes Redis/DB, drives the real
`app.engine.run_strategy_stream` against a fixed 1h candle fixture (wiggle warmup +
downtrend/uptrend/downtrend cycles), capturing every `store_shadow_signal()` call.
Ran once against pre-change code, once against post-change code (harness strategy left at
its class default `entry_trigger='bar_close'`), byte-diffed the output.

```
$ diff baseline.txt postchange.txt
$ echo "DIFF_EXIT=$?"
DIFF_EXIT=0
```

15 identical signal tuples both runs. `tests/test_phantom_flip.py` and `app.exits`
(copied into the running container to execute, since `tests/` isn't baked into the image):

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

`bar_close` strategies are provably unaffected by this phase.

## A bug found and fixed while proving Gate B (scope-limited)

While building a deterministic proof for the intrabar loop, I copied the closed-bar
`_entry_loop`'s signal-processing pattern verbatim:

```python
elif sig.signal in ("close_long", "close_short"):
    strategy.mark_flat()
```

`evaluate()` never emits a standalone close — a close signal is always the first half of a
same-call flip (`[close_X, open_Y]`), and `evaluate()` has already set `position.side` to
the new side by the time this loop runs (this exact reasoning is already documented at the
warmup loop, which correctly does *not* call `mark_flat()` for this reason). Calling
`mark_flat()` here clobbers `position.side` back to `None` right after `evaluate()` set it
correctly. In my first intrabar end-to-end test this caused a real duplicate: after a flip
fired correctly (`close_short` + `open_long`), the *next* poll (still the same direction,
RSI still > 50) saw `position.side == None` instead of `"long"`, satisfied the entry guard
again, and fired a second, spurious `open_long` overwriting the just-created bracket.

Fixed in `_intrabar_entry_loop` only — the close branch now clears `position.bracket` /
`position.entry_bar_time` (matching the warmup loop's pattern) instead of calling
`mark_flat()`:

```python
elif sig.signal in ("close_long", "close_short"):
    position.bracket = None
    position.entry_bar_time = None
```

**This same pattern exists, unfixed, in the live `_entry_loop` (the `bar_close` path)** —
it is pre-existing, not introduced by this phase, and I did **not** touch it, because
Gate A requires `bar_close` behavior to stay byte-identical and fixing it would change
that path's behavior on any live flip. Re-ran the Gate A equivalence diff, phantom-flip
test, and `app.exits` after this fix — all three still pass (shown above; the diff was
re-run against the fixed code, still empty).

**I have not opened a backlog entry for the pre-existing bug — see the question below.**

## Gate B — intrabar enabled, mechanism verified

```
$ docker compose exec -T postgres psql -U matp -d matp -c \
  "UPDATE public.strategies SET entry_trigger='intrabar' WHERE id='tv_test_harness';"
UPDATE 1
$ ./scripts/redeploy.sh signal-engine
✓ signal-engine redeployed.
```

Load log confirms `entry_trigger=intrabar` and the intrabar loop started:

```
[INFO] app.engine: engine: loaded strategy=tv_test_harness symbol=BTC-USDT tf=1h mode=shadow entry_trigger=intrabar
[INFO] app.engine: engine: warmup complete strategy=tv_test_harness bars=500 position=long bracket=active
[INFO] app.engine: engine: catch-up exit strategy=tv_test_harness reason=tp1 at bar_t=1783021800000 price=61629.8000
[INFO] app.engine: engine: catch-up exit strategy=tv_test_harness reason=tp2 at bar_t=1783021800000 price=61629.8000
[INFO] app.engine: engine: entering live subscription strategy=tv_test_harness BTC-USDT 1h
[INFO] app.engine: engine: near-tick monitor started strategy=tv_test_harness
[INFO] app.engine: engine: 1m safety-net subscribed strategy=tv_test_harness
[INFO] app.engine: engine: intrabar entry loop started strategy=tv_test_harness
```

Warmup and catch-up are identical in shape to the pre-Phase-2 baseline (Phase 0's report)
— unaffected, as expected (warmup only ever sees real closed bars).

`docker compose logs signal-engine | grep -iE "error|traceback|exception"` — empty over
~50 minutes of live observation (two redeploys, ~26 min then ~26 min more after the
bugfix redeploy). No exchange calls, no listener POSTs (shadow-only invariant intact).

### Natural live cross: not observed in the window

BTC's hourly RSI stayed in the high-40s to high-60s range for the whole observation
window (checked directly: RSI on the forming candle was ~58.7 shortly after the first
redeploy and ~similar afterward) — it never crossed 50, so no *natural* mid-hour entry
fired during this session. `shadow_signals` shows only the tp1/tp2 catch-up exits from
warmup replay after each redeploy; no new entries. This is expected market behavior, not
a defect — a 50-cross on hourly BTC isn't guaranteed to happen within an arbitrary
1-hour observation window. The harness is left running on `entry_trigger='intrabar'` so
the next real cross will be captured live, going forward.

### Deterministic proof (real code path, controlled inputs)

To prove the mechanism itself — since a natural cross didn't happen in-window — I built a
second scratch harness that drives the **real** `app.engine.run_strategy_stream` (same
function used in production, not a reimplementation) with:

- a fixed warmup fixture (wiggle + a firm 30-bar downtrend) that deterministically ends
  the strategy `short`, confirmed via a standalone probe (`position.side == 'short'`,
  `last_rsi ≈ 0.06`),
- `subscribe_closed_bars('1h')` yielding **nothing** for the rest of the test — so any
  entry/flip observed cannot have come from `_entry_loop`, only from
  `_intrabar_entry_loop`,
- a sequence of forming-candle polls with rising closes (probed in advance:
  RSI-including-forming crosses 50 between close=81 (RSI 48.7) and close=82 (RSI 50.9)) —
  bumps `[5, 8, 11, 12, 14, 16]`, one per ~1s poll.

Result:

```
[trace] last_close=75.0 last_rsi=30.15 side_before=short side_after=short sigs=[]
[trace] last_close=78.0 last_rsi=40.84 side_before=short side_after=short sigs=[]
[trace] last_close=81.0 last_rsi=48.69 side_before=short side_after=short sigs=[]
[trace] last_close=82.0 last_rsi=50.86 side_before=short side_after=long  sigs=['close_short', 'open_long']
[trace] last_close=84.0 last_rsi=54.70 side_before=long  side_after=long  sigs=[]
[trace] last_close=86.0 last_rsi=57.98 side_before=long  side_after=long  sigs=[]
[trace] last_close=86.0 last_rsi=57.98 side_before=long  side_after=long  sigs=[]

signals captured during live (forming-bar) phase = 2
  {'signal': 'close_short', 'signal_bar_time': 396000000, 'bar_close_price': 82.0}
  {'signal': 'open_long',   'signal_bar_time': 396000000, 'bar_close_price': 82.0}

PASS: exactly one flip fired while the underlying 1h bar never closed, at the
forming bar's own open-time (mid-hour, not a closed-bar timestamp). Later polls in
the same direction (bump=14, 16) did not re-fire.
position.side after test: long
position.bracket active: True   (bracket built from bar_close_price=82.0, the forming
                                  candle's close, per constraint 4)
```

This proves, against the real engine code: (a) the cross fires exactly once, mid-hour,
using the forming bar's open-time as `signal_bar_time` (idempotency constraint 3); (b) the
one-entry-per-cross guard holds across repeated polls in the same direction (no spam,
constraint 5 / "no entry spam"); (c) the bracket is built from the forming candle's close
(constraint 4); (d) it was driven entirely by `_intrabar_entry_loop` — `_entry_loop` never
saw a closed bar in this test.

## State left running

`tv_test_harness` is left on `entry_trigger='intrabar'` (shadow-only) — this is the data
we want for the next parity run, per the prompt.

## Files changed

- `signal-engine/app/strategies/base.py` — `evaluate(..., detect_entries=True)` on the
  `Strategy` protocol.
- `signal-engine/app/strategies/test_harness.py` — implements `detect_entries`.
- `signal-engine/app/engine.py` — gates the closed-bar loop's `evaluate()` call for
  intrabar strategies; adds `_intrabar_entry_loop`; starts it only when
  `entry_trigger == 'intrabar'`; module docstring updated.

No exit changes. No exchange calls. Shadow only.
