# Social listener extractions in the AI signal log

**Date:** 2026-07-26
**Branch:** none — landed straight on `main` per CLAUDE.md.
**Why:** to be able to see that social messages are being ingested and how each was read.

---

## 1. The question asked first: how were the last 3 hours from Astronomer read?

One X post, logged **twice** — once as the Telegram text, once as the X link preview carrying
the chart image, one second apart.

```
$ docker compose exec -T postgres psql -U matp -d matp -x -c \
  "SELECT … FROM social_signal_log WHERE posted_at > now() - interval '3 hours' ORDER BY posted_at DESC;"

id 347 | posted 2026-07-26 12:08:24+00 | ingested 12:09:48
  is_actionable f | action_type NONE | BTC SHORT | ref 66000 | confidence 0.15
  in_whitelist t  | anthropic:claude-sonnet-4-6 | v2 | has_image t
  raw_text     https://x.com/astronomer_zero/status/2081350739432276029
  preview_text Astronomer (@astronomer_zero) on X / $btc / Why we countertrend shorted
               above 66k - confluence 5 - the sentiment. …

id 346 | posted 2026-07-26 12:08:23+00 | ingested 12:09:39
  is_actionable f | action_type NONE | BTC SHORT | ref 66000 | confidence 0.85
  in_whitelist t  | anthropic:claude-sonnet-4-6 | v2 | has_image f
  raw_text     Confluence 5 shared as to why we shorted above 66k, as promised
```

The model's own reasoning on each:

```
347 (text + image, confidence 0.15)
  "The post is a retrospective explanation ("Why we countertrend shorted above 66k")
   describing the sentiment confluence that motivated a past short entry above 66k. The
   chart annotations … are illustrative of market sentiment at historical price levels,
   not a new position call. The current price on the chart (~64,539) is well below the
   annotated short entry zone (~66k+), confirming this is a recap/explanation of an
   already-taken trade, not a new actionable position change."

346 (text only, confidence 0.85)
  "The post is a retrospective recap explaining the reasoning ("confluence") behind a
   short that was already taken above 66k. The phrase "as promised" and "why we shorted"
   confirm this is a look-back explanation of an existing/past trade, not a new position
   change being announced now. No new entry or exit is stated."
```

**Verdict: read correctly.** Both `NONE` / not actionable. Nothing traded — no shadow order
was written for either (the most recent is from 23 July), and the position state is unchanged:

```
$ SELECT * FROM social_position_state;
 telegram:AstronomerZero | BTC | FLAT | last_msg_id 9716 | updated 2026-07-26 09:59:04
```

### Two things worth deciding on later

1. **The vision path was far less certain.** Same post, same conclusion, but 0.15 confidence
   with the image versus 0.85 on text alone. Not harmful here — both said NONE — but if
   confidence ever gates behaviour, the image path will behave very differently.
2. **One post yields two independent evaluations.** The Telegram text and the X preview are
   separate `social_signal_log` rows judged separately. Harmless when both say NONE; an
   actionable post would produce two verdicts for one human intent.

Neither was changed here — flagging only.

---

## 2. What the pipeline looks like overall

Now visible through the new endpoint:

```
total ingested messages : 264
judged actionable       : 19
reached the strategy    : 13 (of the most recent 200)
```

Every one of those 13 was **skipped**, not acted:

```
 325  2026-07-23T19:38  OPEN  BTC  -> skipped / no_state_change   | LONG -> LONG | none
 294  2026-07-18T23:58  OPEN  BTC  -> skipped / no_state_change   | LONG -> LONG | none
 285  2026-07-17T22:44  FLIP  —    -> skipped / not_whitelisted   | FLAT -> FLAT | none
 277  2026-07-17T14:14  OPEN  BTC  -> skipped / no_state_change   | LONG -> LONG | none
```

Ingestion and extraction are working; it is the state-change gate that filters nearly
everything — the posts mostly restate a position already held.

---

## 3. `GET /ai/social-signals`

New endpoint on the existing `ai` router. Returns `social_signal_log` rows with the model's
reasoning lifted out of `raw_llm_json`, joined to whatever the strategy decided.

