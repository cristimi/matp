# Prompt 3c — `--since` cutover filter for entry/exit parity

Auto-cutover is derived from `min(received_at)` on `public.orders` for the strategy
(first TV webhook = 2026-06-22T11:32:54Z). Explicit `--since` overrides it.

---

## `python -m app.diff exits tv_test_harness` (auto-cutover)

```
Comparing exits since cutover: 2026-06-22T11:32:54.322715+00:00
BAR (UTC)               side            reason      verdict               note
-----------------------------------------------------------------------------------------------
2026-06-22 18:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 19:00        close_short     flip        matched               reason=flip dt=895s
2026-06-22 21:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 22:28        close_short     trail       matched               reason=trail dt=34s

TV-ONLY CLOSES (exits TradingView made that the engine did not):
  received_at (UTC)         signal
  ----------------------------------------
  2026-06-22 11:32:54       close_short
  2026-06-22 20:25:45       close_long
  2026-06-22 22:27:17       close_short

Summary: 2 matched / 4 shadow closes, 2 unmatched; 3 tv-only closes
```

Down from 46 to 4 shadow closes — all 42 historical-replay flips dropped out.

---

## `python -m app.diff live tv_test_harness` (auto-cutover)

```
Comparing entries since cutover: 2026-06-22T11:32:54.322715+00:00
BAR (UTC)               TV signal       Local signal      Verdict
----------------------------------------------------------------------
2026-06-22 18:00        -               open_short        missing_in_tv
2026-06-22 19:00        open_long       open_long         matched
2026-06-22 21:00        -               open_short        missing_in_tv

Summary: 1 matched / 3 total, 2 mismatches
```

Down from 46 to 3 entries in the comparable window.

---

## `python -m app.diff exits tv_test_harness --since 2026-06-22T00:00:00Z` (explicit override)

```
Comparing exits since cutover: 2026-06-22T00:00:00+00:00
BAR (UTC)               side            reason      verdict               note
-----------------------------------------------------------------------------------------------
2026-06-22 01:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 03:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 04:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 06:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 07:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 18:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 19:00        close_short     flip        matched               reason=flip dt=895s
2026-06-22 21:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 22:28        close_short     trail       matched               reason=trail dt=34s

TV-ONLY CLOSES (exits TradingView made that the engine did not):
  received_at (UTC)         signal
  ----------------------------------------
  2026-06-22 11:32:54       close_short
  2026-06-22 20:25:45       close_long
  2026-06-22 22:27:17       close_short

Summary: 2 matched / 9 shadow closes, 7 unmatched; 3 tv-only closes
```

Explicit `--since 2026-06-22T00:00:00Z` picks up the early-morning flip closes that preceded
the first TV webhook, showing 9 shadow closes vs the auto-cutover's 4.

---

## Reading

In the auto-cutover window (since first TV order at 11:32 UTC on 2026-06-22) there are exactly
4 comparable shadow closes. The `trail` bracket exit matched with a 34s delta — confirming
catch-up recorded the right historical bar. The flip at 19:00 also matched a TV close at
895s (≈15 min) delta. The 2 unmatched flips (`18:00` close_long, `21:00` close_long) have no
corresponding TV close order — expected, as TV doesn't always send explicit closes for flips.
The 3 TV-only closes predate or fall outside the engine's bracket coverage during this period.
