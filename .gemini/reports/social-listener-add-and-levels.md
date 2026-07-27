# social-listener: scaling in, take-profit levels, and reading amounts off charts

Date: 2026-07-27
Follows: `social-listener-partial-close.md`, `social-listener-stop-management.md`

Three things were asked for. All three are built, deployed and verified.

## 1. ADD — scaling into a position

`ADD` was the last position change still forced non-actionable. It now sizes at
`add_multiple` × **one standard entry** (`margin_per_trade` × leverage), defaulting to
**half** a standard entry when the post gives no amount, as instructed.

Measuring against a standard entry rather than against the position is deliberate: a
post that says nothing about size then gets a predictable amount instead of one that
compounds with whatever the position happens to be.

`_evaluate_add` keeps every gate an entry has — including the `staleness_pct` chase gate
that TRIM is exempt from. An add is the one management action that *increases* exposure,
and adding late into a move that already ran is exactly what that gate exists to stop.

Two ceilings apply. order-listener clamps any single order to one `margin_per_trade`
unit, but knows nothing about the orders before it; `fire_add` adds the cumulative one
(`max_position_multiple`, default 2.0 standard entries) so a run of "adding here" posts
cannot compound without limit.

**A subtlety worth recording.** order-listener injects a guaranteed SL on every open
signal, and `compute_guaranteed_sl` treats a break-even SL as *invalid* — it requires
the SL to sit on the loss side of entry (`strategy_sl > entry_ref` for a short). So an
add would silently revert a de-risked trade to a wide liquidation-safe stop. That is why
"risk off the trade" is now a **standing** instruction rather than a one-shot (below).

No order-listener change was needed: `_apply_position_fill` already tops up the existing
leg and blends the entry price, so an add grows the position instead of creating a
second one.

## 2. Levels from a later post (the picture case)

You are right that the first post is often plain text and the chart with entry/SL/TP
only shows up in a *later* post. Every rule in the extractor scored that later post as a
recap, and levels were dropped twice over — never extracted, and never applicable to a
position we already held.

Both are fixed:

- `take_profit_price` is a new extracted field, with `evaluate_take_profit` guarding it
  (wrong-side, already-crossed, unchanged). It needs no tighten-only rule the way stops
  do: a take-profit only ever closes a trade in profit.
- **Levels now apply from non-actionable posts.** A post whose `action_type` is `NONE`
  but which carries a stop or target is handled on its own path, recorded as
  `levels_only`, and applied to the open position.
- `adjust_stop` became `adjust_levels`, sending **both** legs in one call. This is a
  correctness fix, not tidying: modify-stops is cancel-then-place across *every*
  trigger, so moving the stop without re-sending the take-profit deletes the
  take-profit. `social_position_state.tp_price` exists to remember it.

## 3. Reading the amount — "25%", not half

The trim closed 50% because `size_fraction` came back NULL and took the default. The
trader had said 25%.

**Where that number lives matters.** I checked: we do not truncate anything. Telegram
itself hands us a cut-off description for a linked X post — post 9786's `preview_text`
ends mid-sentence at "for the 4th time in". So the percentage is usually either in text
we never receive, or drawn on the chart. The chart *is* already sent to the model (the
image is downloaded and passed on the live path); the prompt simply never asked for
numbers on it.

v5 now does, explicitly: read price labels, "Entry"/"SL"/"TP" boxes, and amounts such as
"25%", "1/4", "closed 50% here" off the image, and prefer a stated amount over any
guess.

Verified on realistic posts (read-only, nothing written or sent):

```
--- stated percentage, not a guess
    post: 'Took 25% off the short here at 64.4k. Rest runs to TP2.\n\nStop is at entry now.'
    v5: {"action_type": "TRIM", "size_fraction": 0.25, "trigger_price": 64400.0,
         "stop_to_breakeven": true, "confidence": 0.97}

--- the '25%' the default would have got wrong
    post: 'banked a quarter of the position, letting the rest breathe'
    v5: {"action_type": "TRIM", "size_fraction": 0.25, "confidence": 0.72}

--- levels on a post that changes nothing (the later-chart case)
    post: '$btc shorts — 15 hours in, we win (4th time in a row). ... entry was 65.5k,
           stop sits at 65.5k now, first target 64.4k, second target 62k.'
    v5: {"action_type": "STOP", "direction": "SHORT", "stop_to_breakeven": true,
         "take_profit_price": 64400.0, "confidence": 0.88}

--- scaling in, no amount given
    post: 'Adding to the short here. Same thesis, better price.'
    v5: {"action_type": "ADD", "direction": "SHORT", "add_multiple": null}   -> default 0.5

--- scaling in, amount given
    post: 'Adding another full size to the btc short at 65.9k'
    v5: {"action_type": "ADD", "direction": "SHORT", "add_multiple": 1.0, "confidence": 0.97}

--- full exit stays a full exit
    post: 'Closed the whole short. Out. Good trade everyone.'
    v5: {"action_type": "CLOSE", "direction": "SHORT", "confidence": 0.95}
```

