# The social strategy can hold two positions in one coin

**Date:** 2026-08-02
**Services touched:** `social-listener`, `order-listener`, DB migration 074
**Status:** shipped and unit-tested. **Off in production** — it turns itself on only when
the execution account is in BloFin hedge mode, and that account is still `net`.

Follow-on from `2026-08-02_blofin_hedge_mode.md`, which gave the exchange layer the ability
to hold a long and a short at once. That report's closing limitation was that the
AstronomerZero strategy still could not use it. This removes that limitation.

---

## 1. What was wrong

`social-listener` modelled the channel as **one stance per asset** — `FLAT | LONG | SHORT`,
with `LONG -> SHORT` expressed as a flip. That is a faithful model of a net-mode exchange
account and an unfaithful model of the trader, who holds both sides of a coin when he wants
to. Every "which side are we on" question in the service was a string comparison against
that single stance, and with two legs none of them had a single right answer any more.

## 2. What changed

### DB — migration 074
`social_position_state` is now keyed on **(source, asset, side)** with `state` in
`OPEN | FLAT`, instead of (source, asset) with `state` in `FLAT | LONG | SHORT`. Each leg
carries its own `stop_price` / `tp_price` / `stop_mode` / `last_msg_id` — the point being
that a long's break-even and a short's are different numbers, and one row could only hold
one of them. A missing row is a flat leg, exactly as before.

```
NOTICE:  Migration 074 verified OK: 1 open leg(s) carried over

         source          | asset | side  | state | last_msg_id
-------------------------+-------+-------+-------+-------------
 telegram:AstronomerZero | BTC   | SHORT | OPEN  |        9806
```

### `app/legs.py` — new
A small `Legs` value type: which of the two legs are open, plus `sole_open()`, which is what
decides whether a post naming no side is actionable or must be refused. `label()` spells a
single leg exactly as the old stance did (`LONG`, `SHORT`, `FLAT`), so historical audit rows
and new ones read alike; both legs is `LONG+SHORT`.

### The state machine
| Post | Net account (unchanged) | Hedge account |
|---|---|---|
| OPEN the opposite side | flip: close one, open the other | **open a second leg**, keep the first |
| FLIP the opposite side | flip | flip — the author says he *reversed* |
| FLIP to a side already held, other leg open | n/a | **close the other leg** (see §4) |
| CLOSE naming a side | close | close that leg, leave the other |
| CLOSE naming no side, both legs open | n/a | close **both** |
| TRIM / ADD / STOP naming a side | act | act on that leg |
| TRIM / ADD / STOP naming no side, both open | act | **refuse** — `*_side_ambiguous` |

Refusing an unattributable trim/add/stop is deliberate. Acting on the wrong leg is not a
near miss: it reduces the trade the author meant to keep and leaves the one he meant to
reduce untouched. A side-less CLOSE goes the other way and takes everything, because the
words that map to CLOSE ("out", "flat", "all out") read as being out entirely, and reducing
exposure is the direction this codebase already picks when it must pick.

### Turning it on is not a setting
Multi-position follows `exchange_accounts.position_mode` on the strategy's account, read at
startup. On a net account the exchange nets the two legs against each other, so recording a
second leg there would simply be false. The log says which it resolved:

```
LIVE execution armed: strategy=social-btc-astro (Social BTC (AstronomerZero))
  account=blofin-blofin-demo-v5vr ... position_mode=net
multi-position off: account position_mode=net — an OPEN against an existing opposite leg stays a flip
```

### Extractor prompt — v7
`direction` on CLOSE / ADD / STOP now means *the side of the existing position being acted
on* (it already meant that for TRIM), not the resulting one. Without that, a management post
about one of two open legs is unattributable and gets refused. The version bump is what
makes the extraction cache re-extract rather than serve v6 answers.

### order-listener — two leg-scoping fixes
- **`target_position=flat` is two different instructions.** Carried by `close_long` /
  `close_short` it means "close THIS leg whole — the size field is not what decides the
  quantity", which is how the social emitter has always used it. Carried by an entry signal
  it means "be flat in this symbol". It now closes only the named leg in the first case and
  every leg in the second. On a net account there is one leg, so nothing changes there.
- **`/strategies/{id}/adjust-stops` accepts `side`.** Without it the route takes the most
  recently opened position — a coin flip once a long and a short are both open, and the
  loser is a live stop. Callers that omit it now get a warning naming every open position.

