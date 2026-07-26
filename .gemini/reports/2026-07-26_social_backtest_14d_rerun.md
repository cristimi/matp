# Social Listener — 14-day backtest, re-run 4× to measure extractor stability

**Date:** 2026-07-26
**Branch:** main
**Window:** 2026-07-12 → 2026-07-26 (131 channel messages)
**Status:** DONE — v1 replayed once, v2 extracted and replayed 4× independently.
Read-only, no live tables mutated.

---

## Headline

**The P&L is stable; the extractor's per-message verdicts are not.**

Four independent v2 extractions of the same 131 messages (cache disabled, ~250k tokens each)
produced **the identical 4 trades and the identical +6.16% every time**. But only
**117 of 131 messages (89.3%) got the same verdict in all four runs** — the disagreements just
happened to land on messages the gates were going to reject anyway.

So the gates are absorbing real extractor noise. That is a good sign for the gate design and a
bad sign for trusting the extractor on its own.

---

## Correction to my earlier claim

Earlier today I reported this as "the v2 result is not reproducible", based on yesterday's run
giving 2 legs / +2.19% against today's 4 legs / +6.16%. **That framing was wrong.** Four same-day
runs are identical. Yesterday is a single outlier, not evidence of general instability — see
"The one real flip" below.

I also told Cristi there were "no results yet" because `social_extraction_cache` was empty. Also
wrong: the cache table (migration 063, `e1988ea`) landed at 16:38, after the 14-day run at 14:53,
so that run predates caching.

---

## Spread

| Run | actionable | legs | NET |
|---|---|---|---|
| 2026-07-25 (previous day) | 14 | 2 | **+2.19%** |
| 2026-07-26 · cached run | 16 | 4 | **+6.16%** |
| 2026-07-26 · #1 `--no-cache` | 14 | 4 | **+6.16%** |
| 2026-07-26 · #2 `--no-cache` | 16 | 4 | **+6.16%** |
| 2026-07-26 · #3 `--no-cache` | 15 | 4 | **+6.16%** |

**Spread across the four same-day runs: zero.** Identical legs, entries, exits, gross, fees.

```
##### RUN 1
messages=131  actionable=14   gate outcomes: no_state_change=6, not_whitelisted=3, ok=5
legs=4  wins=3  fills=8
gross $683.69   fees $64.00   funding $-4.18   NET $615.51 (+6.16% on $10,000 notional)

##### RUN 2
messages=131  actionable=16   gate outcomes: no_state_change=7, not_whitelisted=3, ok=5, stale_price=1
legs=4  wins=3  fills=8
gross $683.69   fees $64.00   funding $-4.18   NET $615.51 (+6.16% on $10,000 notional)

##### RUN 3
messages=131  actionable=15   gate outcomes: no_state_change=7, not_whitelisted=3, ok=5
legs=4  wins=3  fills=8
gross $683.69   fees $64.00   funding $-4.18   NET $615.51 (+6.16% on $10,000 notional)
```

Note `actionable` swings 14 / 15 / 16 while the trades stay fixed — the extra actionable
signals are all caught by `no_state_change`, `not_whitelisted` or `stale_price`.

Each run: `131 records (0 reused, 131 new), 0 failed, ~249 200 tokens` (~$0.75 each).

---

## What actually varies — per-message diff of the 4 runs

```
runs=4  messages=131
identical across all runs: 117/131 (89.3%)
disagree on any field:     14
disagree on is_actionable: 2 -> [9720, 9772]
confidence moved at all:   29

-- messages whose verdict fields differ --
  9663: reference_price=64450.0|64500.0|null
  9679: reference_price=65080.0|65080.4
  9680: reference_price=64087.8|null
  9691: direction="SHORT"|null
  9699: action_type="NONE"|"TRIM"
  9700: reference_price=63550.0|null
  9710: reference_price=62800.0|null
  9720: is_actionable=false|true; action_type="FLIP"|"NONE"; reference_price=62826.1|null
  9733: action_type="NONE"|"TRIM"
  9737: reference_price=66000.0|null
  9743: reference_price=66000.0|null
  9751: reference_price=66000.0|66245.7
  9768: reference_price=64000.0|null
  9772: is_actionable=false|true; action_type="NONE"|"OPEN"; direction="SHORT"|null

-- largest confidence swings --
  9725: 0.05 -> 0.90  (spread 0.85) actionable=False
  9743: 0.15 -> 0.95  (spread 0.80) actionable=False
  9728: 0.15 -> 0.90  (spread 0.75) actionable=False
  9699: 0.40 -> 0.95  (spread 0.55) actionable=False
  9691: 0.35 -> 0.85  (spread 0.50) actionable=False
  9710: 0.40 -> 0.85  (spread 0.45) actionable=False
  9772: 0.35 -> 0.72  (spread 0.37) actionable=False
  9744: 0.60 -> 0.85  (spread 0.25) actionable=False
```

Three findings worth acting on:

1. **`confidence` is close to meaningless on borderline posts.** The same message scoring 0.05
   in one run and 0.90 in another (9725) is not a calibrated probability. `confidence_floor`
   is therefore a much weaker filter than it looks.
