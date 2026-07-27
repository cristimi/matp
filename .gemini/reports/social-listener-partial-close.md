# social-listener: partial profit-taking (TRIM) built end to end

Date: 2026-07-27
Follows: `.gemini/reports/social-listener-partial-tp-not-taken.md`

## What was wrong

The listener could open, flip and fully close, and nothing else. A post that managed
an open trade was blocked in three separate places:

- `extractor.py` forced `is_actionable=False` for ADD and TRIM.
- `statemachine._target_state()` returned `None` for anything but OPEN/FLIP/CLOSE, so
  `evaluate()` answered `skip("no_target")`.
- `emitter._STEPS` mapped only the six full-position signals.

Live consequence on 2026-07-27: msg 9787-9790 ("Entry: 65.5k / Risk off the trade /
Lock in W 64.4k") was read as a **new OPEN** at 65.5k, dropped as `no_state_change`
because the stance was already SHORT, and the partial profit was never taken.

## What was built

**Extractor v3** (`extractor.py`). TRIM is actionable. Two new fields, `size_fraction`
(how much comes off) and `trigger_price` (the level the trader names), both forced to
NULL on any non-TRIM verdict so a stray value can never reach the sizing path. The
prompt gains a TRIM-vs-CLOSE rule, a TRIM-vs-recap rule, and a management-card rule: a
card restating an entry we already hold is management, not a fresh call. **ADD stays
non-actionable** — sizing a scale-in needs a margin decision nothing downstream
expresses.

**State machine** (`statemachine.py`). `evaluate()` now returns `advance` (the stance
moves) and `emit` (orders go out) as separate flags, because a trim emits without
advancing — the three states FLAT/LONG/SHORT cannot express "less of the same".
`_evaluate_trim` is deliberately exempt from the `staleness_pct` chase gate: that gate
stops us chasing an entry that ran away, and refusing to reduce exposure because the
price moved is the wrong failure direction (the same reasoning `partial_close` already
gets in the AI engine's cooldown grouping). The age backstop still applies. The
fraction is clamped to `[0.1, 0.9]`, so a trim can never round up into a full close the
state machine would not record.

**Emitter** (`emitter.py`). A partial sends `close_long`/`close_short` with an explicit
size and **no** `target_position` — that is order-listener's existing partial-reduce
path (`webhook_handler.py:1581`, `close_size` clamped to its own open size inside
`close_strategy_position`). No order-listener or order-executor change was needed. The
quantity comes from `strategy_positions`, the same row order-listener clamps against,
so a stale read can only ever under-close.

**Parked trims** (migration 066, new table `social_pending_trims`). A trim pinned to a
price the market has not reached is parked rather than taken at a worse price. A 30s
watcher fires it when the mark crosses, cancels it when the stance leaves that side,
and expires it after 48h.

## Verification

Migration applied to the live DB:

```
$ docker compose exec -T postgres psql -U matp -d matp -v ON_ERROR_STOP=1 < db/migrations/066_social_partial_close.sql
ALTER TABLE
COMMENT
COMMENT
ALTER TABLE
COMMENT
COMMENT
ALTER TABLE
ALTER TABLE
CREATE TABLE
COMMENT
CREATE INDEX
NOTICE:  Migration 066 verified OK: social partial-close schema present
DO
```

23 state-machine and payload checks, run inside the running container:

```
$ docker compose exec -T social-listener python /app/test_trim.py
PASS  msg 9790 parks below-market trim level
PASS  fires once mark crosses the level
PASS  levelless trim acts at market
PASS  trim leaves stance untouched
PASS  long trim parks below its level
PASS  long trim fires above its level
PASS  no position to trim
PASS  side mismatch refused
PASS  stale trim refused
PASS  backfill never trims
PASS  low confidence refused
PASS  off-whitelist refused
PASS  fraction 1.0 clamps to max
PASS  fraction 0.0 clamps to min
PASS  missing fraction uses default
PASS  junk fraction uses default
PASS  open still acts and advances
PASS  open on an existing side still no-ops
PASS  partial sends close_short with a size and NO target_position
PASS  full close still state-syncs to flat
PASS  partial steps registered
PASS  partial sides
PASS  partial maps to a single step

all checks passed
```

The watcher, exercised against the live service with two seeded rows (a LONG park while
the stance is SHORT, and one already past its TTL):

```
social-listener-1 | 2026-07-27 15:07:35,986 INFO social-listener pending trims: 1 expired unreached
social-listener-1 | 2026-07-27 15:07:36,009 INFO social-listener parked trim msg -1 cancelled: stance left LONG

 channel_msg_id | side  |  status   |      resolution       |          resolved_at
----------------+-------+-----------+-----------------------+-------------------------------
             -1 | LONG  | cancelled | stance no longer LONG | 2026-07-27 15:07:36.002245+00
             -2 | SHORT | expired   | ttl elapsed           | 2026-07-27 15:07:35.971818+00
```

(Both rows deleted afterwards; `social_pending_trims` is back to 0.)

Position lookup used for trim sizing, read from the live DB:

```
open_position(BTC): {'symbol': 'BTC-USDT', 'side': 'short',
                     'size': Decimal('0.003100000000000000000'),
                     'entry_price': Decimal('65385.3')}
state: SHORT
```

## The original message, re-run through the new pipeline

Dry run — nothing written, nothing sent:

```
POST 9790  posted_at=2026-07-27 14:41:02+00:00  has_image=False
------------------------------------------------------------------------
➡️Entry: 65.5k
➡️Risk off the trade: feel free to thank me
➡️Lock in W 64.4k: celebrations
➡️TP 2...
....
You know what to do, etc etc, too lazy to type
counting my cash
good night
------------------------------------------------------------------------
OLD verdict (v2): OPEN SHORT ref=65500 conf=0.72

NEW verdict (v3, anthropic:claude-sonnet-4-6):
{
  "is_actionable": true,
  "action_type": "TRIM",
  "asset": "BTC",
  "direction": "SHORT",
  "reference_price": 65500.0,
  "size_fraction": null,
  "trigger_price": 64400.0,
  "confidence": 0.55,
  "reasoning": "The card lists Entry: 65.5k with a profit level (\"Lock in W\") at 64.4k
                — below the entry — confirming this is a SHORT. \"Lock in W 64.4k:
                celebrations\" is a named trigger price for taking partial profit (TRIM),
                leaving TP2 still open. The post does not say the trade is fully closed,
                so CLOSE is ruled out. No size fraction is given. ..."
}

recorded stance : SHORT
live position   : short 0.003100000000000000000 @ 65385.3
mark now        : 64731.5

DECISION:
  decision         = pending
  reason           = trim_level_pending
  intended_signal  = partial_close_short
  to_state         = SHORT
  advance          = False
  emit             = False
  park             = True
  is_trim          = True
  size_fraction    = 0.5
  trigger_price    = 64400.0

  -> would close 0.00155 BTC of 0.003100000000000000000 (50%), leaving 0.00155 running
  -> parked until mark <= 64400.0 (now 64731.5, 331.5 away)
```

So the same post that produced nothing this morning now produces: take half the short
off at 64400, leave the rest running for TP2. That is what the trader asked for.

## Two things worth knowing

**1. Confidence 0.55 against a floor of 0.50.** The model is less sure of TRIM than it
was of the (wrong) OPEN, mostly because the post never names BTC. It passes, but with
little room. Worth watching over the next few management cards before deciding whether
the floor or the prompt needs adjusting.

**2. This specific post can no longer be replayed through the normal path.** It was
posted at 14:41; `max_signal_age_seconds` is 900s, so re-processing it now returns
`signal_too_old` — correctly, since the age backstop cannot tell a re-run from a
catchup recovery. The fix applies to every future management post automatically. Making
it manage *this* open trade would mean inserting the parked trim by hand:

```sql
INSERT INTO social_pending_trims
  (source, channel_msg_id, asset, side, size_fraction, trigger_price, expires_at)
VALUES ('telegram:AstronomerZero', 9790, 'BTC', 'SHORT', 0.5, 64400,
        now() + interval '48 hours');
```

Not done — that is a live trading decision, not a deploy step.

## Still not built (in the roadmap backlog)

The other half of the same card, "Risk off the trade" (move the stop to break even), is
still dropped. order-listener already exposes `POST /strategies/{id}/adjust-stops` with
the same webhook token, so it needs no exchange work — see the Deferred Backlog entry,
including the open question of whether a social post should ever be allowed to *widen*
a stop rather than only tighten one.
