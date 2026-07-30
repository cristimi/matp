# Social extractor v6 — a complete chart outranks the prose

**Date:** 2026-07-30
**Trigger:** msg 9801 (`telegram:AstronomerZero`, posted 11:53:27) parked a trim the post
never asked for. Reported by the account owner, who read the chart directly.

Two actions: the bad parked trim was cancelled, then the extractor prompt was fixed and
re-run against the same real post and image.

---

## 1. What v5 produced, and why it was wrong

`social_signal_log` id 358, model `anthropic:claude-sonnet-4-6`, extractor v5, image read
(`image_sha 36e66e78…`, `merged_msg_ids {9797,9798,9799,9800,9801}`):

```json
{"asset": "BTC", "direction": "LONG", "action_type": "TRIM", "is_actionable": true,
 "size_fraction": 0.3, "trigger_price": 67700.0, "reference_price": 67700.0,
 "stop_price": null, "take_profit_price": null, "confidence": 0.82, "evidence": "both"}
```

Its own reasoning names the mistake:

> "The chart … shows 'Long V' as the current live long position, with a **'TP (Close 30%)'
> annotation near the ~64,800 level** — but **the text overrides the chart price with 67.7k
> as the actual trim price**. The chart annotation 'TP (Close 30%)' gives the size fraction
> of 0.30."

So it read the chart correctly, then took the **size** from the chart annotation and the
**price** from a line of the post's poem (*"67.7k, another piece is gone"*), welding two
unrelated things into an instruction that appears nowhere in the post. It also returned
`stop_price: null` despite the card carrying a stop.

Per the account owner, the picture actually shows: entry **63,447**, SL **63,103**, an
unsized trim at **~63,950**, and a 30% take-profit at **64,733**; 67,700 is more likely the
final target.

### Live consequence, now cancelled

The trim was parked (not fired — BTC was 64,820 against a 67,700 trigger) and would have
sat pending until 2026-08-01:

```
 id | channel_msg_id | asset | side | size_fraction | trigger_price |  status   |          resolved_at
----+----------------+-------+------+---------------+---------------+-----------+-------------------------------
  8 |           9801 | BTC   | LONG |           0.3 |         67700 | cancelled | 2026-07-30 12:34:39.949915+00

--- any pending left ---
 (0 rows)
```

---

## 2. The prompt gap

`app/extractor.py` told the model that chart numbers count as stated by the trader, and
that text carries information too — but never said **which wins when they disagree**, and
never said a size and a price must come from the **same** annotation. The model invented a
precedence rule and picked the wrong one.

Three blocks added, and `EXTRACTOR_VERSION` bumped **v5 → v6**:

* **A COMPLETE CHART WINS OVER THE PROSE.** A chart is complete when it labels the entry
  AND at least one stop or target. Those drawn numbers are the trade; prose never overrides
  them. Text is used only for fields the chart leaves unlabelled. On a disagreement, the
  chart's value is taken and the reasoning must say so.
* **Fill the levels the action does not need.** A complete card labels more than the action
  uses — `stop_price` and `take_profit_price` must come back filled regardless of
  `action_type`.
* **NEVER SPLIT AN ANNOTATION.** A size and its price come off the same label. If
  "TP (Close 30%)" sits on 64,733 then `size_fraction=0.30` belongs with
  `trigger_price=64733`. If the size has no price on its own annotation, `trigger_price`
  stays null rather than borrowing one.

---

## 3. Verification — the same post and image, re-extracted

Run through `app.backtest_extract`, which talks to Telegram and the LLM but is read-only
with respect to live tables:

```
INFO:backtest-extract:v6: 7 messages in window, 0 cached (cache disabled), 7 to extract
INFO:backtest-extract:[6/7] msg 9795 FLIP BTC conf=0.92 img=y
INFO:backtest-extract:[7/7] msg 9798 TRIM BTC conf=0.82 img=y
INFO:backtest-extract:wrote /tmp/v6.json — 7 records (0 reused, 7 new), 0 failed, 31596 tokens
```

msg 9798 is the record carrying the chart image **and** the poem text — so the exact
conflict was present:

```
=== msg 9798 ===
  action_type          TRIM
  is_actionable        True
  asset                BTC
  direction            LONG
  reference_price      67700.0
  size_fraction        0.3
  trigger_price        64733.0
  stop_price           63103.6
  take_profit_price    64733.0
  confidence           0.82
  evidence             both
```

Its reasoning shows the new rules being applied:

> "The annotation 'TP (Close 30%)' sits on the 64,733.5 line … The size_fraction=0.30 and
> trigger_price=64,733 come from the same chart annotation 'TP (Close 30%)' on the 64,733
> line — **never split**. … The post text references 67.7k which appears to be a
> poetic/celebratory reference to a prior target or overall level, not the current trim
> price — **the chart's labelled level (64,733) is used** as it is the complete, labelled
> annotation for this trim."

### Field-by-field, against what the picture shows

| field | picture | v5 | v6 |
|---|---|---|---|
| trim price | 64,733 | **67,700** | **64,733** |
| trim size | 30% | 0.30 | 0.30 |
| stop | 63,103 | **null** | **63,103.6** |
| take-profit | 64,733 | **null** | **64,733** |
| 67,700 | final target | used as the trim trigger | correctly set aside |

Live tables untouched by the re-run:

```
 max_signal_id      -> 358   (unchanged, no new signal rows)
 pending trims      -> cancelled 1, fired 3, pending 0
 extraction cache   -> v2 131 rows only (no-cache run wrote nothing)
```

---

## Limits of this verification — read before trusting it

* **I never saw the image.** Every "correct" judgement above compares the model's output to
  the account owner's description of the chart. I cannot independently confirm 63,103 or
  64,733 are what is drawn.
* **`reference_price` is still 67,700.** The trading path keys off `trigger_price`, so this
  does not affect behaviour, but the field is meant to hold the price the trader cites and
  it is still carrying the poem's number rather than the entry (~63,447/63,483). Not fixed.
* **The backtest extracts messages one at a time; the live path merges bursts.** msg 9798
  happened to carry both the image and the poem, so the conflict really was exercised — but
  this is not a byte-identical replay of the merged 5-message post that produced id 358.
* **One annotation is read differently from the owner's account.** They describe an unsized
  trim at ~63,950; v6's reasoning places an older trim annotation at ~65,000 and assigns it
  to a previous trade. I cannot adjudicate that without the image.
* **Only one post was checked.** A prompt change of this kind can shift behaviour on other
  posts. The other 6 messages in the window came back NONE/FLIP as before, but that is a
  thin sample — worth a wider backtest window before trusting v6 broadly.
