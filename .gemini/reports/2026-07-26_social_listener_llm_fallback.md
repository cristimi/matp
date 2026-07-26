# Social Listener — LLM fallback chain (+ partial Gemini cross-check)

**Date:** 2026-07-26
**Branch:** main
**Status:** Fallback DONE, deployed and proven. Full Gemini backtest **NOT completed** —
blocked by a hard free-tier quota, see below.

---

## What was asked

Add a fallback provider so the listener stops going down, preferably using the second Gemini
key, then run the 14-day backtest once on Gemini to compare.

## What shipped

The listener had a single `extractor_provider` and no fallback. It went fully down on
2026-07-25 and again on 2026-07-26 when the Anthropic balance ran out. Two changes:

**1. `config_secrets.py` — load every key, not just the first.**

The old query was `SELECT DISTINCT ON (provider) ... ORDER BY provider, priority, id`, so only
the priority-0 key per provider ever reached the service. That is precisely why the Gemini key
looked dead: priority-0 (`imported from .env`) is out of credit, and priority-1 (`key 2`) —
which works — was never loaded. All enabled keys now land in `settings.provider_keys` in
priority order; the highest-priority one is still mirrored into the legacy
`<provider>_api_key` attribute.

**2. `extractor.py` — walk a chain of (provider, model, key).**

New setting `extractor_fallbacks` (default `"google:gemini-3.6-flash"`), comma-separated
`provider:model` entries. `_attempts()` expands the primary plus the chain, and each provider
contributes one attempt *per key it holds*, so a dead high-priority key falls through to its
sibling before the next provider is tried.

A transient failure (no credit, rate limit, 5xx, network) advances to the next attempt. A
**parse** failure deliberately does not — the model answered, so the next provider would likely
answer the same way. Only when every attempt fails is `failed=True` returned, which leaves the
message unrecorded for the catchup loop to retry (`3dbf983`).

`"model"` in the result now reports the provider/model that actually answered, not the
configured one, so `social_signal_log.model` tells the truth about who read a post.

### Proof — primary broken on purpose, real chart image attached

```
$ # settings.provider_keys['anthropic'] = ['sk-ant-invalid-forced-failure']
msg 9751 has_image: True | image bytes: 137594

WARNING app.extractor extraction failed on anthropic:claude-sonnet-4-6 (attempt 1/3):
        Error code: 401 - {'type': 'authentication_error', 'message': 'invalid x-api-key'}
WARNING app.extractor extraction failed on google:gemini-3.6-flash (attempt 2/3):
        429 RESOURCE_EXHAUSTED "Your prepayment credits are depleted."
WARNING app.extractor extraction fell back to google:gemini-3.6-flash after 2 failed attempt(s)

failed: False | model: google:gemini-3.6-flash
actionable: False | action: ADD | asset: BTC | dir: SHORT | ref: 66245.7 | conf: 0.9
evidence: both | tokens: 2955
```

It failed past a dead provider *and* a dead key, then read the chart image correctly on the
third attempt. Live startup confirms the multi-key load:

```
INFO app.config_secrets config: loaded 1 key(s) for anthropic
INFO app.config_secrets config: loaded 2 key(s) for gemini
INFO app.config_secrets config: loaded 1 key(s) for groq
INFO app.config_secrets config: loaded 1 key(s) for openai
INFO social-listener Listening for new messages...
```

---

## The Gemini backtest did not finish — free-tier quota

The run aborted after 21 of 131 messages:

```
ERROR:backtest-extract:ABORTED after 20 consecutive failures — provider looks down.
INFO:backtest-extract:wrote /tmp/v2_gemini.json — 21 records, 21 failed, 21904 tokens
WARNING:backtest-extract:INCOMPLETE: 110 of 131 messages have no extraction
```

Root cause, from the API's own error body:

```
429 RESOURCE_EXHAUSTED
Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests
limit: 20, model: gemini-3.6-flash
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
quotaValue: '20'
```

**Gemini key 2 is on the free tier: 20 requests per day, per model.** The 21 successes consumed
it; everything after failed. `gemini-2.5-pro` returned exhausted as well, so switching model did
not buy a fresh allowance today.

The circuit breaker did its job — it stopped after 20 consecutive failures instead of burning
through the remaining 110, which is exactly the behaviour added after the 62-day run wasted
1085 calls.

### What this means for the fallback

The fallback is real but **thin**: ~20 posts/day. The channel averages 9–19 messages/day, so it
covers roughly one day of an Anthropic outage and no more. It is a genuine safety net against
the failure that hit twice this week, not a second production provider.

