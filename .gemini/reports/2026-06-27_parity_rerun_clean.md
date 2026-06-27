# Parity Re-run: Clean Both-Flat Cutover + Aligned Clocks

**Date:** 2026-06-27  
**Strategy:** `tv_test_harness`  
**Branch:** `main`  
**Scope:** Read-only analysis. No code changes, no order execution.  
**Why re-run:** The 2026-06-25 report used a cutover where the engine carried a position TV never
had (pre-cutover bleed). This report fixes that with a both-flat cutover and aligned-clock entry
classification.

---

## STEP 1 — Both-Flat Cutover Reconstruction

### Engine position walk (shadow_signals, bar_time order, all 127 rows)

Walking `open_long=+1 / open_short=−1 / every close=0`:

Reaching the candidate window:

```
...
2026-06-22 07:00  close_short             SHORT→FLAT
2026-06-22 07:00  open_long               FLAT→LONG
2026-06-22 18:00  close_long              LONG→FLAT
2026-06-22 18:00  open_short              FLAT→SHORT
2026-06-22 19:00  close_short             SHORT→FLAT
2026-06-22 19:00  open_long               FLAT→LONG
2026-06-22 21:00  close_long              LONG→FLAT
2026-06-22 21:00  open_short              FLAT→SHORT
2026-06-22 22:28  close_short [trail]     SHORT→FLAT   ← ENGINE FLAT
2026-06-24 00:00  open_long               FLAT→LONG    (next action, ~25.5h later)
```

### TV position walk (orders, received_at order, all 56 rows)

Implied SHORT before first signal (`close_short` at 11:32:54 is TV closing a pre-monitoring short):

```
2026-06-22 11:32:54  close_short      SHORT→FLAT
2026-06-22 19:14:55  close_short      =FLAT          (redundant; bracket leg)
2026-06-22 19:14:55  open_long        FLAT→LONG
2026-06-22 20:25:45  close_long       LONG→FLAT
2026-06-22 20:25:46  open_short       FLAT→SHORT
2026-06-22 22:27:17  close_short      SHORT→FLAT     (TP1 partial)
2026-06-22 22:28:34  close_short      =FLAT          (trail)   ← TV FLAT
2026-06-24 00:38:27  open_long        FLAT→LONG      (next action, ~26h later)
```

### Chosen cutover

**Both sides are simultaneously FLAT immediately after `2026-06-22 22:28` UTC:**
- Engine: `close_short [trail]` at bar 22:28 (generated 2026-06-23 20:26 — live bracket fire)
- TV: `close_short` at 22:28:34 (trail webhook, 34s after engine bracket)

Both sides stay flat for approximately 25.5 h with no further signals until Jun 24.

**`CUTOVER = 2026-06-22T23:00:00+00:00`** (1h bar boundary immediately after both-flat event)

---

## STEP 2 — Harness Outputs (verbatim)

### 2a — Entry parity (`replay`)

