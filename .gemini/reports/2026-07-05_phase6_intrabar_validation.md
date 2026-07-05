# Phase 6 — validate intrabar entries vs TradingView on real overnight crosses

Branch: `main`. Read-only analysis, no code/schema/engine changes. `STRATEGY=tv_test_harness`.

## Step 1 — Census + confirm intrabar mode

```
SELECT id, entry_trigger, local_signal_mode FROM public.strategies WHERE id='tv_test_harness';

       id        | entry_trigger | local_signal_mode
-----------------+---------------+-------------------
 tv_test_harness | intrabar      | shadow
(1 row)
```

Engine entries, most recent 20 (`shadow_signals`, `signal IN ('open_long','open_short')`):

```
   signal   |           fired_at            |    signal_bar_time
------------+-------------------------------+------------------------
 open_long  | 2026-07-05 06:19:56.732299+00 | 2026-07-05 06:00:00+00
 open_short | 2026-07-05 05:02:22.239815+00 | 2026-07-05 05:00:00+00
 open_long  | 2026-07-05 03:52:04.234397+00 | 2026-07-05 03:00:00+00
 open_short | 2026-07-05 02:07:46.130784+00 | 2026-07-05 02:00:00+00
 open_long  | 2026-07-01 13:00:00+00        | 2026-07-01 13:00:00+00
 ... (rest of the 20 are exactly on-the-hour — pre-intrabar / bar_close-era rows)
```

The 4 most recent `fired_at` values are all mid-hour (`:19:56`, `:02:22`, `:52:04`, `:07:46`)
— confirms the engine really fired intrabar overnight. Everything before `2026-07-01 13:00:00`
is exactly on-the-hour, i.e. pre-dates/is unrelated to the intrabar comparison.

TV entries, most recent 20 (`orders`, `signal IN ('open_long','open_short')`):

```
   signal   |          received_at
------------+-------------------------------
 open_long  | 2026-07-05 06:20:18.008338+00
 open_short | 2026-07-05 05:02:23.869544+00
 open_long  | 2026-07-05 03:52:06.577561+00
 open_short | 2026-07-05 02:07:50.016905+00
 open_long  | 2026-07-04 12:05:19.896267+00
 open_short | 2026-07-02 00:42:48.412643+00
 ... (older)
```

**4 crosses per side overnight** (both alternating long/short): `02:07`, `03:52`, `05:02`,
`06:19/06:20` on 2026-07-05.

## Step 2 — Clean both-flat cutover

Reconstructed full open/close history on both sides from 2026-06-27 onward (`shadow_signals`
and `orders`, all signal types). Both sides go flat before the overnight run:

- **Engine** last close before the overnight crosses: `close_long` at `2026-07-04 15:25:57.083`
  (with a duplicate at `15:25:57.043` and an earlier pair at `15:20:08` — these are catch-up/backfill
  artifacts tagged with a `signal_bar_time` of `2026-07-03 05:59`/`06:05`, i.e. from a restart of
  `signal-engine` at `2026-07-04T15:24:20Z` re-processing older bars; not part of this validation's
  scope, and no open signals were fired in that backfill). No further engine signal fires until
  `open_short` at `2026-07-05 02:07:46`.
- **TV** last close before the overnight crosses: `close_long` at `2026-07-04 12:06:24.493`
  (closing a short-lived `open_long`/`close_long` pair at `12:05:19`/`12:06:24`). No further TV
  order until `open_short` at `2026-07-05 02:07:50`.

Both flat from `max(15:25:57, 12:06:24)` = **2026-07-04 15:25:57**. Earliest clean 1h boundary
after that, before the first overnight cross (`02:07:46`):

**SINCE = 2026-07-04T16:00:00Z**

## Step 3 — Harness output (verbatim)

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

```
$ docker compose exec -T signal-engine python -m app.diff exits tv_test_harness 15 --since 2026-07-04T16:00:00
Comparing exits since cutover: 2026-07-04T16:00:00+00:00
BAR (UTC)               side            reason      verdict               note
-----------------------------------------------------------------------------------------------
2026-07-05 03:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m
2026-07-05 05:00        close_long      flip        matched               reason=flip dt=143s
2026-07-05 06:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m

TV-ONLY CLOSES (exits TradingView made that the engine did not):
  received_at (UTC)         signal
  ----------------------------------------
  2026-07-05 03:52:05       close_short
  2026-07-05 06:20:10       close_short

Summary: 1 matched / 3 shadow closes, 2 unmatched; 2 tv-only closes
```

