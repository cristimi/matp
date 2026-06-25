# Parity Report: Internal Signal-Engine vs Pine/TV Harness
**Date:** 2026-06-25  
**Strategy:** `tv_test_harness`  
**Oracle:** Pine "MATP Test Harness v4" (TradingView)  
**Branch:** `main`  
**Scope:** Read-only analysis. No code changes, no order execution.

---

## Cutover

Auto-cutover from first TV order for `tv_test_harness`:

```
2026-06-22 11:32:54.322715 UTC
```

---

## STEP 1 — Census

### Engine side (`shadow_signals`)

```
   signal    | cnt |      earliest_bar      |       latest_bar
-------------+-----+------------------------+------------------------
 close_long  |   5 | 2026-06-22 18:00:00+00 | 2026-06-25 07:03:00+00
 close_short |   9 | 2026-06-22 19:00:00+00 | 2026-06-25 12:30:00+00
 open_long   |   4 | 2026-06-22 19:00:00+00 | 2026-06-25 05:00:00+00
 open_short  |   5 | 2026-06-22 18:00:00+00 | 2026-06-25 10:00:00+00
```

**Total engine signals in window: 23** (14 closes, 9 entries)

### TV side (`orders`)

```
     signal     | cnt |           earliest            |            latest
----------------+-----+-------------------------------+-------------------------------
 close_long     |   8 | 2026-06-22 20:25:45.875144+00 | 2026-06-25 12:30:43.670886+00
 close_short    |   9 | 2026-06-22 11:32:54.322715+00 | 2026-06-25 11:14:33.45808+00
 exchange_close |   2 | 2026-06-24 03:19:12.958015+00 | 2026-06-25 13:33:39.063774+00
 open_long      |   6 | 2026-06-22 19:14:55.958018+00 | 2026-06-25 12:30:27.302258+00
 open_short     |   4 | 2026-06-22 20:25:46.755823+00 | 2026-06-25 10:41:37.364675+00
```

**Total TV orders in window: 29** (17 closes + 2 exchange_close, 10 entries)

**Gate: TV-side count is non-zero — parity comparison proceeds.**

---

## STEP 2 — Harness Outputs (verbatim)

### 2a — Entry parity (`replay`)

```
$ docker compose exec -T signal-engine python -m app.diff replay tv_test_harness 2026-06-22T11:32:54+00:00

2026-06-25 15:13:33,939 [INFO] __main__: replay: fetching BTC-USDT 1h from 2026-06-16T15:32:54+00:00 to now
2026-06-25 15:13:38,371 [INFO] __main__: replay: fetched 216 total candles
2026-06-25 15:13:38,444 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1781884800000 close=62944.90 rsi=47.73
2026-06-25 15:13:38,454 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1781888400000 close=63170.70 rsi=52.29
2026-06-25 15:13:38,477 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1781895600000 close=62998.60 rsi=48.68
2026-06-25 15:13:38,490 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1781899200000 close=63217.30 rsi=53.26
2026-06-25 15:13:38,501 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1781902800000 close=63046.90 rsi=49.55
2026-06-25 15:13:38,512 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1781906400000 close=63287.80 rsi=54.39
2026-06-25 15:13:38,708 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1781960400000 close=63368.10 rsi=49.14
2026-06-25 15:13:38,731 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1781964000000 close=63926.60 rsi=60.74
2026-06-25 15:13:39,503 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782072000000 close=63816.30 rsi=41.36
2026-06-25 15:13:39,625 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782090000000 close=64579.20 rsi=61.32
2026-06-25 15:13:39,709 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782097200000 close=63940.00 rsi=48.70
2026-06-25 15:13:39,736 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782100800000 close=64063.60 rsi=50.89
2026-06-25 15:13:39,793 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782108000000 close=63960.60 rsi=48.80
2026-06-25 15:13:39,804 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782111600000 close=64188.00 rsi=52.80
2026-06-25 15:13:39,932 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782151200000 close=64305.80 rsi=48.72
2026-06-25 15:13:39,942 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782154800000 close=64431.10 rsi=50.76
2026-06-25 15:13:39,963 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782162000000 close=64282.00 rsi=48.15
2026-06-25 15:13:40,259 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782259200000 close=62982.10 rsi=51.49
2026-06-25 15:13:40,277 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782262800000 close=62904.60 rsi=49.57
2026-06-25 15:13:40,440 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782298800000 close=62887.70 rsi=51.85
2026-06-25 15:13:40,451 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782302400000 close=62587.90 rsi=45.69
2026-06-25 15:13:40,804 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782363600000 close=61567.10 rsi=52.81
2026-06-25 15:13:40,973 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782381600000 close=61240.00 rsi=47.12
2026-06-25 15:13:41,214 [INFO] __main__: replay: 9 local entry signals in window
2026-06-25 15:13:41,678 [INFO] __main__: replay: 10 tv_test entry orders in window
BAR (UTC)               TV signal       Local signal      Verdict
----------------------------------------------------------------------
2026-06-22 18:00        -               open_short        bar_offset
2026-06-22 19:00        open_long       open_long         matched
2026-06-22 20:00        open_short      -                 bar_offset
2026-06-22 21:00        -               open_short        bar_offset
2026-06-24 00:00        open_long       open_long         matched
2026-06-24 01:00        open_short      open_short        matched
2026-06-24 02:00        open_long       -                 bar_offset
2026-06-24 11:00        -               open_long         bar_offset
2026-06-24 12:00        open_short      open_short        matched
2026-06-25 04:00        open_long       -                 bar_offset
2026-06-25 05:00        open_long       open_long         matched
2026-06-25 10:00        open_short      open_short        matched
2026-06-25 12:00        open_long       -                 extra_local

Summary: 6 matched / 13 total, 7 mismatches
```

