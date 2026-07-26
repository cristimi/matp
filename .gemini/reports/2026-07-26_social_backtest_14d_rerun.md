# Social Listener — 14-day backtest re-run (2026-07-26)

**Date:** 2026-07-26
**Branch:** main
**Window:** 2026-07-12 → 2026-07-26 (131 channel messages)
**Status:** DONE — v1 and v2 both replayed. Read-only, no live tables mutated.

---

## Headline

**The v2 (vision) result is not reproducible.** Same code, same model, same window, same
gates — re-extracted 24 hours apart it produced a different set of trades and a materially
different return:

| Run | actionable | legs | NET |
|---|---|---|---|
| 2026-07-25 | 14 | 2 | **+2.19%** |
| 2026-07-26 | 16 | 4 | **+6.16%** |

That swing is extraction noise, not alpha. It is the most important thing this run found and
it undercuts using either number as evidence.

---

## Interruption: the run was blocked, then unblocked

Every LLM provider the listener can use was dead when this started. All four supported by
`extractor.py`:

```
anthropic  claude-sonnet-4-6   400 "Your credit balance is too low to access the Anthropic API."
google     gemini-2.5-flash    429 RESOURCE_EXHAUSTED "Your prepayment credits are depleted."
openai     gpt-4o-mini         429 "You exceeded your current quota..."
groq       (model list)        HTTP 403 Forbidden — key rejected outright
```

