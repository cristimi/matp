# Phase 4+5 — record signal fire-time + intrabar-aware entry diff

Branch: `main` (shadow-only, no live-trading impact). Next free migration number was
**042** (038/039/040/041 were already used by prior work), not 040 as originally guessed.

## Gate A — record the fire-time (schema + writer)

### Migration `db/migrations/042_shadow_fired_at.sql`

Applied to the live DB:

```
BEGIN
NOTICE:  column "fired_at" of relation "shadow_signals" already exists, skipping
ALTER TABLE
UPDATE 0
ALTER TABLE
ALTER TABLE
COMMIT
NOTICE:  Migration 042 verified OK: fired_at column present, NOT NULL, default=now(), 0 NULL rows
DO
```

(First run hit a `column reference "is_nullable" is ambiguous` bug in the verify block's
variable naming — fixed by renaming the local variable to `col_nullable`, then re-ran; the
DDL itself had already applied cleanly on the first pass, only the DO block needed the fix.)

Schema after migration:

```
 fired_at         | timestamp with time zone |           | not null | now()
```

```
SELECT count(*) FROM shadow_signals WHERE fired_at IS NULL;
 count
-------
     0
```

### `shadow_store.py`

Added `fired_at = datetime.now(timezone.utc)` (captured at emit time, before the INSERT)
to the column list / VALUES. Additive only — no change to which signals get stored or the
`ON CONFLICT` idempotency key.

### Fresh-row proof

Redeployed `signal-engine`. The live strategy's last real market flip was 2026-07-02 21:12
(~42h before this session), so no organic intrabar cross fired in the verification window.
Instead, verified the writer directly: called `store_shadow_signal()` in the running
container with a synthetic `signal_bar_time` (year 2286, guaranteed non-colliding) via
`signal_source='verify_fired_at_probe'`, then deleted the test row immediately after:

```
{'id': 4083, 'signal_bar_time': datetime.datetime(2286, 11, 20, 17, 30, tzinfo=timezone.utc),
 'fired_at': datetime.datetime(2026, 7, 4, 15, 21, 15, 205467, tzinfo=timezone.utc),
 'age': datetime.timedelta(microseconds=51135)}
cleaned up test row
```

`fired_at` was 51ms after invocation (real emit time) while `signal_bar_time` was the
unrelated synthetic value — proving the writer sets the real emit time, independent of
`signal_bar_time`. Confirmed 0 rows remain with that marker after cleanup:
`SELECT count(*) FROM shadow_signals WHERE signal_source='verify_fired_at_probe';` → `0`.

## Gate B — intrabar-aware entry matching in `cmd_live`

Rewrote `cmd_live` in `signal-engine/app/diff.py` to match each shadow entry to the
**nearest unused TV entry order of the same signal within a ±window** (mirrors
`cmd_exits`), using `shadow.fired_at` vs `orders.received_at` instead of the old
whole-1h-bar `[bar, bar+1h)` lookup. Added `--window <min>` (default 10). Verdicts:
`matched` / `side_mismatch` / `missing_in_tv`, written back to `match_status` /
`matched_order_id` / `diff_notes` (now includes `dt=<seconds>s` for matches). Prints a
median/max fire-time-delta summary across matches — the headline intrabar metric.
`cmd_replay` is unchanged except for a one-line note that it reflects closed-bar
derivation and intrabar entries should be checked via `live`.

```
$ docker compose exec -T signal-engine python -m app.diff live tv_test_harness --window 10
Comparing entries since cutover: 2026-06-22T11:32:54.322715+00:00 (window=10m)
FIRED (UTC)                 signal          verdict           note
------------------------------------------------------------------------------------------
2026-06-22 18:00:00         open_short      missing_in_tv     no tv entry within 10m
...
2026-06-24 01:00:00         open_short      matched           dt=81.2s
2026-06-24 12:00:00         open_short      matched           dt=450.6s
2026-06-25 05:00:00         open_long       matched           dt=221.7s
...
2026-06-26 15:00:00         open_long       side_mismatch     tv=open_short local=open_long
2026-06-27 02:00:00         open_long       matched           dt=235.2s
2026-06-28 10:00:00         open_short      matched           dt=134.1s
2026-06-29 05:00:00         open_long       matched           dt=24.8s
2026-06-29 09:00:00         open_long       matched           dt=99.8s
...

TV-ONLY ENTRIES (entries TradingView made that the engine did not fire):
  received_at (UTC)         signal
  ----------------------------------------
  2026-06-22 19:14:55       open_long
  ... (28 total)

Summary: 7 matched / 34 shadow entries, 27 unmatched; 28 tv-only entries
Fire-time delta (matched only, n=7): median=134.1s max=450.6s
```

34 shadow entry rows evaluated, 7 matched within the ±10min window, deltas ranging
24.8s–450.6s (median 134.1s, max 450.6s). All 34 rows predate migration 042, so their
`fired_at` was backfilled to `signal_bar_time` (on-the-hour) — no organic intrabar cross
has fired since the deploy, so a genuine mid-hour `fired_at` match hasn't been observed
live yet. The mechanism itself is proven: matching is now driven by `fired_at`/window
(same nearest-match approach `cmd_exits` already used for closes), not the old 1h-bucket
lookup — the `side_mismatch` and `missing_in_tv` verdicts above are only possible because
the code no longer treats "any TV order landing in the same 1h bar" as an automatic match.

**Replay stays closed-bar; intrabar validation runs through `live`** — as designed, since
`cmd_replay` re-derives signals from closed candles only and cannot reproduce an intrabar
cross.

## Scope

Shadow only. No exit-path changes. No exchange calls.