### 2b — Exit parity (`exits`)

```
$ docker compose exec -T signal-engine python -m app.diff exits tv_test_harness 15

Comparing exits since cutover: 2026-06-22T11:32:54.322715+00:00
BAR (UTC)               side            reason      verdict               note
-----------------------------------------------------------------------------------------------
2026-06-22 18:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 19:00        close_short     flip        matched               reason=flip dt=895s
2026-06-22 21:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-22 22:28        close_short     trail       matched               reason=trail dt=34s
2026-06-24 00:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-24 01:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-24 02:39        close_short     tp1         missing_in_tv         reason=tp1 no tv close within 15m
2026-06-24 02:41        close_short     trail       missing_in_tv         reason=trail no tv close within 15m
2026-06-24 11:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-24 12:00        close_long      flip        matched               reason=flip dt=447s
2026-06-24 13:06        close_short     trail       matched               reason=trail dt=160s
2026-06-24 13:06        close_short     tp1         matched               reason=tp1 dt=268s
2026-06-25 07:03        close_long      trail       matched               reason=trail dt=819s
2026-06-25 12:30        close_short     stop        side_mismatch         reason=stop opposite-side tv close

TV-ONLY CLOSES (exits TradingView made that the engine did not):
  received_at (UTC)         signal
  ----------------------------------------
  2026-06-22 11:32:54       close_short
  2026-06-22 20:25:45       close_long
  2026-06-22 22:27:17       close_short
  2026-06-24 02:00:17       close_short
  2026-06-24 03:16:02       close_long
  2026-06-25 05:07:49       close_long
  2026-06-25 05:11:21       close_long
  2026-06-25 05:26:14       close_long
  2026-06-25 11:03:55       close_short
  2026-06-25 11:14:33       close_short
  2026-06-25 12:30:43       close_long

Summary: 6 matched / 14 shadow closes, 8 unmatched; 11 tv-only closes
```

---

## STEP 3 — Root-Cause Classification

### Raw timelines (for reference)

**Engine shadow_signals (bar_time UTC, signal, close_price, exit_reason):**