Keys that did have credit (tested live via `ai-signal-generator`'s key pool): `openrouter`
(which also serves `anthropic/claude-sonnet-4.6`, the same model) and `zhipu`. Cristi chose to
top up Anthropic rather than add a provider, so **no code was changed**. After the top-up:

```
version: v2 | provider: anthropic | model: claude-sonnet-4-6
failed: False | action: OPEN | asset: BTC | dir: LONG | ref: 65000.0 | conf: 0.97 | tokens: 1700
```

---

## Inputs (re-fetched — the previous run's files died with the container)

```
$ docker compose exec -T strategy-tester python /tmp/fetch_backtest_data.py 14 \
      /tmp/ohlcv_14d.json /tmp/funding_14d.json
ohlcv bars: 20161  1783845540000 -> 1785055140000  close 63971.2 -> 64374.5
funding pts: 42  mean 0.00514%/8h (5.62%/yr)
```

The fetcher is now committed as `scripts/fetch_backtest_data.py` so this is not re-derived a
third time.

```
$ docker compose exec -T social-listener python -u -m app.backtest_extract 14 /tmp/v2_14d.json
INFO:backtest-extract:wrote /tmp/v2_14d.json — 131 records (0 reused, 131 new), 0 failed, 249181 tokens
```

249 181 tokens (~$0.75), zero failures. All 131 are now checkpointed in
`social_extraction_cache`, so a re-run of this window is free.

---

## Result

```
$ docker compose exec -T social-listener python -m app.backtest_replay \
      /tmp/ohlcv_14d.json 14 --v2 /tmp/v2_14d.json --funding /tmp/funding_14d.json

window: 14d   BTC 63956.1 -> 64374.5 (+0.65% buy & hold)
costs: taker 6bps + slippage 2bps per fill, $10,000 notional, funding modelled (42 pts)
seeded state at window open: {'BTC': 'LONG'}

==========================================================================
v1 — stored text-only extractions
==========================================================================
messages=131  actionable=11
gate outcomes: no_state_change=5, not_whitelisted=3, ok=1, ok_implied_ref=2
    msg side       entry      exit     held   gross $  fund $    MAE $    MFE $  img reason
   9670 SHORT    63807.0   63510.8    60.2h     46.42    5.38  -279.39    58.21  n  ok_implied_ref
   9716 LONG     62831.9   64374.5   217.9h    245.51  -12.62   -52.01   651.29  n  ok_implied_ref (OPEN)

legs=2  wins=2  fills=4
gross $291.93   fees $32.00   funding $-7.24   NET $252.69 (+2.53% on $10,000 notional)
decision times: 131 measured, 0 assumed (+19s, the live p50)

==========================================================================
v2 — re-extracted WITH chart images
==========================================================================
messages=131  actionable=16
gate outcomes: no_state_change=7, not_whitelisted=3, ok=5, stale_price=1
    msg side       entry      exit     held   gross $  fund $    MAE $    MFE $  img reason
   9670 SHORT    63807.0   63510.8    60.2h     46.42    5.38  -279.39    58.21  y  ok
   9716 LONG     62831.9   66044.4   118.8h    511.28   -7.03   -52.01   651.29  y  ok
   9751 SHORT    66044.4   64789.4    38.2h    190.02    1.53   -48.33   213.25  y  ok
   9758 LONG     64789.4   64374.5    61.0h    -64.04   -4.06  -173.39   152.90  n  ok (OPEN)

legs=4  wins=3  fills=8
gross $683.69   fees $64.00   funding $-4.18   NET $615.51 (+6.16% on $10,000 notional)
```

Every decision time is measured (`131 measured, 0 assumed`) — no backfill contamination, the
listener was live all window.

---

## Where the +3.63pp difference actually comes from

Two extra signals, and they are not the same kind of thing:

- **msg 9751 — the real win.** `evidence: "both"`, image + text, confidence 0.80, cited price
  66 000. Text: *"after closing a lot of long exposure, I am slowly adding some countertrend
  short exposure again here"*. v1 missed it entirely. Taking it did two things: it banked the
  LONG at 66 044 instead of riding it back down (9716 gross **$511.28 vs $245.51**, the single
  largest contribution), and the SHORT itself made **+$190.02**. Together that is the whole
  gain.
- **msg 9758 — the marginal one.** `evidence: "text"`, **no image**, confidence **0.55** against
  a floor of 0.50. The model *inferred* the direction: *"Direction is inferred as LONG since a
  risk-off level (64.8k) is below the entry"*. It is currently **−$64.04** and open. Nothing to
  do with vision — a text-only guess that scraped over the threshold.

So the honest read: one genuinely image-driven call earned the difference; one low-confidence
inference is losing money. `confidence_floor = 0.5` is doing very little work at 0.55.

---

## Why the two runs disagree

Ruled out first — the code did not change. The only edit to `extractor.py` since yesterday's
run (`73bc0ca`) is the `failed` flag from `3dbf983`; `SYSTEM_PROMPT`, the model and
`statemachine.py` are byte-identical:

```
$ git diff 73bc0ca..HEAD -- social-listener/app/{extractor,statemachine,config}.py
  (only: + call_failed = False ... + "failed": call_failed)
```

So with `temperature = 0` and an unchanged prompt, the same 131 messages yielded 14 actionable
yesterday and 16 today. Temperature 0 is not a determinism guarantee — yesterday's report
flagged this as a caveat; this run turned it into a measured fact. Vision extraction over
compressed chart images looks especially unstable: the marginal calls (0.55–0.80 confidence)
flip between runs, and those are exactly the ones the gates let through.

---

## Correction

I told Cristi earlier today there were "no results yet" because `social_extraction_cache` was
empty. Wrong: the cache table (migration 063, `e1988ea`) landed 16:38, after the 14-day run at
14:53 — that run predates caching and was never cached. The 2026-07-25 report had the answer all
along.

---

## Caveats — these still dominate the number

- **n = 4.** Four positions is not a sample. +6.16% and a 75% win rate are noise.
- **Not reproducible** (above). Any single run's P&L is one draw from a distribution nobody has
  measured.
- **No stop loss.** 9670 ended +$46.42 after drawing −$279.39 against itself first.
- **Wrong venue.** Binance perp prices, because Blofin serves only ~3.5 days of history.
- One leg is still open and marked to the last bar.

---

## Recommended next

1. **Measure the instability before anything else.** Re-extract this same window 3–5 times
   (~$0.75 each, cache disabled via `--no-cache`) and report the spread of NET. If it is wide,
   no backtest of this extractor means anything until it is pinned down.
2. **Raise `confidence_floor`.** 0.55 passing on an *inferred* direction is the weakest trade
   in the set. Worth testing 0.65–0.70 against the cache — free, no new extraction.
3. **Still open (third day running):** social-listener has a single `extractor_provider` with no
   fallback. It was fully down twice this week for exactly that reason, while `openrouter` and
   `zhipu` keys sat in `llm_keys` with credit. `ai-signal-generator` already solved this with a
   key pool; the listener has not.
