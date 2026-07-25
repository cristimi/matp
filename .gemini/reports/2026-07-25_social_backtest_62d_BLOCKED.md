# Social Listener — 62-day backtest: BLOCKED (Anthropic credit exhausted)

**Date:** 2026-07-25
**Branch:** main
**Status:** NOT COMPLETED — no backtest result. One real bug found and fixed.

---

## What was asked

Re-extract the last 2 months of channel history with the v2 (vision) extractor and backtest it,
v2 only.

## What happened

The window is far larger than the 14-day run: **1170 messages** over 62 days (the channel
averaged 18.9 msgs/day in June vs 9.4 in July), 143 of them carrying a chart image. Extraction
was launched at concurrency 8.

**The Anthropic credit balance ran out partway through.**

```
succeeded: 85   failed(credit): 1085
actionable among succeeded: 0
succeeded date range: 2026-05-25 -> 2026-05-27
tokens: 157,758
```

```
Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error',
'message': 'Your credit balance is too low to access the Anthropic API.
Please go to Plans & Billing to upgrade or purchase credits.'}}
```

85 of 1170 messages extracted (2.5 days' worth) before the balance went. **There is no backtest
result** — I am not reporting a number computed from a 7%-complete extraction.

I under-estimated this: I sized the run from the 14-day window's message rate and did not check
the balance first. The 14-day run cost 249k tokens; this one needed roughly 2.3M.

---

## Blast radius

| System | Affected? | Why |
|---|---|---|
| **social-listener** | **Yes — extraction is down** | `extractor_provider = anthropic`, no fallback |
| ai-signal-generator | No | Last 12h: zhipu (54), groq (37), cerebras (25). Zero Anthropic calls — it last used Anthropic historically (117 all-time) but nothing current |
| order-listener / executor / dashboard | No | No LLM dependency |

Confirmed live-failing:

```
$ extract("BTC longs opened here, entry 65000", "")
live extractor status: FAILING
reasoning: extraction_error: ... credit balance is too low ...
```

**No live data was corrupted** — but only by luck of timing: the channel has not posted since
2026-07-24 21:41, so the running listener never attempted an extraction during the outage.

```
SELECT count(*) FROM social_signal_log
 WHERE confidence = 0 AND raw_llm_json->>'reasoning' LIKE 'extraction_error%';
 count
-------
     0
```

---

## The real bug this exposed

Had a message arrived during the outage, it would have been **silently and permanently lost**:

1. `extract()` caught the API exception and returned a `NONE / confidence 0.0` placeholder,
   indistinguishable from a genuine "this post is not a trade" verdict.
2. `handle()` passed that to `insert_signal()`, which wrote it.
3. `already_seen()` then returns True for that message **forever** — neither the catchup loop
   nor a restart backfill would ever re-extract it.

So a provider outage silently buries every signal posted during it, with no way to recover after
the fact and nothing in the logs to distinguish it from a quiet day.

### Fix

- `extract()` returns `failed=True` for transient API failures **only**. A `parse_error` is
  deliberately *not* flagged: the model did answer, tokens were spent, and a retry would likely
  fail identically — that case still persists as before.
- `handle()` logs an error and returns **without recording anything** when `failed` is set.
  Leaving the row unwritten also holds `max_channel_msg_id` back, so the existing catchup loop
  re-processes the message on its next pass once the provider recovers. No new retry machinery.

Verified against the live outage — the failure path is real right now, so this is not a
simulated test:

```
2026-07-25 15:08:28 WARNING app.extractor extraction failed: ... credit balance is too low ...
2026-07-25 15:08:28 ERROR social-listener msg 999999904: extraction unavailable, leaving unrecorded for retry

max_channel_msg_id before=9777 after=9777 (unchanged=True)
already_seen(999999904) = False  <- catchup will retry it
```

---

## To finish the backtest

Everything else is staged and needs no rework:

- 90 720 1m BTC bars (63 days) — fetched
- 189 funding points, mean 0.00431%/8h (4.72%/yr) — fetched
- `backtest_replay.py` now has a `--v2-only` flag and reports how many decision times are
  measured vs assumed (the signal log only starts 2026-06-11, so ~2/3 of a 62-day window has no
  recorded ingest latency and falls back to the measured live p50 of +19s)

Once the balance is topped up:

```bash
docker compose exec -T social-listener python -u -m app.backtest_extract 62 /tmp/v2_62d.json
docker compose exec -T social-listener python -m app.backtest_replay \
    /tmp/ohlcv_62d.json 62 --v2 /tmp/v2_62d.json --funding /tmp/funding_62d.json --v2-only
```

Budget ~2.3M tokens (~$7 at Sonnet 4.6 rates) for a cold extraction.

**Update — the run is now resumable** (migration 063, `public.social_extraction_cache`):
successful extractions are cached per `(message, extractor_version)` and checkpointed as they
complete, so a second attempt only pays for what is still missing. Failures are deliberately
never cached. A consecutive-failure circuit breaker (20) now aborts a run against a dead
provider instead of burning through the rest of the window — the first attempt made 1085
pointless calls after the balance went.

The cache is in Postgres rather than a file because container `/tmp` does not survive a
`--force-recreate` redeploy: that is exactly how this attempt's 85 successful extractions, the
90 720 OHLCV bars and the funding history were lost. Those three need re-fetching (free, ~2 min);
the extractions would have been reused had the cache existed at the time.

**Also worth deciding:** social-listener has no LLM fallback. `config.py` already supports
google/openai/groq and `config_secrets` loads keys for all of them, but `extractor_provider` is
a single value. A fallback chain would have degraded this to reduced quality instead of a total
outage.
