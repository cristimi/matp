# The missed short: direction inversion in the extractor, and a merge window too short to help

**Date:** 2026-07-26
**Branch:** none — landed straight on `main` per CLAUDE.md.
**Follows:** `.gemini/reports/260726_social_burst_merge.md`

---

## Correction to the previous report

An earlier note said *"13 messages reached the strategy — all of those skipped"*. That was
taken from the most recent 200 rows, not the full history, and it was wrong. Over everything:

```
$ SELECT count(*) ingested,
         count(*) FILTER (WHERE is_actionable) actionable,
         count(*) FILTER (WHERE is_actionable AND s.channel_msg_id IS NOT NULL) reached_brain,
         count(*) FILTER (WHERE is_actionable AND s.channel_msg_id IS NULL)     never_evaluated
    FROM social_signal_log l LEFT JOIN LATERAL (…) s ON true;

 ingested | actionable | reached_brain | never_evaluated
      264 |         19 |            19 |               0
```

**Nothing was lost between extraction and the gate**, and the gate has acted 4 times:

```
$ SELECT decision, reason, count(*) FROM social_shadow_orders GROUP BY 1,2 ORDER BY 3 DESC;
 skipped | no_state_change  | 11
 skipped | not_whitelisted  |  3
 acted   | priceless_market |  2
 acted   | ok               |  1
 acted   | backfill_replay  |  1
 skipped | low_confidence   |  1
```

The 11 `no_state_change` skips are all `OPEN`/`FLIP` toward a side we were already on — the
author restating an open position. Skipping those is correct; it is what stops us re-entering
a position we already hold.

---

## The actual failure

On **2026-07-23 19:38:29** the author posted his trade card:

```
➡️Entry: 66.2k
➡️Risk off the trade: feel free to thank me
➡️Lock in W 64.8k: celebrations
➡️TP 2 to be revealed: more celebrations
➡️Lock in big W: large celebrations.
```

Entry 66.2k, profits at 64.8k and below — a **short**. The extractor recorded
`OPEN / BTC / LONG`, confidence 0.52, reasoning:

> "Direction is inferred as LONG since a risk-off level (64.8k) is below the entry, implying
> a stop-loss below entry."

It read **"Lock in W"** — lock in the *win* — as a stop-loss, and inverted the trade.

The gate then did the right thing with the wrong input: target `LONG`, current state `LONG`,
so `no_state_change`, skipped. Read correctly it would have been `FLIP LONG→SHORT`, which
`_SIGNAL[("LONG","SHORT")]` maps to `flip_to_short` — a trade.

The correct reading was 34 seconds earlier in msg 318 (`"$btc shorts, weekly open reached,
poor lows cleared"`), which the extractor classified as a recap. At the 15-second merge
window those two stayed in separate bursts, so the card was judged with no idea which way the
trade went.

**This is why the account is flat while the author is short from 66k.**

---

## Fix 1 — direction from a trade card

Added to `SYSTEM_PROMPT` in `extractor.py`. Written to generalise, not to pattern-match this
one post:

- Work the direction out from where the **profit** levels sit relative to the entry: profits
  below entry → SHORT, above → LONG.
- Use the wording to tell a profit from a stop. `TP` / `target` / `lock in W` / `celebrations`
  mark profit; `SL` / `stop` / `invalidation` / `risk off` mark the stop.
- **A level below the entry is not automatically a stop-loss** — on a short the profit sits
  below the entry. Identify which levels are profits first, then compare.
- Ambiguous and no side stated → leave direction null and lower confidence rather than guess.

## Fix 2 — merge window 15s → 60s

The disambiguating context was 34 seconds away. 15s could never have caught it. 60s is still
far inside `max_signal_age_seconds` (900), at the cost of up to a minute of latency before a
signal is evaluated.

---

## Replay against the stored messages

Text-only — image bytes are not persisted, only `image_sha` — run in a throwaway container
off the service image with the repo mounted.

### Single-message replay

