# Fix: geometry classifier drops broadening/diverging formations (Phase 1)

Scope: `ai-signal-generator` only. Pure logic + tests. No DB migration, no `builder.py`
change, no exchange/executor changes.

## Root cause (recap)

`detect_geometry()`'s shape-classification `if/elif` chain in
`ai-signal-generator/app/data/geometry.py` had no branch for a **broadening / megaphone**
formation (upper boundary rising, lower boundary falling). It fell through to
`else: shape = 'no_pattern'`, and `builder._render_geometry()` treats `no_pattern`
identically to "no data", so the entire `GEOMETRIC PATTERN` section was dropped even
though the trendline fit was strong (R² 0.91–0.94). See
`docs/process/reports/2026-07-05-hype-geometry-investigation.md` for the original
investigation.

## Design decision

Added a new shape, `broadening`, defined as **strictly opposite-sign slopes**: upper
boundary rising (`_is_positive(upper_pct)`) AND lower boundary falling
(`_is_negative(lower_pct)`). This is the classic widening-megaphone shape and matches
the reproduced HYPE anchor case exactly (upper +0.166%/bar, lower -0.085%/bar).

Rejected alternative: classifying *any* sufficiently-diverging pair (including
same-direction-but-widening, e.g. both boundaries rising but at different rates) as
`broadening`. That case doesn't form a megaphone — it's a non-parallel channel drifting
in one direction — so it does not carry the same "expanding volatility / breakout risk"
signal a real megaphone does. Reclassifying it risked adding label noise for a case that
isn't actually a recognized chart pattern.

**Reconciling `test_no_pattern_diverging`**: this existing test builds a both-rising,
diverging series (`upper 110+0.3i`, `lower 90+0.1i` — same sign). Under the definition
above it is same-sign, not opposite-sign, so it correctly stays `no_pattern`. The test's
assertion was left unchanged; only its comment was updated to explain *why* it is not
reclassified, so the distinction is documented rather than implicit.

The new branch sits after the wedge branches (which require same-sign + converging) and
before the final `else`, so it cannot shadow any existing shape — triangles require one
side flat, wedges require same-sign, so opposite-sign only ever reaches the new branch
or the final fallback.

## Diff summary

- `ai-signal-generator/app/data/geometry.py`:
  - Module docstring: documented the full shape list and the `broadening` definition.
  - Classification chain: added
    `elif _is_positive(upper_pct) and _is_negative(lower_pct): shape = 'broadening'`
    with an inline comment explaining the design decision (opposite-sign only).
- `ai-signal-generator/tests/test_geometry.py`:
  - `test_no_pattern_diverging`: comment updated to explain why this same-sign case is
    deliberately *not* reclassified as `broadening`. Assertion unchanged.
  - Added `test_broadening`: upper rising / lower falling zigzag series, asserts
    `shape == 'broadening'`, `fit_quality == 'strong'`, `convergence_pct_per_bar < 0`.

No `builder.py` change was needed: `_render_geometry()` only special-cases
`shape == 'no_pattern'` (line ~246). Once `detect_geometry` returns any other shape
string, the function renders boundaries/touches/position/fit-quality unconditionally,
and already has a `conv < 0` branch that prints `"Divergence Rate: ...% of price per
bar (boundaries widening)"` (line ~266-267) — exactly what a `broadening` result needs.
Confirmed by reading the function; no edit was required or made.

## Verification

Tests were run inside a disposable `python:3.11-slim` container (pytest/numpy are not
present on the host or in the deployed image), mounting the repo read-write:

```
$ docker run --rm -v .../ai-signal-generator:/app -w /app python:3.11-slim \
    bash -c "pip install -q pytest numpy && python -m pytest tests/test_geometry.py -q"
...............                                                          [100%]
15 passed in 3.06s
```

All 15 tests pass (13 pre-existing + `test_broadening` + the reconciled
`test_no_pattern_diverging`, which was already counted before and is unchanged in
assertion).

Reproduction of the anchor case (upper rising / lower falling, strong fit):

```python
candles = _zigzag_candles(80, lambda i: 110 + 0.15 * i, lambda i: 90 - 0.08 * i)
result = detect_geometry(candles)
print(result)
```