2. **`reference_price` flips between a number and `null` on 8 of 131 messages.** This is the
   direct answer to "can the price be read from the picture?" — *sometimes*, and not
   consistently on the same input. When it comes back `null` the gate falls back to the implied
   market price (`bf7fa5d`), which is why this instability has not caused damage.
3. **Two messages flip `is_actionable` outright** (9720, 9772). Both are currently absorbed by
   later gates. Nothing guarantees the next one will be.

---

## The one real flip: msg 9751

Yesterday's 2-leg result versus today's 4-leg result comes down to a single message:

```
9751 | posted 2026-07-22 05:29:23+00 | OPEN BTC SHORT | ref 66000 | conf 0.80 | evidence "both"
```

It sits well inside yesterday's 07-11 → 07-25 window, so this is not a window-shift artefact —
yesterday's extraction simply did not act on it and all four of today's did. Taking it does two
things: it banks the open LONG at 66 044 instead of riding it back down (9716 gross **$511.28 vs
$245.51**) and the SHORT itself makes **+$190.02**. That single message is the entire
+2.19% → +6.16% difference.

Its `reference_price` is also one of the unstable ones (`66000.0|66245.7` across runs) — the
cited level versus a level read off the chart.

---

## Result detail (all four same-day runs)

```
window: 14d   BTC ~63950 -> 64374.5 (+0.65% buy & hold)
costs: taker 6bps + slippage 2bps per fill, $10,000 notional, funding modelled (42 pts)
seeded state at window open: {'BTC': 'LONG'}
decision times: 131 measured, 0 assumed

v1 — stored text-only extractions
messages=131  actionable=11
gate outcomes: no_state_change=5, not_whitelisted=3, ok=1, ok_implied_ref=2
   9670 SHORT    63807.0   63510.8    60.2h     46.42    5.38  -279.39    58.21  n  ok_implied_ref
   9716 LONG     62831.9   64374.5   217.9h    245.51  -12.62   -52.01   651.29  n  ok_implied_ref (OPEN)
legs=2  wins=2  fills=4
gross $291.93   fees $32.00   funding $-7.24   NET $252.69 (+2.53%)

v2 — re-extracted WITH chart images
   9670 SHORT    63807.0   63510.8    60.2h     46.42    5.38  -279.39    58.21  y  ok
   9716 LONG     62831.9   66044.4   118.8h    511.28   -7.03   -52.01   651.29  y  ok
   9751 SHORT    66044.4   64789.4    38.2h    190.02    1.53   -48.33   213.25  y  ok
   9758 LONG     64789.4   64374.5    61.0h    -64.04   -4.06  -173.39   152.90  n  ok (OPEN)
legs=4  wins=3  fills=8
gross $683.69   fees $64.00   funding $-4.18   NET $615.51 (+6.16%)
```

The two extra v2 trades are not the same kind of thing:

- **9751** — image + text (`evidence: "both"`), confidence 0.80, cited 66 000. The genuine win.
  Its `preview_text` is empty in `social_signal_log`, which is exactly the lost-preview bug
  `21541be` fixed — v1 had nothing to read at all.
- **9758** — **no image**, `evidence: "text"`, confidence **0.55** against a floor of 0.50, and
  the model *inferred* the direction ("Direction is inferred as LONG since a risk-off level
  (64.8k) is below the entry"). Currently **−$64.04**. Nothing to do with vision.

---

## Inputs

```
$ docker compose exec -T strategy-tester python /tmp/fetch_backtest_data.py 14 \
      /tmp/ohlcv_14d.json /tmp/funding_14d.json
ohlcv bars: 20161  1783845540000 -> 1785055140000  close 63971.2 -> 64374.5
funding pts: 42  mean 0.00514%/8h (5.62%/yr)
```

Committed as `scripts/fetch_backtest_data.py`. Container `/tmp` does not survive a redeploy, so
these are re-fetched rather than stored.

Total spend for this report: 4 × ~249 200 tokens ≈ **$3.00**, zero extraction failures.

---

## Caveats — still dominant

- **n = 4 positions.** Stable is not the same as meaningful. +6.16% on four trades is noise.
- **One leg is still open** and marked to the last bar (−$64.04).
- **No stop loss.** 9670 ended +$46.42 after drawing −$279.39 against itself first.
- **Wrong venue.** Binance perp prices; Blofin serves only ~3.5 days of history.
- Stability was measured over four runs within ~30 minutes on one model version. It says nothing
  about drift across model updates.

---

## Recommended next

1. **Do not raise `confidence_floor` and call it done.** The 0.05→0.90 swings show the score is
   not calibrated on borderline posts. Better lever: require `evidence` in (`image`, `both`) or a
   non-null `reference_price` for an entry — both are far more stable than `confidence`.
   Free to test against `social_extraction_cache`.
2. **Sample size is now the only real bottleneck.** 4 trades in 14 days. The 62-day window
   (~1170 messages, ~2.3M tokens, ~$7) is the next meaningful step and is now resumable.
3. **Still open (third day running):** social-listener has a single `extractor_provider` with no
   fallback. It was fully down again this morning — Anthropic, Gemini, OpenAI and Groq all dead
   simultaneously — while `openrouter` and `zhipu` keys sat in `llm_keys` with working credit.
   `ai-signal-generator` already solved this with a key pool; the listener has not.