```
[325] THE MISS — trade card, was read OPEN/LONG, is a short
   was: ACT OPEN  BTC  LONG  conf=0.52
   now: ACT OPEN  BTC  SHORT conf=0.72  <-- CHANGED

[318] same run, 34s earlier, names the side ('$btc shorts')
   was: ·   NONE  BTC  SHORT conf=0.35
   now: ·   NONE  BTC  SHORT conf=0.35

[328] recap, must stay NONE          was/now: ·  NONE BTC SHORT 0.30 -> 0.30
[330] recap, must stay NONE          was/now: ·  NONE BTC SHORT 0.35 -> 0.20
[346] recap, must stay NONE          was/now: ·  NONE BTC SHORT 0.85 -> 0.40

[336] the same card reposted 24 Jul
   was: ·   NONE  BTC  -     conf=0.35
   now: ACT OPEN  BTC  SHORT conf=0.82  <-- CHANGED

[77]  control: acted FLIP LONG  (18 Jun)   ACT FLIP BTC LONG  0.90 -> 0.93
[218] control: acted FLIP SHORT (14 Jul)   ACT FLIP BTC SHORT 0.82 -> 0.82
[255] control: acted CLOSE      (17 Jul)   ACT CLOSE BTC -    0.85 -> 0.82
[271] control: acted FLIP LONG  (17 Jul)   ACT FLIP BTC LONG  0.92 -> 0.85

REGRESSIONS on previously-acted messages: none
```

The direction on the miss is corrected, every message that previously produced a real trade
still produces the same one with the same direction, and the recaps stay `NONE` — two of them
more decisively than before (0.85 → 0.40 drops msg 346 under the 0.5 confidence floor).

### Burst replay — what will actually happen

Judged alone, msg 336 (the card reposted the next day as part of a P&L brag) becomes a false
`OPEN`. In production it is never judged alone. Regrouping the real 23-26 July messages at the
new 60s window:

```
23 Jul: 25 messages -> 6 posts
  burst [315, 316, 317, 318, 319, 325]  spans 49s
    ➡️Entry: 66.2k / Lock in W 64.8k / TP 2 to be revealed …
    verdict: ACT FLIP  BTC  SHORT conf=0.90
    reason : The linked post explicitly states "Above 66k, we flipped our longs into shorts
             loudly and clearly." The native post provides a trade card with Entry at 66.2k,
             and profit levels (Lock in W, TP 2) sitting BELOW the entry (64.8k and lower),
             confirming a SHORT direction. This is a FLIP from long to short.

24-26 Jul: 15 messages -> 5 posts
  burst [328, 330, 327]                  ·  NONE BTC SHORT conf=0.15
  burst [333, 332, 335, 334, 336, 338]   ·  NONE BTC SHORT conf=0.15   <== contains 336
    reason : This post is a P&L brag / TP celebration on an existing trade … The trade card
             (Entry 66.2k, TP levels at 64.8k and 64k) confirms this is a SHORT that was
             already opened previously. No new position change is being made here.
  burst [337]                            ·  NONE -   -     conf=0.99
  burst [341, 343, 340]                  ·  NONE -   -     conf=0.99
  burst [346, 347]                       ·  NONE BTC SHORT conf=0.15
```

Three things this settles:

1. **The missed trade is recovered.** The 23 July burst now reads `FLIP BTC SHORT` at 0.90.
   With the state `LONG` at the time, `_SIGNAL[("LONG","SHORT")] = flip_to_short` — it would
   have traded.
2. **Merging is part of the fix, not a nicety.** Msg 336 alone is a false `OPEN`; merged with
   its P&L context it is correctly `NONE` at 0.15. Without the wider window, fix 1 on its own
   would have introduced a false entry.
3. **Today's duplicate is gone** — msgs 346 and 347 now form one post with one verdict,
   instead of the 0.85/0.15 split pair.

---

## Live

```
$ ./scripts/redeploy.sh social-listener
matp-social-listener-1   social-listener   Up 4 seconds
✓ social-listener redeployed.

$ docker compose logs social-listener --tail 8
… LIVE execution armed: strategy=social-btc-astro (Social BTC (AstronomerZero))
  account=blofin-blofin-demo-v5vr margin/trade=10 leverage=20x isolated
… Telegram connected as 8833405539
… Backfilling last 50 messages from AstronomerZero
… Backfill complete (50 messages, 15 post(s))
… Listening for new messages...

$ SELECT count(*) FROM social_signal_log;
264            -- unchanged: nothing re-extracted, no LLM spend on the redeploy
```

The same 50 messages now group into **15 posts**, down from 19 at the 15-second window and
from 50 before merging existed.

---

## Still open

- **The replay is text-only.** Rows whose verdict leaned on the chart image could not be
  re-run faithfully; `image_sha` is stored but the bytes are not. Msg 347's burst was judged
  without its chart.
- **`social_position_state` is still FLAT** and was again left alone. The fix means a *future*
  flip will be caught; it does not retroactively put us in the short the author is holding.
  Joining it now is a live-account action, not a data edit.
- **Confidence floor 0.5.** The miss scored 0.52 before and 0.72 now; the merged burst scores
  0.90. Nothing about the floor was changed.