Durable options, in order of preference:
1. Put credit on a Gemini key (removes the free-tier cap entirely).
2. Wire `openrouter` into the listener — its key is in `llm_keys`, has credit, and serves
   `anthropic/claude-sonnet-4.6`, i.e. the *same* model as the primary. Not done: Cristi chose
   to top up Anthropic rather than add a provider earlier today.

---

## Partial cross-check: Gemini vs Claude on the 21 messages both read

```
gemini records=21  claude records=21  comparable=21

msg 9650  (image=False)
    asset: claude=None gemini='BTC'
    direction: claude=None gemini='LONG'
    reference_price: claude=None gemini=61000.0
    confidence: claude=0.95 gemini=0.95

msg 9654  (image=True)
    direction: claude='LONG' gemini=None
    confidence: claude=0.85 gemini=0.95

identical on all 5 verdict fields: 19/21 (90%)
actionable — claude: 2 [9657, 9660]
actionable — gemini: 2 [9657, 9660]
```

90% field-level agreement, and **identical on which messages are actionable** — the decision
that actually matters. On this sample Gemini is a credible stand-in.

### But the one message that matters, it read differently

The 21 messages cover 9645–9688 and do **not** include msg 9751 — the post that drove the
entire +2.19% → +6.16% difference in today's earlier backtest. The forced-failure test above
extracted exactly that message with Gemini:

| | action_type | actionable | reference_price |
|---|---|---|---|
| Claude Sonnet 4.6 | `OPEN` | **true** | 66 000 |
| Gemini 3.6 Flash | `ADD` | **false** | 66 245.7 |

`ADD` is forced non-actionable by contract. So on the single most consequential post in the
window, Gemini would have taken **no trade at all** — removing both the well-timed exit and the
+$190 short, i.e. most of the measured edge.

That is one message, not a study. But it is a direct warning against treating the fallback as
equivalent for anything except keeping the service alive.

---

## Self-inflicted problem worth recording

Redeploying social-listener (`--force-recreate`) wiped container `/tmp`, so the replay step of
the Gemini run died on a missing file:

```
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/funding_14d.json'
```

This is the third time this week that container `/tmp` losing backtest inputs has cost time —
the 62-day run lost 90 720 OHLCV bars the same way. `scripts/fetch_backtest_data.py` makes
re-fetching cheap (~2 min, free), but the files must be re-copied after **any** redeploy of the
service that reads them.

---

## Files

- `social-listener/app/config.py` — `extractor_fallbacks`, `provider_keys`
- `social-listener/app/config_secrets.py` — load all keys per provider
- `social-listener/app/extractor.py` — `_attempts()`, per-attempt LLM cache, per-provider image
  block, truthful `model` field
- `scripts/cross_model_extractions.py` — Gemini vs Claude verdict diff

## To finish the Gemini comparison

Gemini's free-tier counter resets daily. Either wait for the reset and re-run, or remove the cap
by adding credit:

```bash
docker cp scripts/fetch_backtest_data.py "$(docker compose ps -q strategy-tester)":/tmp/
docker compose exec -T strategy-tester python /tmp/fetch_backtest_data.py 14 \
    /tmp/ohlcv_14d.json /tmp/funding_14d.json
docker cp "$(docker compose ps -q strategy-tester)":/tmp/ohlcv_14d.json /tmp/
docker cp "$(docker compose ps -q strategy-tester)":/tmp/funding_14d.json /tmp/
docker cp /tmp/ohlcv_14d.json  "$(docker compose ps -q social-listener)":/tmp/
docker cp /tmp/funding_14d.json "$(docker compose ps -q social-listener)":/tmp/

docker compose exec -T -e EXTRACTOR_PROVIDER=google -e EXTRACTOR_MODEL=gemini-3.6-flash \
    -e EXTRACTOR_FALLBACKS= social-listener \
    python -u -m app.backtest_extract 14 /tmp/v2_gemini.json --no-cache --concurrency 4
docker compose exec -T social-listener python -m app.backtest_replay \
    /tmp/ohlcv_14d.json 14 --v2 /tmp/v2_gemini.json --funding /tmp/funding_14d.json --v2-only
```

`--no-cache` is required: the cache key is `(message, extractor_version)` and both models write
`v2`, so a cached run would silently mix Claude and Gemini extractions. **That collision is a
latent trap** — if cross-model comparison becomes routine, the cache key needs the model in it.