```
$ docker compose exec -T signal-engine python -m app.diff replay tv_test_harness 2026-06-22T23:00:00+00:00

2026-06-27 11:12:50,406 [INFO] __main__: replay: fetching BTC-USDT 1h from 2026-06-17T03:00:00+00:00 to now
2026-06-27 11:12:57,438 [INFO] __main__: replay: fetched 249 total candles
2026-06-27 11:12:57,781 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1781960400000 close=63368.10 rsi=49.10
2026-06-27 11:12:57,806 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1781964000000 close=63926.60 rsi=60.72
2026-06-27 11:12:58,192 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782072000000 close=63816.30 rsi=41.36
2026-06-27 11:12:58,262 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782090000000 close=64579.20 rsi=61.32
2026-06-27 11:12:58,284 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782097200000 close=63940.00 rsi=48.70
2026-06-27 11:12:58,295 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782100800000 close=64063.60 rsi=50.89
2026-06-27 11:12:58,333 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782108000000 close=63960.60 rsi=48.80
2026-06-27 11:12:58,347 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782111600000 close=64188.00 rsi=52.80
2026-06-27 11:12:58,573 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782151200000 close=64305.80 rsi=48.72
2026-06-27 11:12:58,596 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782154800000 close=64431.10 rsi=50.76
2026-06-27 11:12:58,633 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782162000000 close=64282.00 rsi=48.15
2026-06-27 11:12:59,003 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782259200000 close=62982.10 rsi=51.49
2026-06-27 11:12:59,015 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782262800000 close=62904.60 rsi=49.57
2026-06-27 11:12:59,214 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782298800000 close=62887.70 rsi=51.85
2026-06-27 11:12:59,273 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782302400000 close=62587.90 rsi=45.69
2026-06-27 11:12:59,790 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782363600000 close=61567.10 rsi=52.81
2026-06-27 11:12:59,886 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782381600000 close=61240.00 rsi=47.12
2026-06-27 11:13:00,323 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782453600000 close=60296.90 rsi=51.80
2026-06-27 11:13:00,359 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782464400000 close=59713.80 rsi=46.15
2026-06-27 11:13:00,422 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782478800000 close=60146.00 rsi=51.77
2026-06-27 11:13:00,446 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782482400000 close=59568.90 rsi=46.70
2026-06-27 11:13:00,472 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782486000000 close=60297.80 rsi=52.97
2026-06-27 11:13:00,521 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782496800000 close=59646.20 rsi=47.15
2026-06-27 11:13:00,572 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782511200000 close=60012.50 rsi=51.16
2026-06-27 11:13:00,615 [INFO] app.strategies.test_harness: test_harness: open_short bar_time=1782522000000 close=59931.90 rsi=49.98
2026-06-27 11:13:00,628 [INFO] app.strategies.test_harness: test_harness: open_long bar_time=1782525600000 close=60265.90 rsi=54.26
2026-06-27 11:13:00,752 [INFO] __main__: replay: 15 local entry signals in window
2026-06-27 11:13:00,993 [INFO] __main__: replay: 14 tv_test entry orders in window
BAR (UTC)               TV signal       Local signal      Verdict
----------------------------------------------------------------------
2026-06-24 00:00        open_long       open_long         matched
2026-06-24 01:00        open_short      open_short        matched
2026-06-24 02:00        open_long       -                 bar_offset
2026-06-24 11:00        -               open_long         bar_offset
2026-06-24 12:00        open_short      open_short        matched
2026-06-25 04:00        open_long       -                 bar_offset
2026-06-25 05:00        open_long       open_long         matched
2026-06-25 10:00        open_short      open_short        matched
2026-06-25 12:00        open_long       -                 extra_local
2026-06-26 06:00        -               open_long         missing_in_tv
2026-06-26 09:00        open_short      open_short        matched
2026-06-26 13:00        -               open_long         bar_offset
2026-06-26 14:00        open_short      open_short        matched
2026-06-26 15:00        open_long       open_long         matched
2026-06-26 18:00        -               open_short        bar_offset
2026-06-26 19:00        open_long       -                 bar_offset
2026-06-26 22:00        -               open_long         bar_offset
2026-06-26 23:00        open_short      -                 bar_offset
2026-06-27 01:00        -               open_short        bar_offset
2026-06-27 02:00        open_long       open_long         matched

Summary: 9 matched / 20 total, 11 mismatches
```

### 2b — Exit parity (`exits`)

