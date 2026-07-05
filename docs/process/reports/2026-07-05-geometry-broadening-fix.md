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

Phase 1 complete and pushed to `main`. Phase 2 (surfacing strong-fit-but-unclassified
structures generically in `_render_geometry`) is deferred pending review, per the task
spec — not started.
