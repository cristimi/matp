# ETH read "horizontal channel" while the chart drew two ascending, diverging lines

Date: 2026-07-26

## The report

The AI range chip on the ETH chart said `horizontal channel · strong`, but the
chart underneath showed two lines that both climbed, with the upper one climbing
faster.

## Diagnosis — the chart was right, the label was wrong

The stored geometry for signal 4757 (ETH-USDT, 1h, 16:00):

```
$ docker compose exec -T postgres psql -U matp -d matp -c "..."
  id  |         triggered_at          |       shape        | age |     us     |     ls     |  conv
------+-------------------------------+--------------------+-----+------------+------------+---------
 4757 | 2026-07-26 16:00:46.711516+00 | horizontal_channel | 42  | 0.95339202 | 0.72031746 | -0.0122
 4748 | 2026-07-26 15:00:28.184598+00 | no_pattern         | 41  | 0.95339202 | 0.72031746 | -0.0123
 4734 | 2026-07-26 14:00:14.494684+00 | no_pattern         | 40  | 0.95339202 | 0.72031746 | -0.0124
```

Both slopes are positive, so the chart drew ascending lines — correctly, from the
data. Three separate findings:

**1. `fit_quality` never spoke to slope.** It is R² only — how tightly the swing
points sit on each fitted line. A perfectly straight steep line scores "strong".
So "strong" and "ascending" were never in conflict; only "horizontal" was wrong.

**2. Flat/parallel were judged per bar, which hid the total.** A boundary counted
as flat below `FLAT_THR_PCT = 0.05`% of price *per bar*. Across the 42-bar pattern
that permits a 2.1% climb, and the parallel threshold let the channel more than
double in width:

```
upper drift over 42 bars = 2.120%
lower drift over 42 bars = 1.601%
channel width 7.03 -> 16.82  (2.39x wider)
```

**3. The label was unstable, because slopes were divided by the live close.**
Identical slopes, identical swings — only ETH's close moved:

```
old: divide by live close 15:00  per-bar upper=0.05024 lower=0.03796  flat(thr 0.05)=False,True
old: divide by live close 16:00  per-bar upper=0.04982 lower=0.03764  flat(thr 0.05)=True,True
```

`upper_pct` crosses 0.05 at exactly price 1906.784. ETH closed 1897.73 at 15:00
and 1913.76 at 16:00, so the shape flipped from `no_pattern` to
`horizontal_channel` on price drift alone.

## The fix (user chose "fix both")

`ai-signal-generator/app/data/geometry.py`:

- Classification now works on **end-to-end drift over the whole pattern**
  (`slope × pattern_age_bars`) instead of per-bar slope.
  `FLAT_THR_PCT 0.05` → `FLAT_DRIFT_PCT 1.0`,
  `PARALLEL_THR_PCT 0.04` → `PARALLEL_DRIFT_PCT 2.0`,
  `CONV_THR_PCT 0.01` → `CONV_DRIFT_PCT 0.5`.
- Slopes are normalised against **the fitted channel's own midline** at the last
  bar, not the last close, so an unchanged fit keeps its label as price moves.
- `pattern_age_bars` moved above the classifier (it is now an input to it); the
  duplicate computation lower down was removed.
- New output fields `upper_drift_pct` / `lower_drift_pct` record the exact numbers
  the shape was decided on.

`convergence_pct_per_bar` keeps its name and its per-bar meaning — the payload
contract is unchanged apart from the two added fields.

## Proof

### The ETH case under the new rules

```
ref midline price     = 1889.167
upper drift over 42b  = 2.120%   flat? False
lower drift over 42b  = 1.601%   flat? False
divergence            = -0.518%  parallel? True
channel width 7.03 -> 16.82  (2.39x wider)
=> both boundaries positive and parallel -> ascending_channel
```

### The live ETH candles, through the deployed detector

```
$ docker run --rm --network none -v .../ai-signal-generator:/src:ro \
    matp-ai-signal-generator python -c "... detect_geometry(eth_1h_candles) ..."
shape                      ascending_channel
fit_quality                strong
upper_boundary             1928.760633
lower_boundary             1889.399138
upper_slope                2.06934974
lower_slope                1.08219149
upper_drift_pct            3.7938
lower_drift_pct            1.984
pattern_age_bars           35
convergence_pct_per_bar    -0.0517
```

The label now matches the drawn lines: both boundaries rising.

### Tests — 28 existing plus 2 new regressions, all pass

The image ships numpy but not pytest, so the suite was run through a minimal
runner that stubs `pytest` and calls each `test_*` function.

```
$ docker run --rm --network none -v .../ai-signal-generator:/src:ro \
    -v .../scratchpad:/runner:ro matp-ai-signal-generator \
    python /runner/run_geometry_tests.py

  PASS  test_geometry::test_horizontal_channel
  PASS  test_geometry::test_ascending_channel
  PASS  test_geometry::test_descending_channel
  PASS  test_geometry::test_ascending_triangle
  PASS  test_geometry::test_descending_triangle
  PASS  test_geometry::test_rising_wedge
  PASS  test_geometry::test_falling_wedge
  PASS  test_geometry::test_no_pattern_diverging
  PASS  test_geometry::test_broadening
  PASS  test_geometry::test_slow_climb_is_not_horizontal            <- new
  PASS  test_geometry::test_shape_does_not_depend_on_the_last_close <- new
  ... (30 total, incl. test_builder_geometry)

ALL TESTS PASSED
```

The two new tests pin exactly the two causes: a slow climb must not read as
horizontal, and nudging only the final close by ±16 must not change the shape.

### Deploy

```
$ ./scripts/redeploy.sh ai-signal-generator
matp-ai-signal-generator-1   matp-ai-signal-generator   Up (health: starting)
✓ ai-signal-generator redeployed.
```

## Note

Rows already in `ai_signal_log` keep the label they were written with — the chip
on an old chart will still read `horizontal channel`. Only cycles from this deploy
onward carry the corrected classification.