---

## 3. Tests

```
social-listener  56 passed in 8.94s     (all new: legs, state machine, handler wiring)
order-listener   81 passed              (21 in test_hedge_mode.py, 8 new here)
order-executor   86 passed
```

The listener's 5 remaining failures (`test_fill_size_open_path`, `test_webhook_handler`)
predate this work — proven in the previous report by running them against `git show HEAD:`.

New coverage includes both modes side by side for every case, because `multi=False` must
keep behaving exactly as the single-stance version did — that is what the live account and
the backtest replay both run on.

---

## 4. A real gap the channel replay found

`app/replay_modes.py` (new, read-only) replays the recorded posts through both modes and
prints the divergences. Run it before switching the account:

```
$ docker compose exec -e DAYS=60 social-listener python -m app.replay_modes
```

The first run exposed a bug in this change. Msg 9795 (2026-07-29, `FLIP LONG`) arrived while
hedge mode had the channel recorded as `LONG+SHORT`. The code saw "already long" and decided
**nothing at all** — leaving a short the author had just said he was out of:

```
  msg 9795 2026-07-29 20:15 BTC FLIP LONG
      net   : flip_to_long   SHORT -> LONG
      hedge : none           LONG+SHORT -> LONG+SHORT      ← wrong
```

A flip's real content is the half not yet done. Fixed, with a test pinning it:

```
  msg 9795 2026-07-29 20:15 BTC FLIP LONG
      net   : flip_to_long   SHORT -> LONG
      hedge : close_short    LONG+SHORT -> LONG            ← after the fix
```

Final divergence over 60 days — 275 posts, 28 actionable, **3 decisions differ**:

```
  msg 9782 2026-07-26 23:16 BTC OPEN SHORT
      net   : flip_to_short  LONG -> SHORT
      hedge : open_short     LONG -> LONG+SHORT
  msg 9795 2026-07-29 20:15 BTC FLIP LONG
      net   : flip_to_long   SHORT -> LONG
      hedge : close_short    LONG+SHORT -> LONG
  msg 9806 2026-08-02 05:12 BTC OPEN SHORT
      net   : flip_to_short  LONG -> SHORT
      hedge : open_short     LONG -> LONG+SHORT

final recorded legs (net  ): {'BTC': 'SHORT'}
final recorded legs (hedge): {'BTC': 'LONG+SHORT'}
```

Twice in two months the trader opened a second position the old model had to express as a
reversal. That is the whole size of the effect on this channel — worth knowing before
deciding it is worth the funding cost of carrying both sides.

---

## 5. What is NOT verified

Same blocker as the hedge-mode work: **no live two-leg run has happened.** BloFin refuses the
position-mode switch while anything is open, and the demo account is carrying two strategy
positions. Everything above is unit-tested and replayed against real recorded posts, but no
order has yet been sent with two social legs live.

The sequence, once the account is flat and its bots are stopped:

1. flip the account to hedge (`POST /api/dashboard/accounts/<id>/position-mode`);
2. restart `social-listener` and confirm the log says **MULTI-POSITION on**;
3. let it take an entry, then an opposite-side entry, and confirm two rows in
   `social_position_state` and two in `strategy_positions`;
4. confirm a stop moved on one leg leaves the other's alone;
5. confirm a trim sizes itself from the named leg;
6. close each leg and confirm PnL lands on the right position.

## 6. Known limitations, stated rather than hidden

- **The backtest replay is net-only.** `backtest_replay.py` prices one position per asset at
  a time — it pairs each transition with the next to find the exit, and a concurrent second
  leg has no place in that walk. It now calls the state machine with `multi=False`
  explicitly and refuses a `LONG+SHORT` seed rather than silently reading it as one side.
  A hedge-mode backtest needs that walk rewritten.
- **A CLOSE post is still gated on price.** The old single-stance code ran closes through the
  same staleness gate as entries, using the short-side test regardless of which side was
  being closed (`going_long = target == "LONG"`, and a close targeted `"FLAT"`). That is
  preserved verbatim rather than quietly re-derived per leg: changing it changes when the
  system exits trades, which deserves its own decision. Worth revisiting — refusing to exit
  because the price moved in your favour is odd.
- **`db/init.sql` still not regenerated** — it lags migrations 070 onward, as flagged in the
  previous report.