Design notes:

- **Not merged into `/ai/signals`.** `ai_signal_log` and `social_signal_log` have almost no
  columns in common — gate/webhook/tokens/confidence-threshold on one side, action_type /
  whitelist / evidence on the other. A union would have to null out most of both and the
  page's filters would apply to neither cleanly. Two views, one page.
- **The shadow-order join is a `LATERAL … LIMIT 1`,** not a plain join on
  `(source, channel_msg_id)`. Today an evaluated message has at most one decision row, but a
  plain join would silently multiply log rows if that ever stopped holding.
- Filters: `source`, `actionable`, plus `limit`/`offset` matching the existing list route.

### Verification

```
$ …/ai/social-signals?limit=3
{
  "signals": [
    {
      "id": "347", "source": "telegram:AstronomerZero", "channel_msg_id": "9779",
      "posted_at": "2026-07-26T12:08:24.000Z", "ingested_at": "2026-07-26T12:09:48.015Z",
      "is_actionable": false, "action_type": "NONE", "asset": "BTC", "direction": "SHORT",
      "reference_price": 66000, "confidence": 0.15, "in_whitelist": true,
      "model": "anthropic:claude-sonnet-4-6", "extractor_version": "v2", "has_image": true,
      "input_tokens": 3157, "output_tokens": 281, "total_tokens": 3438,
      "reasoning": "The post is a retrospective explanation …",
      "evidence": "both",
      "decision": null, "decision_reason": null, …
    },
    { "id": "346", …, "confidence": 0.85, "has_image": false, "evidence": "text", … }
  ]
}

$ …/ai/social-signals?limit=200
total: 264 | returned: 200
rows with a strategy decision: 13
  325 2026-07-23T19:38 OPEN BTC -> skipped / no_state_change | state LONG -> LONG
  294 2026-07-18T23:58 OPEN BTC -> skipped / no_state_change | state LONG -> LONG
  285 2026-07-17T22:44 FLIP None -> skipped / not_whitelisted | state FLAT -> FLAT
  277 2026-07-17T14:14 OPEN BTC -> skipped / no_state_change | state LONG -> LONG

$ …/ai/social-signals?actionable=true&limit=3
total actionable: 19 | ids: ['325', '294', '285']
```

---

## 4. The Social tab

`AiSignalLog.tsx` gained a source tab: **AI cycles** | **Social**.

Each social card shows, collapsed: who posted, the verdict chip (`OPEN`/`FLIP`/`CLOSE`/`ADD`/
`TRIM`/`NONE`), asset + direction, actionable or not, the confidence bar, an `image` chip when
the extractor also saw the chart, a `not whitelisted` warning, what the strategy did with it,
and the message text on one line.

Expanded, it adds the model's reasoning in full, posted/ingested times, reference and mark
price, the state transition, evidence (`text` / `image` / `both`), extractor model and version,
token spend, the Telegram message id, and a link to the X post.

The AI-cycle filters and the token-usage rollup are hidden on the Social tab rather than
shown-but-ignored: they key off `ai_signal_log` columns the social table has no equivalent
for.

### Verification

```
$ cd dashboard-api && npx tsc --noEmit
api tsc exit: 0

$ cd dashboard-ui && npx tsc --noEmit
ui tsc exit: 0

$ ./scripts/redeploy.sh dashboard-api
matp-dashboard-api-1   dashboard-api   Up 3 seconds (health: starting)
✓ dashboard-api redeployed.
```

**Pending at the time of this commit:** the `dashboard-ui` image build was still running
(builds are taking 20-30 minutes at the current host load, ~35). The endpoint it calls is
deployed and verified above, and the page type-checks clean. The served-bundle check is added
in the follow-up commit below once the build lands.

---

## Not done

- Nothing was changed about the duplicate-evaluation or vision-confidence observations in
  section 1 — they are reported, not fixed.
- The Social tab has no filters of its own yet; the endpoint supports `source` and
  `actionable` if they turn out to be wanted.