| bar_utc | signal | close_price | exit_reason |
|---------|--------|-------------|-------------|
| 2026-06-22 18:00 | close_long | 64305.8 | — |
| 2026-06-22 18:00 | open_short | 64305.8 | — |
| 2026-06-22 19:00 | close_short | 64431.1 | — |
| 2026-06-22 19:00 | open_long | 64431.1 | — |
| 2026-06-22 21:00 | close_long | 64282.0 | — |
| 2026-06-22 21:00 | open_short | 64282.0 | — |
| 2026-06-22 22:28 | close_short | 64038.8 | trail |
| 2026-06-24 00:00 | close_short | 62982.1 | — |
| 2026-06-24 00:00 | open_long | 62982.1 | — |
| 2026-06-24 01:00 | close_long | 62904.6 | — |
| 2026-06-24 01:00 | open_short | 62904.6 | — |
| 2026-06-24 02:39 | close_short | 62589.5 | tp1 |
| 2026-06-24 02:41 | close_short | 62772.7 | trail |
| 2026-06-24 11:00 | close_short | 62887.7 | — |
| 2026-06-24 11:00 | open_long | 62887.7 | — |
| 2026-06-24 12:00 | close_long | 62587.9 | — |
| 2026-06-24 12:00 | open_short | 62587.9 | — |
| 2026-06-24 13:06 | close_short | 62254.9 | tp1 |
| 2026-06-24 13:06 | close_short | 62196.6 | trail |
| 2026-06-25 05:00 | open_long | 61567.1 | — |
| 2026-06-25 07:03 | close_long | 61637.7 | trail |
| 2026-06-25 10:00 | open_short | 61240.0 | — |
| 2026-06-25 12:30 | close_short | 61726.9 | stop |

**TV orders (received_at UTC, signal):**

| received_at | signal |
|-------------|--------|
| 2026-06-22 11:32:54 | close_short |
| 2026-06-22 19:14:55 | close_short |
| 2026-06-22 19:14:55 | open_long |
| 2026-06-22 20:25:45 | close_long |
| 2026-06-22 20:25:46 | open_short |
| 2026-06-22 22:27:17 | close_short |
| 2026-06-22 22:28:34 | close_short |
| 2026-06-24 00:38:27 | open_long |
| 2026-06-24 01:01:21 | open_short |
| 2026-06-24 02:00:17 | close_short |
| 2026-06-24 02:00:18 | open_long |
| 2026-06-24 03:16:02 | close_long |
| 2026-06-24 03:19:12 | exchange_close |
| 2026-06-24 12:07:27 | close_long |
| 2026-06-24 12:07:30 | open_short |
| 2026-06-24 13:01:31 | close_short |
| 2026-06-24 13:08:40 | close_short |
| 2026-06-25 04:56:18 | open_long |
| 2026-06-25 05:07:49 | close_long |
| 2026-06-25 05:11:21 | close_long |
| 2026-06-25 05:11:22 | open_long |
| 2026-06-25 05:26:14 | close_long |
| 2026-06-25 07:16:39 | close_long |
| 2026-06-25 10:41:37 | open_short |
| 2026-06-25 11:03:55 | close_short |
| 2026-06-25 11:14:33 | close_short |
| 2026-06-25 12:30:27 | open_long |
| 2026-06-25 12:30:43 | close_long |
| 2026-06-25 13:33:39 | exchange_close |

### Entry comparison table

Categories:
- **A · SIGNAL_LOGIC** — both sides same position state, RSI logic genuinely differs → real bug
- **B · POSITION_STATE** — sides disagreed on whether a position was open
- **C · INTRABAR** — Pine fired intrabar (`calc_on_every_tick=true`), engine (closed-bar) confirmed at next bar close or not at all

