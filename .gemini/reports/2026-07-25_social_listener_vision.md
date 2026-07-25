# Social Listener — read the chart images, not just the text (migration 062)

**Date:** 2026-07-25
**Branch:** main
**Status:** DONE — deployed and verified live

---

## Background

Asked whether the images from the X posts reposted on Telegram could be analysed too,
"because there are the trades residing". They are.

The channel's X reposts arrive as `MessageMediaWebPage` whose `webpage.photo` is a
**Telegram-hosted** `Photo` — downloadable with the existing session, no X API and no
scraping. 52 of the last 300 messages (17.3%) carry one, and they are annotated TradingView
screenshots where the position change is written *on the chart*: msg 9771 is captioned
`Close longs` → `Flipped longs into Shorts` with the entry levels marked. The text-only
extractor scored that message `NONE, conf=0.10`.

A second, separate bug surfaced while checking this: 14 of the 46 rows with an `x_url` had an
empty `preview_text`, yet the description is on the server right now. Cause was a race in
`_preview()` — see the Fixes section.

---

## Changes

- **`db/migrations/062_social_signal_log_image.sql`** — adds `has_image` (bool, default false)
  and `image_sha` to `social_signal_log`, so vision-derived extractions can be told apart on
  review.
- **`social-listener/app/telegram.py`** — `to_record()` is now async and does three things it
  didn't: detects a `WebPagePending` preview and re-fetches the message until it resolves
  (3 attempts, 2s apart) so the X post's text is no longer lost on the live path; downloads
  the preview's chart image (`webpage.photo`, plus natively attached `MessageMediaPhoto`);
  returns `image_bytes` / `has_image` / `image_sha` alongside the text.
- **`social-listener/app/extractor.py`** — `extract()` takes `image_bytes` and sends a
  multimodal `HumanMessage` (text block + base64 image block). Block shape is per-provider:
  Anthropic-native `{"type":"image","source":{...}}` for the configured provider, OpenAI-style
  `image_url` data URI for the openai/google/groq branches. `include_raw=True` and the
  existing token accounting are untouched. Prompt gains chart-reading rules and, importantly,
  a **retrospective-annotation rule** — a marker on an earlier candle explaining a trade
  already taken is a recap, not a new call. New `evidence` field (`text`/`image`/`both`/`none`)
  records where the signal came from. `EXTRACTOR_VERSION` → `v2`.
- **`social-listener/app/config.py`** — `vision_enabled`, `image_max_bytes` (4MB),
  `image_media_type`, `webpage_resolve_attempts`, `webpage_resolve_delay_seconds`.
- **`social-listener/app/db.py`** — `insert_signal()` persists the two new columns.
- **`social-listener/app/main.py`** — awaits `to_record()` and passes the image through.
  The `already_seen()` check now runs *before* `to_record()`, so replayed messages no longer
  re-download the image (previously the base record was built and thrown away).

---

## Verification

Migration:
```
$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/062_social_signal_log_image.sql
BEGIN
ALTER TABLE
COMMIT
NOTICE:  Migration 062 verified OK: image columns present on social_signal_log
DO
```

Redeploy — clean startup, backfill replays without re-extracting:
```
$ ./scripts/redeploy.sh social-listener
✓ social-listener redeployed.

2026-07-25 14:21:14,860 INFO app.db DB pool initialized
2026-07-25 14:21:15,381 INFO social-listener Telegram connected as 8833405539
2026-07-25 14:21:15,427 INFO social-listener Backfilling last 50 messages from AstronomerZero
2026-07-25 14:21:15,852 INFO social-listener Backfill complete (50 messages)
2026-07-25 14:21:15,852 INFO social-listener Listening for new messages...
```

### The two real chart messages, image vs text-only

```
=== msg 9771 ===
preview_text: 'Astronomer (@astronomer_zero) on X\n$btc shorts\n\n64k has arrived ✅, I took profits here\n\nAl'
has_image: True | bytes: 143424 | sha: d5965c730117
WITH IMAGE : {'is_actionable': False, 'action_type': 'NONE', 'asset': 'BTC', 'direction': 'SHORT',
              'reference_price': 64000.0, 'confidence': 0.85, 'total_tokens': 3466}
            evidence: both | The post is a P&L recap/brag: "trade up 4 RR", "I took profits here",
            "countertrend shorts from above 66k are truly stretching out now." The chart ann…
TEXT ONLY  : {…, 'total_tokens': 1838}

=== msg 9766 ===
has_image: True | bytes: 119114 | sha: 6a12b11be399
WITH IMAGE : {'is_actionable': False, 'action_type': 'NONE', 'asset': 'BTC', 'direction': 'SHORT',
              'reference_price': 66000.0, 'confidence': 0.15, 'total_tokens': 3493}
            evidence: both | The post is explicitly a retrospective recap/explanation post —
            "Why we countertrend shorted above 66k" …
```

Both correctly stay non-actionable — they are recaps. On 9771 the model **did** read the
`Flipped longs into Shorts` annotation and classified it as retrospective, which is exactly
the intended behaviour: the flip is marked at ~66.3k while the chart's last price is 64.1k.

### A/B proving the image is what produces the signal

Same synthetic text (`"Taking this one right now. Executed."`), with and without 9771's chart:

```
TEXT ONLY : {'is_actionable': False, 'action_type': 'NONE', 'asset': None,  'direction': None,
             'reference_price': None,    'confidence': 0.1}
TEXT+IMAGE: {'is_actionable': True,  'action_type': 'FLIP', 'asset': 'BTC', 'direction': 'SHORT',
             'reference_price': 64125.5, 'confidence': 0.88}  evidence= both
```

Asset, direction and price all come from the chart — the text carries none of them.

### Full pipeline → DB (synthetic id, cleaned up after)

```
2026-07-25 14:23:52,108 INFO social-listener msg 999999902 [·] NONE BTC ref=64000.0 conf=0.85 img=y

 channel_msg_id | is_actionable | action_type | asset | has_image |     sha      | extractor_version | total_tokens
----------------+---------------+-------------+-------+-----------+--------------+-------------------+--------------
      999999902 | f             | NONE        | BTC   | t         | d5965c730117 | v2                |         3467
(1 row)

$ … DELETE FROM social_signal_log WHERE channel_msg_id=999999902;
DELETE 1
```

### `WebPagePending` retry (stubbed transition — can't force a live pending preview)

```
pending detected: True
re-fetches: 2 | preview_text: 'Astronomer on X\n$btc shorts, flipped here'
x_url: https://x.com/a/1
gave up after: 3 attempts | preview_text: '' | x_url from regex: https://x.com/a/1
```

Detects pending, re-fetches until resolved (recovering text that was previously dropped), and
gives up after 3 attempts falling back to the regex URL rather than hanging.

---

## Cost

~1.6k extra input tokens on a message with an image (3466 vs 1838 measured on 9771). At the
observed 17.3% image rate that's roughly **+280 tokens per message on average**, against the
current ~1.4k baseline.

---

## Still open (unchanged by this work)

- `execution_mode` is still `shadow`; nothing routes social signals to order-generator.
- No dashboard-api endpoint and no UI — `social_signal_log` / `social_shadow_orders` are
  DB-only.
- **The `priceless_market` path matters more now.** 2 of the 4 shadow `acted` decisions fired
  with `reason=priceless_market`, i.e. `marketdata.get_mark()` returned nothing and the 1%
  staleness gate was bypassed by `entry_on_missing_price`. Chart annotations are frequently
  retrospective, so that gate is the main thing standing between an image-derived signal and a
  stale entry. Worth fixing before `execution_mode=live`.
