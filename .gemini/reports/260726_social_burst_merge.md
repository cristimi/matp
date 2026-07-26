# One post, one judgement: merging Telegram bursts in the social listener

**Date:** 2026-07-26
**Branch:** none — landed straight on `main` per CLAUDE.md.
**Follows:** `.gemini/reports/260726_social_signals_in_ai_log.md`, which surfaced the problem.

---

## The problem

It looked like the listener was evaluating one post twice. It was not — **Astronomer posted
two separate Telegram messages one second apart**, and the listener correctly processed each:

```
9778  12:08:23  "Confluence 5 shared as to why we shorted above 66k, as promised"
                 → NONE, confidence 0.85, evidence "text",  1740 tokens
9779  12:08:24  https://x.com/astronomer_zero/status/2081350739432276029
                 (preview: the full article + annotated chart)
                 → NONE, confidence 0.15, evidence "both",  3438 tokens
```

One human post, two LLM calls, two verdicts, 5,178 tokens. Harmless here because both said
`NONE`, but on a live path (`execution_mode=live`, strategy `social-btc-astro`) an actionable
post would produce **two independent decisions for one intent**.

The 0.85-vs-0.15 confidence split is a symptom of the same thing: the model saw half the post
in one call and the other half in the other.

---

## The fix: merge the burst, extract once

`handle()` now takes a *list* of messages — the burst that makes up one post — instead of a
single message. Messages arriving within `merge_window_seconds` of each other are folded into
one record before extraction.

### Merging rules (`telegram.merge_records`)

| field | rule | why |
|---|---|---|
| `channel_msg_id` | **highest** id in the burst | `max_channel_msg_id` then advances past every part, so the catchup loop will not re-fetch the earlier ones |
| `posted_at` | **earliest** | that is when the human posted; the staleness gate measures against it |
| `raw_text` | all non-empty texts joined, id order | the comment and the link both carry meaning |
| `preview_text` | first that resolved | the previews are near-identical copies; concatenating them just burns tokens |
| `x_url`, image | first present | the chart survives into the merged record |
| `merged_msg_ids` | every id, ascending | audit trail — see migration 064 |

### Burst detection (`main.group_bursts`)

A new burst starts when the gap to the previous message exceeds the window, **or** when the
current burst hits `merge_max_messages` (6). The cap stops a busy stretch of unrelated posts
being welded into one giant prompt.

### Live path (`main._LiveBuffer`)

The live handler no longer processes inline — the follow-up message that completes the post
has not arrived yet. It buffers and schedules a flush; each new message cancels and
reschedules. At the cap it flushes immediately rather than waiting for quiet.

Backfill and the catchup loop both group their message runs through the same
`group_bursts` before handling, so a recovered gap is judged the same way a live burst is.

### The cost, stated plainly

**Every live signal is now delayed by up to `merge_window_seconds` (15s)**, because a burst is
only known to be complete once the window has passed with nothing new. That is the price of
one verdict per post. It is configurable and sits well inside the state machine's
`max_signal_age_seconds` (900).

---

## Migration 064 — `social_signal_log.merged_msg_ids bigint[]`

Without it a merged row would look like a single message and there would be no way to audit
which ids a verdict covered. Additive and nullable; existing rows stay NULL, which reads as
"one message, the one in `channel_msg_id`".

```
$ docker compose exec -T postgres psql -U matp -d matp -f db/migrations/064_social_merged_msg_ids.sql
ALTER TABLE
COMMENT
DO
NOTICE:  Migration 064 verified OK: social_signal_log.merged_msg_ids present

$ \d social_signal_log
 merged_msg_ids    | bigint[]
```

---

## Verification

### Unit checks against the real 2026-07-26 case

Run in a throwaway container off the service image with the repo mounted:

```
burst: two messages 1s apart  -> 1 post  OK
burst: 60s apart              -> 2 posts OK
burst: cap at 3               -> 3/3/3/1 OK
burst: unordered input sorted  OK
merge : both parts folded into one record  OK
merge : single message passes through      OK

all merge/burst checks passed
```

The merge check asserts exactly the properties above on the real 9778/9779 pair: keyed on
9779, `posted_at` from 9778, the comment first in `raw_text`, the X preview kept, the image
carried through, `merged_msg_ids == [9778, 9779]`.

### Live

```
$ ./scripts/redeploy.sh social-listener
matp-social-listener-1   social-listener   Up 4 seconds
✓ social-listener redeployed.

$ docker compose logs social-listener --tail 30
… LIVE execution armed: strategy=social-btc-astro (Social BTC (AstronomerZero))
  account=blofin-blofin-demo-v5vr margin/trade=10 leverage=20x isolated
… Telegram connected as 8833405539
… Backfilling last 50 messages from AstronomerZero
… Backfill complete (50 messages, 19 post(s))
… Listening for new messages...
```

**50 messages → 19 posts.** The channel's recent history is roughly 2.6 Telegram messages per
actual post, which is the scale of the duplication that was being paid for.

No re-extraction happened, so the redeploy cost nothing in LLM calls:

```
$ SELECT count(*) FROM social_signal_log;
264            -- unchanged across the redeploy

$ SELECT count(*) FILTER (WHERE merged_msg_ids IS NOT NULL), count(*) FROM social_signal_log;
0 | 264        -- no backfilled row was rewritten; new posts will populate it
```

Existing rows are keyed on ids that are already `already_seen`, so the burst loads from the DB
rather than re-calling the model.

### Surfaced in the UI

`/ai/social-signals` returns `merged_msg_ids`; the Social tab shows an "N msgs" chip on merged
rows and lists the ids as `9778 + 9779` in the expanded detail.

---

## Deliberately NOT done: correcting the position state

Reported alongside this: *"the current position of astro is short from 66000"*, while
`social_position_state` says:

```
 telegram:AstronomerZero | BTC | FLAT | last_msg_id 9716 | updated 2026-07-26 09:59:04
```

**FLAT is correct and was left alone.** That column tracks **our own mirrored position, not
Astronomer's**. From `main.py`:

> Fail-closed: a failed emission leaves the state unchanged, so `social_position_state` never
> claims a position the exchange does not hold. The cost of that choice is a missed trade,
> which is the safe direction to be wrong in.

We are flat because we never joined his short — every post about it was a recap, correctly
judged `NONE`. Editing the row to `SHORT` would break that invariant: the next `CLOSE` he
posts would emit a **real close order on Blofin for a position we do not hold**, since
execution is live on `social-btc-astro` (blofin-demo, $10/trade, 20x isolated).

If mirroring his current short is wanted, the correct route is to open it through the strategy
so the exchange and the state agree — a live-account action, not a data edit, and not taken
without an explicit instruction.

---

## Still open

- **Vision confidence.** 0.15 with the chart versus 0.85 on text alone for the same post.
  Merging removes the split-brain cause, but it is worth watching whether the merged
  (text + image) call now scores like the text one. Nothing here changes the extractor.
- **`merge_window_seconds = 15`** is a first guess. The observed burst was 1 second; if posts
  routinely arrive further apart, it needs raising — at the cost of more latency.