## Step 4 — Classification of every non-clean verdict

**Entries (`live`): 4/4 matched, 0 mismatches.** Nothing to classify — every overnight cross
agrees on side and fires within seconds. This is the headline result.

**Exits (`exits`): 2 `missing_in_tv` + the 1 `matched` row's own delta are all a single root
cause — a diff-tool artifact, not a real divergence.**

Root cause, confirmed by reading `signal-engine/app/diff.py`:

- `cmd_live` (line 281) matches on `shadow.fired_at` — real intrabar emit time — vs
  `orders.received_at`. This is the fired_at-aware rewrite from Phase 4/5.
- `cmd_exits` (lines 384–388, 407) was **not** carried over to that rewrite: its SELECT only
  pulls `signal_bar_time` (not `fired_at`) from `shadow_signals`, and line 407 matches on
  `signal_bar_time` — the bar *boundary* — vs `orders.received_at`. For a bar-close strategy
  that's fine (close fires at the boundary), but for an intrabar close the real fire time is
  tens of minutes after the bar opens, so the bar-boundary-vs-TV-received delta is inflated by
  however far into the bar the cross happened.

Recomputing the same three rows using the *actual* `fired_at` from `shadow_signals` (not
fetched by `cmd_exits`, pulled separately) against the TV `received_at` already visible in the
"TV-ONLY CLOSES" list:

| bar (signal_bar_time) | engine fired_at (real) | TV received_at | real Δt | tool's verdict |
|---|---|---|---|---|
| 03:00 close_short | 2026-07-05 03:52:04.126 | 2026-07-05 03:52:05.546 | **1.42s** | `missing_in_tv` (tool compared 03:00 vs 03:52:05 → 3125s, blew the 15m window) |
| 05:00 close_long | 2026-07-05 05:02:22.047 | 2026-07-05 05:02:23.229 | **1.18s** | `matched, dt=143s` (tool compared 05:00 vs 05:02:23 → 143s, coincidentally under 15m) |
| 06:00 close_short | 2026-07-05 06:19:56.576 | 2026-07-05 06:20:10.393 | **13.82s** | `missing_in_tv` (tool compared 06:00 vs 06:20:10 → 1210s, blew the 15m window) |

All three exits actually match TV within **1.2–13.8 seconds** — as tight as the entries. The
"2 unmatched" and the misleading `dt=143s` on the one that happened to survive are entirely
explained by `cmd_exits` still keying off `signal_bar_time`; there is no `POSITION_STATE` or
`SIGNAL_LOGIC` issue and no genuine timing gap. (Not fixing this — out of scope for a
measurement-only pass — but flagging it as the next `app/diff.py` gap, same shape as the
Phase 4/5 fix already applied to `cmd_live`.)

For reference, the earlier `PROMPT_3C_exit_parity.md` report's `missing_in_tv` rows are a
*different* root cause (warmup-replay flip closes with no TV counterpart, pre-bracket-wiring
era) — unrelated to this intrabar-timestamp gap, which only shows up now that real intrabar
closes exist to compare.

**Matched-entry fire-time deltas**: 1.6s, 2.3s, 3.9s, 21.3s (median 3.1s, max 21.3s) — tight
tracking, not poll-cadence lag. The one 21.3s outlier (`06:19:56` engine vs `06:20:18` TV) is
still well inside normal webhook/network jitter, not a cadence problem.

## Headline

- **4/4 entries matched** (`live`), 0 `side_mismatch`, 0 `missing_in_tv`, 0 genuine `SIGNAL_LOGIC`
  divergences. Median fire-time delta **3.1s**, max **21.3s**.
- **3/3 exits actually match** TV within 1.2–13.8s once compared on real `fired_at`; the harness's
  `cmd_exits` verdicts (1 matched/2 missing) are wrong only because that command still compares
  against `signal_bar_time` instead of `fired_at` — a tooling gap, not an engine or TV divergence.
- **0 genuine `SIGNAL_LOGIC` mismatches** on either entries or exits.

## Verdict

**Yes** — the engine reproduces TradingView's intrabar entries (and exits) on every overnight
RSI-50 cross, with sub-25-second fire-time agreement throughout. The only bars that don't show
as a clean match are the two `exits` "missing_in_tv" rows (03:00, 06:00 bars), and those are a
false negative from `cmd_exits` comparing against `signal_bar_time` instead of `fired_at` — when
checked against the real fire time, both match TV within 14 seconds. No divergence to fix in the
engine or the intrabar port itself.