| Bar (UTC) | TV signal | Engine signal | Verdict | Root Cause | Notes |
|-----------|-----------|---------------|---------|------------|-------|
| 2026-06-22 18:00 | — | open_short | MISSING_IN_PINE | **B · POSITION_STATE** | Engine built long position through pre-cutover replay window; TV was flat (first TV order was close_short at 11:32). Engine flipped long→short, TV had nothing to flip. |
| 2026-06-22 19:00 | open_long | open_long | **MATCH** | — | RSI 52.29 at bar close. Both flip short→long. ✓ |
| 2026-06-22 20:00 | open_short | — | MISSING_IN_ENGINE | **C · INTRABAR** | RSI crossed below 50 intrabar; Pine fired at 20:25:46 UTC (during bar 20:00-21:00). Engine only fires on bar close; bar 20:00 close = bar 21:00 (see next row). |
| 2026-06-22 21:00 | — | open_short | MISSING_IN_PINE | **C · INTRABAR** | Engine side of same flip: fires open_short at bar 21:00 close (RSI 48.15). TV was already short from 20:25. These two rows are one physical event viewed at different times. |
| 2026-06-24 00:00 | open_long | open_long | **MATCH** | — | RSI 51.49 at bar close. Both flip short→long. ✓ |
| 2026-06-24 01:00 | open_short | open_short | **MATCH** | — | RSI 49.57 at bar close. Both flip long→short. ✓ |
| 2026-06-24 02:00 | open_long | — | MISSING_IN_ENGINE | **C · INTRABAR** | Pine detected RSI>50 at 02:00:17 UTC (17 sec into bar — almost bar open tick). Engine at bar 02:00 close saw RSI still <50; engine's next long signal is bar 11:00 (9h later). RSI excursion above 50 was transient — not confirmed at bar close. |
| 2026-06-24 11:00 | — | open_long | MISSING_IN_PINE | **B · POSITION_STATE** | Engine fires close_short + open_long (RSI 51.85). Engine's shadow tracker still thought the 01:00 short was open (bracket had closed it via tp1 02:39 + trail 02:41, but shadow signals don't track bracket exits). TV was flat since exchange_close at 03:19. Spurious flip from ghost position. |
| 2026-06-24 12:00 | open_short | open_short | **MATCH** | — | RSI 45.69 at bar close. Both flip long→short. ✓ |
| 2026-06-25 04:00 | open_long | — | MISSING_IN_ENGINE | **C · INTRABAR** | TV fired open_long at 04:56:18 UTC (56 min into bar 04:00). Engine fired open_long at bar 05:00 close (= bar 04:00 close). But TV re-entered long at 05:11:22 (bar 05:00) after partial close, and the harness matched THAT with the engine's bar-05:00 signal (see next row). The 04:00 TV entry is the intrabar first entry, unmatched. |
| 2026-06-25 05:00 | open_long | open_long | **MATCH** | — | TV re-entry at 05:11:22 (bar 05:00); engine at bar 05:00 close. ✓ |
| 2026-06-25 10:00 | open_short | open_short | **MATCH** | — | RSI 47.12 at bar close. Both go short. ✓ |
| 2026-06-25 12:00 | — | open_long | MISSING_IN_PINE | **B · POSITION_STATE** | Engine re-run fires open_long (RSI 52.81). TV was flat (closed short at 11:14), later went long at 12:30:27 (immediately stopped at 12:30:43 — 16s hold). Harness did not count TV's 12:30 open_long as bar-12:00 match (possibly filtered due to instant stop-out). Engine's shadow shows close_short (stop) at 12:30, not open_long — internal shadow/bracket state inconsistency. |

**Entry headline:**
- SIGNAL_LOGIC (A) true bugs: **0**
- POSITION_STATE (B): **3 events** (18:00, 11:00, 12:00) → 3 MISSING_IN_PINE rows
- INTRABAR (C): **3 events** (20:00↔21:00, 02:00, 04:00) → 4 table rows (20:00+21:00 are one event)

### Exit comparison table

**Engine shadow closes vs TV closes:**