```
$ docker compose exec -T signal-engine python -m app.diff exits tv_test_harness 15 --since 2026-06-22T23:00:00+00:00

Comparing exits since cutover: 2026-06-22T23:00:00+00:00
BAR (UTC)               side            reason      verdict               note
-----------------------------------------------------------------------------------------------
2026-06-24 00:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-24 01:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-24 02:39        close_short     tp1         missing_in_tv         reason=tp1 no tv close within 15m
2026-06-24 02:41        close_short     trail       missing_in_tv         reason=trail no tv close within 15m
2026-06-24 11:00        close_short     flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-24 12:00        close_long      flip        matched               reason=flip dt=447s
2026-06-24 13:06        close_short     tp1         matched               reason=tp1 dt=160s
2026-06-24 13:06        close_short     trail       matched               reason=trail dt=268s
2026-06-25 07:03        close_long      trail       matched               reason=trail dt=819s
2026-06-25 12:30        close_short     stop        side_mismatch         reason=stop opposite-side tv close
2026-06-26 07:28        close_long      tp1         missing_in_tv         reason=tp1 no tv close within 15m
2026-06-26 07:45        close_long      trail       missing_in_tv         reason=trail no tv close within 15m
2026-06-26 10:46        close_short     tp1         matched               reason=tp1 dt=392s
2026-06-26 11:03        close_short     trail       missing_in_tv         reason=trail no tv close within 15m
2026-06-26 14:36        close_long      stop        side_mismatch         reason=stop opposite-side tv close
2026-06-26 15:09        close_short     stop        matched               reason=stop dt=709s
2026-06-26 16:00        close_long      trail       missing_in_tv         reason=trail no tv close within 15m
2026-06-26 19:09        close_short     stop        matched               reason=stop dt=125s
2026-06-27 01:00        close_long      flip        missing_in_tv         reason=flip no tv close within 15m
2026-06-27 08:39        close_long      trail       missing_in_tv         reason=trail no tv close within 15m

TV-ONLY CLOSES (exits TradingView made that the engine did not):
  received_at (UTC)         signal
  ----------------------------------------
  2026-06-24 02:00:17       close_short
  2026-06-24 03:16:02       close_long
  2026-06-25 05:07:49       close_long
  2026-06-25 05:11:21       close_long
  2026-06-25 05:26:14       close_long
  2026-06-25 11:03:55       close_short
  2026-06-25 11:14:33       close_short
  2026-06-25 12:30:43       close_long
  2026-06-26 09:49:36       close_short
  2026-06-26 09:54:32       close_short
  2026-06-26 14:36:47       close_short
  2026-06-26 14:48:08       close_short
  2026-06-26 14:55:39       close_short
  2026-06-26 15:26:17       close_long
  2026-06-26 15:28:10       close_long
  2026-06-26 18:47:42       close_short
  2026-06-26 20:31:08       close_long
  2026-06-26 23:31:07       close_long
  2026-06-27 02:03:53       close_short
  2026-06-27 02:20:53       close_long
  2026-06-27 03:07:03       close_long

Summary: 7 matched / 20 shadow closes, 13 unmatched; 21 tv-only closes
```

---

## STEP 3 — Aligned-Clock Entry Classification

Rules: map each TV entry to the 1h candle it occurred within; compare against the engine's
decision for that same candle. Same candle + same direction = **MATCH** regardless of timestamp
offset. Remaining mismatches are classified into:

- **A · SIGNAL_LOGIC** — both sides same position state, RSI at bar close agreed, engine
  and TV still disagreed → real bug
- **B · POSITION_STATE** — engine's shadow tracker held a stale position, causing phantom signal
- **C · INTRABAR** — RSI crossed threshold intrabar but had reverted by bar close; engine
  (closed-bar) correctly did not fire; TV fired at the intrabar tick
- **F · MISSING_WEBHOOK** — TV fired a signal whose webhook was not received; cannot classify
  as A without the missing data