**One honest limit.** I could not re-test this against the *actual* chart from post 9786,
because we store `image_sha` but not the image bytes. That is now a backlog item — see
"social-listener does not retain chart images". Until it is done, a vision mis-read
cannot be diagnosed after the fact.

## Standing break-even

"Risk off the trade" is a standing instruction, not a one-shot: after an add blends the
entry price, break-even means a different number. `social_position_state.stop_mode`
records the intent and a 30s watcher re-asserts it against the current entry, skipping
work when the stop already matches.

Verified live by seeding a deliberately stale value (66000) in our own memory while the
exchange held the correct 65385.3:

```
social-listener-1 | app.emitter: levels set sl=65385.3 tp=None -> 200
social-listener-1 | STANDING break-even re-asserted for BTC: 66000.0 -> 65385.3 (entry 65385.3): sl=65385.3 tp=None confirmed

 asset | state |     stop_price     | tp_price | stop_mode
-------+-------+--------------------+----------+-----------
 BTC   | SHORT | 65385.300000000002 |          | breakeven

$ ... /accounts/blofin-blofin-demo-v5vr/trigger-orders/BTC-USDT
[{"oid":"10002953969","tpsl":"sl","triggerPx":"65385.300000000000000000","sz":"1.6"}]
```

The trigger was genuinely replaced — new `oid` (was `10002953452`), same price. The live
position is unchanged in every way that matters; `stop_mode='breakeven'` is now set for
the BTC stance, which is the honest record of the "risk off the trade" instruction
already applied.

## Verification

Migration applied:

```
$ docker compose exec -T postgres psql -U matp -d matp -v ON_ERROR_STOP=1 < db/migrations/068_social_add_and_levels.sql
NOTICE:  Migration 068 verified OK: social add/levels schema present
```

59 checks across three suites, all in the running container:

```
$ docker compose exec -T social-listener python /app/test_add_tp.py   # 26 checks
PASS  unsized add uses the default multiple           got=(... 0.5, 'add_short', 'SHORT')
PASS  default multiple is half a standard entry       got=0.5
PASS  stated full-size add honoured                   got=('add_at_market', 1.0)
PASS  oversized add clamps                            got=1.0
PASS  zero-size add refused                           got=('skipped', 'add_size_zero')
PASS  no position to add to                           got=('skipped', 'no_position_to_add', False)
PASS  add side mismatch refused                       got=('skipped', 'add_side_mismatch')
PASS  stale add refused                               got=('skipped', 'signal_too_old')
PASS  backfill never adds                             got=('skipped', 'backfill_no_add')
PASS  add refused after the move already ran          got=('skipped', 'stale_price')
PASS  add accepted near the cited price               got=('acted', 'add_at_market')
PASS  add maps to an open signal on the wire          got=('open_short', 'sell', False)
PASS  tp below mark on a short accepted               got=(62000.0, 'ok')
PASS  tp above entry on a short refused               got=(None, 'tp_wrong_side')
PASS  tp already past the mark refused                got=(None, 'tp_already_crossed')
PASS  unchanged tp is not resent                      got=(None, 'tp_unchanged')
PASS  no position, no tp                              got=(None, 'no_position_for_tp')
PASS  stale post sets no tp                           got=(None, 'signal_too_old')
   (plus long-side mirrors and payload-shape checks)
all checks passed

$ docker compose exec -T social-listener python /app/test_stop.py     # 18 checks — all checks passed
$ docker compose exec -T social-listener python /app/test_trim.py     # 23 checks — all checks passed
```

Service healthy after redeploy, backfill clean, no errors:

```
social-listener-1 | LIVE execution armed: strategy=social-btc-astro account=blofin-blofin-demo-v5vr
social-listener-1 | Backfill complete (50 messages, 13 post(s))
social-listener-1 | Listening for new messages...
```

## What the listener can now do

Open, flip, fully close, **scale in**, **take partial profit** (immediately or parked at
a named level), **move the stop** (tighten-only), and **set a take-profit** — including
from a later post that only shows the chart. Nothing about the trader's management
workflow is dropped any more.

## Known limits

- **Chart images are not retained**, so vision extractions cannot be re-judged or
  regression-tested after the fact. Backlog item.
- **A widened stop is still silently ignored** (`stop_would_widen_risk`), by design.
- **Break-even means exactly entry**, with no fee buffer, so a stop-out there is a small
  net loss (~0.06% per side).
- **The cumulative exposure ceiling is 2 standard entries.** A third "adding here" post
  is refused with `add_cap_reached` rather than silently sized down to nothing.