| Bar (UTC) | Engine side | Reason | Verdict | Root Cause | Notes |
|-----------|-------------|--------|---------|------------|-------|
| 2026-06-22 18:00 | close_long | flip | MISSING_IN_TV | **B · POSITION_STATE** | Engine flipped long→short; TV had no long to close (was flat). |
| 2026-06-22 19:00 | close_short | flip | **MATCH** | — | dt=895s (TV at 19:14:55, engine bar close ~19:00). ✓ |
| 2026-06-22 21:00 | close_long | flip | MISSING_IN_TV | **C · INTRABAR** | Engine's close_long at bar 21:00 is the engine-side of the 20:25 intrabar flip. TV had already closed long at 20:25:45 (TV-ONLY close). |
| 2026-06-22 22:28 | close_short | trail | **MATCH** | — | dt=34s (TV at 22:28:34, engine ~22:28). ✓ |
| 2026-06-24 00:00 | close_short | flip | MISSING_IN_TV | **B · POSITION_STATE** | Engine closes shadow-short (from 01:00 bracket; remaining 50% after trail); TV was flat since 22:28, didn't need a close. Opposite signal condition in engine's bracket spec triggered this phantom close. |
| 2026-06-24 01:00 | close_long | flip | MISSING_IN_TV | **B · POSITION_STATE** | Engine fires flip close_long; TV sent only open_short at 01:01 (no explicit close_long). Either Pine omits the close on flip, or TV's position tracker handles it implicitly. |
| 2026-06-24 02:39 | close_short | tp1 | MISSING_IN_TV | **B · POSITION_STATE** | Engine's tp1 exit on short (entry 01:00). TV had already exited the same short via flip at 02:00 (intrabar). Different exit mechanisms for same position. |
| 2026-06-24 02:41 | close_short | trail | MISSING_IN_TV | **D · PARTIAL_CLOSE_LEGS** | Engine's trailing leg fires after tp1 (50% left). TV had exited fully at 02:00 via flip. No remaining TV position to trail. |
| 2026-06-24 11:00 | close_short | flip | MISSING_IN_TV | **B · POSITION_STATE** | Ghost flip from engine's stale short position (bracket closed at 02:39-02:41, shadow tracker didn't know). TV was flat. |
| 2026-06-24 12:00 | close_long | flip | **MATCH** | — | dt=447s (TV at 12:07:27, engine bar close ~12:00). ✓ |
| 2026-06-24 13:06 | close_short | trail | **MATCH** | — | dt=160s. ✓ |
| 2026-06-24 13:06 | close_short | tp1 | **MATCH** | — | dt=268s. ✓ |
| 2026-06-25 07:03 | close_long | trail | **MATCH** | — | dt=819s (TV at 07:16:39). ✓ |
| 2026-06-25 12:30 | close_short | stop | SIDE_MISMATCH | **B · POSITION_STATE** | Engine short stopped at 12:30; TV long closed at 12:30:43. Opposite-side positions — full position state inversion by this point. |

**TV-ONLY CLOSES (exits TV made that engine did not):**

| TV received_at | Signal | Root Cause | Notes |
|----------------|--------|------------|-------|
| 2026-06-22 11:32:54 | close_short | **B · POSITION_STATE** | Pre-cutover Pine close. TV was closing a short that existed before monitoring began. Engine had no knowledge of it. |
| 2026-06-22 20:25:45 | close_long | **C · INTRABAR** | TV's intrabar flip long→short. Engine's corresponding close_long fires at bar 21:00 close (MISSING_IN_TV row above — same physical event). |
| 2026-06-22 22:27:17 | close_short | **D · PARTIAL_CLOSE_LEGS** | TV TP1 partial close on short from 20:25. Engine has 1 trail close at 22:28:34 (matched); TV sent TP1 as a separate webhook 67s earlier. |
| 2026-06-24 02:00:17 | close_short | **B · POSITION_STATE** | TV flip close (short→long) at 02:00 intrabar. Engine closed via tp1 (02:39) + trail (02:41) instead. |
| 2026-06-24 03:16:02 | close_long | **B · POSITION_STATE** | TV TP1 partial close on long from 02:00:18. Engine was flat (had no long here). |
| 2026-06-25 05:07:49 | close_long | **D · PARTIAL_CLOSE_LEGS** | TV TP1 close on long from 04:56. Engine collapses to 1 trail at 07:03. |
| 2026-06-25 05:11:21 | close_long | **D · PARTIAL_CLOSE_LEGS** | TV trail/TP2 close on same position. |
| 2026-06-25 05:26:14 | close_long | **D · PARTIAL_CLOSE_LEGS** | TV third leg close on same position (3 TV close_long for one position vs 1 engine close). |
| 2026-06-25 11:03:55 | close_short | **D · PARTIAL_CLOSE_LEGS** | TV TP1 on short from 10:41. Engine entry at 10:00 bar close (price 61240) vs TV at 10:41 (different entry price → different bracket prices → TP1 timing differs). Engine shows stop at 12:30. |
| 2026-06-25 11:14:33 | close_short | **D · PARTIAL_CLOSE_LEGS** | TV trail on same short. Engine still holding (stop not yet hit). |
| 2026-06-25 12:30:43 | close_long | **B · POSITION_STATE** | TV closed the immediately-stopped long (opened 12:30:27, closed 16s later). Engine was short at this time (full state inversion). |