Output:

```
{'shape': 'broadening', 'upper_boundary': 121.85, 'lower_boundary': 83.68,
 'upper_touches': 5, 'lower_touches': 5, 'convergence_pct_per_bar': -0.2073,
 'pattern_age_bars': 58, 'position_in_range_pct': 71.43, 'fit_quality': 'strong'}
OK: shape == 'broadening' and fit_quality == 'strong'
```

Confirms `shape == 'broadening'` and `fit_quality == 'strong'`, with
`convergence_pct_per_bar` negative (diverging) — consistent with the real HYPE case's
`conv_rate ≈ -0.25`.

No pre-existing unrelated test failures were observed; all 15 tests in this file passed
both before conceptually (13 prior) and after (15) this change.

## Status

Phase 1 complete and pushed to `main`.

---

# Phase 2 — stop silently dropping strong fits

## Design

`_render_geometry()` previously bailed out on `shape == 'no_pattern'` unconditionally,
so *any* future unhandled-but-strong-fit case (not just the broadening gap fixed in
Phase 1) would be silently dropped from the prompt. Changed the gate: a `no_pattern`
result is only omitted when `fit_quality != 'strong'` (i.e. genuinely noisy — too few
swings, or a weak trendline fit). A `no_pattern` result with a **strong** fit is now
rendered with all the same fields (boundaries, touches, position, divergence/
convergence rate), but the "Detected Shape" line reads `Unclassified Structure (no
named pattern, but a strong trendline fit)` instead of a title-cased shape name, so the
LLM can't mistake it for a recognized chart pattern.

No change to `geometry.py` — this is purely a `builder.py` rendering-gate change.

## Diff summary

- `ai-signal-generator/app/prompt/builder.py` (`_render_geometry`):
  - Gate changed from `not gd or shape == 'no_pattern'` to `not gd`, with a
    second check `if unclassified and fit_quality != 'strong': return ''` placed
    after fetching `shape`/`fit_quality`.
  - Added `label` computation: unclassified+strong → the "Unclassified Structure"
    string; otherwise the existing `shape.replace('_', ' ').title()` behavior,
    unchanged for all named shapes (including `broadening` from Phase 1).
- `ai-signal-generator/tests/test_builder_geometry.py` (new file): 5 tests exercising
  `_render_geometry` directly — no_pattern+weak omitted, no_pattern+strong surfaced as
  Unclassified, a named shape (`broadening`) still renders its title, `use_geometry`
  off omitted, empty `geometry_data` omitted.

## Verification

Full test run (geometry + new builder tests) in a disposable `python:3.11-slim`
container with `pytest numpy asyncpg pydantic pydantic-settings` installed (builder.py
imports `app.prompt.templates`, which imports `asyncpg`):

```
$ python -m pytest tests/test_geometry.py tests/test_builder_geometry.py -q
....................                                                     [100%]
20 passed in 2.33s
```

(`tests/test_ohlcv.py` was not run in this environment — it imports `ccxt`, which
wasn't installed for this pure-logic verification; this is an environment gap, not a
test failure, and is unrelated to this change.)

Before/after of the rendered `GEOMETRIC PATTERN` section for a strong `no_pattern`
fixture (`upper_boundary=121.85, lower_boundary=83.68, fit_quality='strong',
convergence_pct_per_bar=-0.2073`, i.e. the Phase 1 anchor case reinterpreted as
unclassified):

**Before** (ran against the pre-Phase-2 `builder.py`):
```
''
```
(empty string — the whole section was dropped)

**After**:
```
GEOMETRIC PATTERN:
Detected Shape:       Unclassified Structure (no named pattern, but a strong trendline fit)
Fit Quality:          strong
Upper Boundary:       121.85
Lower Boundary:       83.68
Upper Touches:        5
Lower Touches:        5
Position in Range:    71.43%  (0=at lower boundary, 100=at upper)
Pattern Age:          58 bars
Divergence Rate:      -0.2073% of price per bar (boundaries widening)
```

The weak-fit `no_pattern` case (same fixture with `fit_quality='weak'`) still renders
`''` before and after — confirmed by the same script.

## Status

Both phases complete, verified, and pushed to `main`.
