# Bring `cmd_exits` onto `fired_at` (match the Phase 4/5 fix already in `cmd_live`)

Branch: `main`. Shadow-only, no live-trading impact. Change scoped to `cmd_exits` in
`signal-engine/app/diff.py` only.

## Root cause (from the Phase 6 validation)

`cmd_live` (Phase 4/5) matches shadow entries against TV orders using `shadow.fired_at` — the
real intrabar emit time. `cmd_exits` never got the same treatment: it selected only
`signal_bar_time` (the bar's `:00` boundary) and matched on that instead of the real fire time.
For a bar-close strategy that's harmless, but for an intrabar close — fired tens of minutes into
the bar — comparing the *boundary* against TV's `received_at` inflates the delta by however far
into the bar the cross happened, producing false `missing_in_tv` verdicts (see
`2026-07-05_phase6_intrabar_validation.md` for the full before-state: 2 of 3 overnight exits
showed `missing_in_tv`, and the one that "matched" reported a misleading `dt=143s` when the real
delta was 1.18s).

## Change

`signal-engine/app/diff.py`, `cmd_exits` only:

- Shadow SELECT now also fetches `fired_at`; `ORDER BY` changed from `signal_bar_time` to
  `fired_at` (kept `signal_bar_time` in the SELECT — still used for the printed `BAR (UTC)`
  column, unchanged).
- Match/delta calculation (`s_ms`) now uses `sr["fired_at"]`, coalescing to `sr["signal_bar_time"]`
  only if `fired_at` is `NULL` (defensive; migration 042 backfilled the column `NOT NULL`, so this
  path shouldn't trigger — confirmed below).
- `bar_ms` (the value stored in `rows_out` for table display) still comes from
  `signal_bar_time`, unchanged — the printed "BAR (UTC)" column continues to show the bar
  boundary, not the fire time.
- No change to exit-reason logic, `side_mismatch`/opposite-side detection, the TV-only-closes
  section, or the DB write-back (`match_status`/`matched_order_id`/`diff_notes` still written the
  same way, just with a now-accurate `dt`).

`cmd_live` and `cmd_replay` untouched. No schema or engine changes.

## Verify

Defensive fallback is dead code — no NULL `fired_at` rows exist on close signals:

```
$ docker compose exec -T postgres psql -U matp -d matp -c "
SELECT count(*) FROM public.shadow_signals WHERE signal IN ('close_long','close_short') AND fired_at IS NULL;"
 count
-------
     0
(1 row)
```

Redeployed `signal-engine` (`./scripts/redeploy.sh signal-engine`) and re-ran the same Phase 6
window (`SINCE=2026-07-04T16:00:00Z`):

### `exits` — AFTER

```
$ docker compose exec -T signal-engine python -m app.diff exits tv_test_harness 15 --since 2026-07-04T16:00:00
Comparing exits since cutover: 2026-07-04T16:00:00+00:00
BAR (UTC)               side            reason      verdict               note
-----------------------------------------------------------------------------------------------
2026-07-05 03:00        close_short     flip        matched               reason=flip dt=1s
2026-07-05 05:00        close_long      flip        matched               reason=flip dt=1s
2026-07-05 06:00        close_short     flip        matched               reason=flip dt=13s

Summary: 3 matched / 3 shadow closes, 0 unmatched; 0 tv-only closes
```

**Before** (from `2026-07-05_phase6_intrabar_validation.md`, same `SINCE`, unchanged code):

```
BAR (UTC)               side            reason      verdict               note
-----------------------------------------------------------------------------------------------
2026-07-05 03:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m
2026-07-05 05:00        close_long      flip        matched               reason=flip dt=143s
2026-07-05 06:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m

Summary: 1 matched / 3 shadow closes, 2 unmatched; 2 tv-only closes
```

All three bars flip from false `missing_in_tv`/misleading `dt=143s` to `matched` with the real
sub-15-second deltas the Phase 6 report had already computed by hand (1.42s, 1.18s, 13.82s —
rounding down to whole seconds in the tool's `//1000` output gives 1s/1s/13s).

### `live` — sanity check (should be byte-for-byte unchanged)

```
$ docker compose exec -T signal-engine python -m app.diff live tv_test_harness --since 2026-07-04T16:00:00 --window 10
Comparing entries since cutover: 2026-07-04T16:00:00+00:00 (window=10m)
FIRED (UTC)                 signal          verdict           note
------------------------------------------------------------------------------------------
2026-07-05 02:07:46         open_short      matched           dt=3.9s
2026-07-05 03:52:04         open_long       matched           dt=2.3s
2026-07-05 05:02:22         open_short      matched           dt=1.6s
2026-07-05 06:19:56         open_long       matched           dt=21.3s

Summary: 4 matched / 4 shadow entries, 0 unmatched; 0 tv-only entries
Fire-time delta (matched only, n=4): median=3.1s max=21.3s
```

Identical to the Phase 6 report's `live` output — confirms the edit was scoped to `cmd_exits`
only; entries are untouched.

## Result

- 3/3 exits now correctly report `matched` with deltas of 1s/1s/13s (was 1/3 matched with a
  misleading 143s delta, 2/3 falsely `missing_in_tv`).
- `cmd_live` unaffected.
- No NULL-`fired_at` crash risk — verified 0 such rows exist.