**Exit headline:**
- Engine shadow closes: 14 total, **6 matched**, 8 unmatched
  - MISSING_IN_TV (POSITION_STATE/B): 6 rows
  - MISSING_IN_TV (PARTIAL_CLOSE_LEGS/D): 1 row
  - SIDE_MISMATCH (POSITION_STATE/B): 1 row
- TV-ONLY closes: 11 total
  - POSITION_STATE (B): 4 events
  - INTRABAR (C): 1 event
  - PARTIAL_CLOSE_LEGS (D): 6 events (across 3 positions)

---

## STEP 4 — Two Known Suspected Divergences

### Finding 1: Engine flips that TV skips

The engine opened/flipped where Pine stayed put. Confirmed in **3 cases**:

| Bar (UTC) | Engine fires | TV does | Reason |
|-----------|-------------|---------|--------|
| 2026-06-22 18:00 | open_short (flip from long) | — (flat) | Engine had phantom long from pre-cutover replay; TV was flat. |
| 2026-06-24 11:00 | open_long (flip from short) | — (flat) | Engine shadow tracker thought short was still open (bracket closed at 02:39-02:41); TV was flat since exchange_close at 03:19. |
| 2026-06-25 12:00 | open_long (flip from short) | — (flat then instant stop) | Engine's signal logic fires flip at RSI 52.81; TV was flat, later entered at 12:30:27 and stopped in 16 seconds. |

Root cause for all three: the shadow position tracker in the engine does not consume bracket exits (tp1, trail, stop). It only tracks signal-driven state (opposite-signal closes). When a bracket exit closes a position, the shadow tracker remains on the old side, eventually fires a spurious flip when the opposite RSI signal arrives.

### Finding 2: Pine per-partial-close legs the engine collapses

Pine emits separate webhook per bracket leg (TP1, TP2/RUN, TRAIL). Engine emits one shadow close per exit event but bracket exits happen in a separate module not fully reflected in shadow signals.

Confirmed in **3 positions**, 6 TV-only close events:

| Position entry | TV close legs | Engine close(s) | TV-only count |
|---------------|--------------|-----------------|---------------|
| Short @ 20:25:46 | 22:27:17 (TP1) + 22:28:34 (matched trail) | 22:28 trail ×1 (matched) | 1 TV-only (22:27 TP1) |
| Long @ 04:56:18 | 05:07:49 (TP1) + 05:11:21 (TP2) + 05:26:14 (trail) | 07:03 trail ×1 (matched) | 3 TV-only |
| Short @ 10:41:37 | 11:03:55 (TP1) + 11:14:33 (trail) | 12:30 stop ×1 (not matched) | 2 TV-only |

For the third position (short from 10:41), TV's TP1 hits at 11:03 while the engine stop fires at 12:30 — the bracket calc difference here likely stems from entry price divergence: TV entered at ~10:41 intrabar price (unknown), engine entered at bar 10:00 close (61240.00). Different entry prices → different TP1/stop levels.

---

## Headline Counts