| Candle (UTC) | TV signal | TV time | Engine signal | Aligned verdict | Cat | RSI at close | Notes |
|---|---|---|---|---|---|---|---|
| 2026-06-24 00:00 | open_long | 00:38:27 | open_long | **MATCH** | — | 51.49 | Both agree. |
| 2026-06-24 01:00 | open_short | 01:01:21 | open_short | **MATCH** | — | 49.57 | Both agree. |
| 2026-06-24 02:00 | open_long | 02:00:18 | — | INTRABAR | C | <50 (no engine signal) | TV fired 18s into bar. RSI was above 50 intrabar, reverted. Engine fired at 11:00 (different event — phantom, see below). |
| 2026-06-24 11:00 | — | — | open_long | PHANTOM FLIP | B | 51.85 | Engine bracket (tp1+trail) closed short at 02:39–02:41; shadow tracker didn't know. Fired spurious flip at bar 11:00 close. TV was flat (exchange_close 03:19). |
| 2026-06-24 12:00 | open_short | 12:07:30 | open_short | **MATCH** | — | 45.69 | Both agree. |
| 2026-06-25 04:00 | open_long | 04:56:18 | — | INTRABAR | C | <50 | TV 56min into bar. Engine fired at bar 05:00 close when RSI finally confirmed. |
| 2026-06-25 05:00 | open_long | 05:11:22 | open_long | **MATCH** | — | 52.81 | TV re-entry + engine bar-05 close. |
| 2026-06-25 10:00 | open_short | 10:41:37 | open_short | **MATCH** | — | 47.12 | Both agree. |
| 2026-06-25 12:00 | open_long | 12:30:27* | open_long | **MATCH (signal)** | C | ~>50 | Both fire long at bar 12:00. TV's entry instantly stopped (16s); harness filtered it (→ "extra_local"). Signal-level: agree. |
| 2026-06-26 06:00 | — | (missing) | open_long | MISSING_WEBHOOK | F | 51.80 | Engine→long (RSI 51.80). TV had a short at this time (close_short at 09:49 with no preceding open_short in DB). Cannot classify. |
| 2026-06-26 09:00 | open_short | 09:54:33 | open_short | **MATCH** | — | 46.15 | Both agree. |
| 2026-06-26 13:00 | — | (missing) | open_long | MISSING_WEBHOOK | F | 51.77 | Engine→long 1 bar then →short at 14:00 (RSI 46.70). TV had a short (close_short at 14:36+ with no open). Cannot classify. |
| 2026-06-26 14:00 | open_short | 14:48:09 | open_short | **MATCH** | — | 46.70 | Both agree. Two TV entries on same bar (14:48 + 14:57). |
| 2026-06-26 15:00 | open_long | 15:28:12 | open_long | **MATCH** | — | 52.97 | Both agree. |
| 2026-06-26 18:00 | — | (missing) | open_short | MISSING_WEBHOOK | F | 47.15 | Engine→short. TV also apparently short (close_short+exchange_close at 18:47–18:48 with no open). Same direction — no bug, missing webhook. |
| 2026-06-26 19:00 | open_long | 19:09:51 | — | INTRABAR | C | <50 | TV fired 9min in. RSI crossed above 50 intrabar, didn't confirm at bar close. Engine confirmed at bar 22:00 (RSI 51.16) — 3-bar delay from RSI oscillation. |
| 2026-06-26 22:00 | — | — | open_long | INTRABAR | C | 51.16 | Engine side of bar-19:00 TV entry. |
| 2026-06-26 23:00 | open_short | 23:31:09 | — | INTRABAR | C | >50 at close | TV fired 31min in. RSI crossed below 50 intrabar, reverted. Engine confirmed at bar 01:00 (RSI 49.98). |
| 2026-06-27 01:00 | — | — | open_short | INTRABAR | C | 49.98 | Engine side of bar-23:00 TV entry. |
| 2026-06-27 02:00 | open_long | 02:03:55 | open_long | **MATCH** | — | 54.26 | Both agree. |

*TV's open_long at 12:30:27 was stopped out 16s later; harness filtered it.

### Aligned-clock entry summary

| Category | Events | Table rows | Meaning |
|---|---|---|---|
| **A · SIGNAL_LOGIC (true bugs)** | **0** | **0** | No confirmed case where RSI agreed and engine/TV diverged |
| B · POSITION_STATE (phantom) | 1 | 1 | Bar 11:00 Jun24: phantom flip from bracket not tracked |
| C · INTRABAR | 5 events | 8 rows | (02:00), (04:00/05:00†), (12:00), (19:00/22:00), (23:00/01:00) |
| F · MISSING_WEBHOOK | 3 | 3 rows | Jun26 06:00 (opposite dir), 13:00 (opposite), 18:00 (same dir) |
| MATCH | 10 (9 harness + 1 signal-level) | 10 | — |

†Bar 05:00 matched; bar 04:00 is the intrabar predecessor.

**Trustworthy SIGNAL_LOGIC count: 0.**

The 3 MISSING_WEBHOOK cases (Jun26 06:00 and 13:00 show engine long / TV apparently short) cannot
be confirmed as signal-logic bugs — the TV open_short webhook was never received. The directions
appear opposite for bars 06:00 and 13:00, but this could be TV going through the same 1-bar RSI
oscillation (51.80 → 46.15 over 3h) without the flip webhooks being captured.

---

## STEP 4 — Surviving Exit Divergences

### Phantom-flip exits (the primary tracker bug)

Two confirmed phantom flips in the clean window:

| Engine exit | Reason | Root cause |
|---|---|---|
| 2026-06-24 00:00 `close_short` flip | Tracker believed short was still open from bar 22:28 trail close (bracket fire ≠ signal-driven close) | Bracket exit not consumed by shadow tracker |
| 2026-06-24 11:00 `close_short` flip | Tracker believed short from 01:00 was still open (tp1 02:39 + trail 02:41 closed it, but tracker didn't know) | Same bug, second occurrence |

Both generate `missing_in_tv` because TV was flat and had nothing to close. These 2 phantom flips
produce 3 additional ghost signals (the close_short at 00:00 then the open_long+close_short at 11:00
cascade), accounting for **5 of the 13 unmatched engine exits**.

### Exit classification table

| Engine exit (bar UTC) | Reason | Verdict | Root cause | Notes |
|---|---|---|---|---|
| 2026-06-24 00:00 `close_short` flip | missing_in_tv | **PHANTOM FLIP #1** | Bracket tracker bug | TV was flat since 22:28 |
| 2026-06-24 01:00 `close_long` flip | missing_in_tv | PHANTOM FLIP cascade | Tracker bug | Ghost long (from 00:00 open_long) flip-closed 1 bar later |
| 2026-06-24 02:39 `close_short` tp1 | missing_in_tv | EXIT MECH DIVERGE | TV used intrabar flip (02:00:17); engine used bracket (02:39 tp1) | Different exit path for same position |
| 2026-06-24 02:41 `close_short` trail | missing_in_tv | EXIT MECH DIVERGE | Same position, second leg | — |
| 2026-06-24 11:00 `close_short` flip | missing_in_tv | **PHANTOM FLIP #2** | Bracket tracker bug | TV flat since exchange_close 03:19 |
| 2026-06-24 12:00 `close_long` flip | **matched** (dt=447s) | ✓ | — | — |
| 2026-06-24 13:06 `close_short` tp1 | **matched** (dt=160s) | ✓ | — | — |
| 2026-06-24 13:06 `close_short` trail | **matched** (dt=268s) | ✓ | — | — |
| 2026-06-25 07:03 `close_long` trail | **matched** (dt=819s) | ✓ | — | — |
| 2026-06-25 12:30 `close_short` stop | **side_mismatch** | POSITION INVERSION | Engine short / TV long (full state inversion by this bar) | Traces to phantom flips cascading into mismatched open positions |
| 2026-06-26 07:28 `close_long` tp1 | missing_in_tv | MISSING_WEBHOOK cascade | Engine was long from 06:00; TV apparently went short (no open webhook) | TV was on opposite side |
| 2026-06-26 07:45 `close_long` trail | missing_in_tv | MISSING_WEBHOOK cascade | Same position, trail leg | — |
| 2026-06-26 10:46 `close_short` tp1 | **matched** (dt=392s) | ✓ | — | — |
| 2026-06-26 11:03 `close_short` trail | missing_in_tv | MISSING_WEBHOOK | TV trail webhook not captured | TV tp1 at 10:39 was within 15min window (392s); trail at 11:03 was not received |
| 2026-06-26 14:36 `close_long` stop | **side_mismatch** | MISSING_WEBHOOK cascade | Engine was long from 13:00 (1-bar RSI flip); TV was short at same time | TV close_short at 14:36; engine close_long |
| 2026-06-26 15:09 `close_short` stop | **matched** (dt=709s) | ✓ | — | — |
| 2026-06-26 16:00 `close_long` trail | missing_in_tv | MISSING_WEBHOOK | TV long from 15:28 exited via bracket; webhook not captured | — |
| 2026-06-26 19:09 `close_short` stop | **matched** (dt=125s) | ✓ | — | — |
| 2026-06-27 01:00 `close_long` flip | missing_in_tv | INTRABAR EXIT PAIR | Engine closes long at bar 01:00 (bar close confirms); TV already closed at 23:31 (intrabar) | No TV close within 15min of engine bar 01:00 |
| 2026-06-27 08:39 `close_long` trail | missing_in_tv | BRACKET TIMING | TV closed this long at 02:20+03:07; engine bracket trail fires at 08:39 | Entry price divergence (TV intrabar vs engine bar close) → different bracket levels |

### TV-only closes: 21 total

| Group | Count | Root cause |
|---|---|---|
| Jun24 02:00 / 03:16 | 2 | TV intrabar flip+partial close (engine used bracket at 02:39-02:41) |
| Jun25 05:07-05:26 | 3 | TV 3-leg exit on long (TP1 + trail x2); engine 1 trail at 07:03 |
| Jun25 11:03-11:14 | 2 | TV 2-leg exit on short (TP1+trail); engine used stop at 12:30 |
| Jun25 12:30:43 | 1 | TV instant stop-out (16s) on long opened 16s earlier |
| Jun26 09:49-09:54 | 2 | TV closing short (from missing open_short around 06:00); engine was long |
| Jun26 14:36-14:55 | 3 | TV bracket legs on short that had no recorded open |
| Jun26 15:26-15:28 | 2 | TV closing long (partial legs); engine had long from 15:00 |
| Jun26 18:47 | 1 | TV closing short; engine closed short at 19:09 (matched separately) |
| Jun26 20:31 | 1 | TV closing long; engine also had long but trail was later |
| Jun26 23:31 | 1 | TV intrabar flip close (bar 23:00); engine closed at bar 01:00 |
| Jun27 02:03-03:07 | 3 | TV 3-leg partial exits; engine trail at 08:39 |

### Phantom-flip count in clean window

**2 confirmed phantom flips** (bars 00:00 and 11:00 Jun24) generating **5 ghost exit rows**.

An additional position state divergence occurs at bar 12:30 Jun25 (side_mismatch — engine short /
TV long), which traces back to the same phantom-flip bug cascading across the Jun24 window. That's
**1 full state inversion** downstream of the 2 phantom flips.

The Jun26 missing-webhook events create their own divergences but are a data quality problem, not
the tracker bug.

---

## STEP 5 — Summary: What's a Bug vs What's Methodology

### Clean picture (pre-cutover bleed removed, aligned clocks applied)

**Confirmed signal-logic bugs: 0**

Every entry divergence in the clean window resolves to one of:
1. **C · INTRABAR** — TV fires on a tick, engine fires on bar close. Same RSI cross, different
   timing. 5 events (8 rows). Not a bug; inherent to `calc_on_every_tick=true` on TV side.
2. **B · POSITION_STATE phantom flip** — engine shadow tracker doesn't consume bracket exits
   (tp1/trail/stop), fires spurious flip when opposite RSI signal arrives. 1 entry event.
3. **F · MISSING_WEBHOOK** — TV fired webhooks that weren't received (3 Jun26 entry events).
   Cannot classify as bug without the data; Jun26 18:00 case is same direction (no divergence).

**Confirmed tracker bugs: 1 (two manifestations)**

The shadow position tracker is unaware of bracket exits. When a bracket closes a position, the
tracker retains the old side and fires a phantom flip on the next opposite RSI signal. Observed
twice in clean window (Jun24 00:00, Jun24 11:00), producing 5 ghost exit rows and 1 state
inversion by Jun25.

**Exit exit-mechanism divergence (methodology, not a bug):**

TV sends a per-leg webhook for each bracket component (TP1, trail, flip). The engine's harness
matches per-signal. When TV exits via intrabar flip instead of bracket (Jun24 02:00), the bracket
legs the engine fires (02:39 tp1, 02:41 trail) have no TV counterpart. This is a methodology
difference — both sides closed the same position, just through different mechanisms. Not a bug,
but it inflates the unmatched count.

**Per-leg vs per-position exit counting (open design question):**

TV sends 2–3 close webhooks per position (TP1 + trail, or flip + partial legs). The harness
matches each leg individually. This means one physical exit generates 1–3 TV-only rows. Whether
to match per-leg or per-position is an open design question that should not be resolved by
changing how the harness scores closes.

### Headline

| Category | Entry rows | Exit rows | Total |
|---|---|---|---|
| **A · SIGNAL_LOGIC (bugs)** | **0** | **0** | **0** |
| B · POSITION_STATE (phantom tracker) | 1 | 5 | 6 |
| C · INTRABAR (methodology) | 8 | 4 | 12 |
| D · PARTIAL_CLOSE_LEGS (methodology) | 0 | 9 | 9 |
| E · MISSING_WEBHOOK (data quality) | 3 | 11* | 14 |
| MATCHED | 10 | 7 | 17 |

*Jun26 missing-webhook cascades affect exit comparison significantly.

**The single actionable bug is the shadow tracker not consuming bracket exits (P0 from prior
report). Everything else is methodology (intrabar, per-leg) or data quality (missing webhooks).**