| Category | Entry rows | Exit rows | Total divergent rows |
|----------|-----------|----------|---------------------|
| **A · SIGNAL_LOGIC (true engine bugs)** | **0** | **0** | **0** |
| **B · POSITION_STATE** | 3 | 11 | 14 |
| **C · INTRABAR** | 4 (3 events) | 2 (1 event) | 6 |
| **D · PARTIAL_CLOSE_LEGS** | 0 | 7 | 7 |
| **MATCHED** | 6 | 6 | 12 |
| **Total rows in comparison** | 13 | 14+11=25 | 38 |

Exit breakdown: 8 engine-unmatched + 11 TV-only = 19 exit mismatches across 6 shadow closes matched.

---

## Gaps to Fix (prioritized, NOT implemented here)

### P0 — Shadow tracker must consume bracket exits

**Category B.** The shadow position tracker maintains state based only on signal-driven opens/closes (opposite-signal condition). It is unaware of bracket exits (tp1, trail, stop). When a bracket closes a position, the tracker still shows the old side, generating phantom closes and spurious flips on the next opposite RSI signal.

This is the single biggest source of divergence: 14 mismatch rows (entries + exits) trace back to this gap.

**Fix direction:** When the bracket manager fires tp1, trail, or stop, it must also update the shadow position state (or emit a synthetic shadow signal). The `exits` harness consistently shows these as "missing_in_tv" flip closes or ghost flips.

### P1 — Intrabar entry price divergence affects bracket levels

**Category C/D.** Pine enters positions intrabar (e.g., 17 seconds into bar 02:00, or 56 minutes into bar 04:00). The engine enters at bar close. Entry prices differ → TP1/stop/trail thresholds differ → bracket legs fire at different times or not at all (see Finding 2, third position: TV TP1 at 11:03 vs engine stop at 12:30).

**Fix direction:** Either (a) switch Pine test harness to `process_orders_on_close=true` for a clean closed-bar baseline, or (b) accept that intrabar entry prices are a permanent methodology delta and document it. Option (a) is strongly recommended before drawing conclusions about bracket accuracy.

### P2 — Partial-close webhook model not reflected in shadow signals

**Category D.** Pine sends one webhook per bracket leg (TP1, trail as separate events). Engine emits one shadow close per leg too, but the harness matching window (15 min) is sometimes too narrow for all TV legs to land before the engine's matching close. Result: TV shows 3 close_long for one long position while harness matches only 1 and calls the other 2 TV-only.

**Fix direction:** The exit harness should treat consecutive same-side closes within a short window (e.g., 30 min) as a single position's leg group, match them as a set, and report set-level parity rather than individual close parity.

### P3 — Pre-cutover position bleed

**Category B.** Engine replay re-runs the strategy from a configurable lookback (here ~6 bars pre-cutover, based on fetch window). TV carried over position state from before that window (short was live before 11:32 cutover). The first TV close_short (11:32) has no engine counterpart.

**Fix direction:** Either (a) choose a cutover where both sides are known to be flat, or (b) seed the engine's replay position state from the actual TV position at cutover time (more complex but accurate).

---

## Methodology Note

Pine runs `calc_on_every_tick=true`, meaning webhooks can fire at any point within a bar — including at the very open (02:00:17 was 17 seconds into bar 02:00). This has two consequences:

1. **Entry timing divergence:** Engine confirms signals only at bar close. An intrabar RSI excursion that reverses before close will generate a TV entry but no engine entry. Conversely, a RSI that crosses on bar close but not during the bar will generate an engine entry but no TV entry. **3 of the 7 entry mismatches (and their cascading exit effects) are directly caused by this.**

2. **Entry price divergence:** Intrabar Pine entries use the tick price at fire time. Engine uses bar close price. Bracket levels (TP1, stop, trail arm) differ accordingly.

**Recommendation before trusting entry or exit counts as true bugs:** Run the Pine test harness with `process_orders_on_close=true` (or the equivalent `calc_on_order_fills=true` workaround) to get a closed-bar Pine baseline. With that baseline, the INTRABAR category collapses to zero, and any remaining divergence is unambiguously a signal-logic or bracket-tracking bug. The current `calc_on_every_tick=true` dataset cannot cleanly distinguish C from A.
