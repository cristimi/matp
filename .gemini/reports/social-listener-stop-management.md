# social-listener: stop management ("risk off the trade")

Date: 2026-07-27
Follows: `.gemini/reports/social-listener-partial-close.md`

## What was wrong

Post 9787-9790 gave two instructions. The trim ("Lock in W 64.4k") was built and fired
live at 15:38 for +1.448 USDT. The other one — **"Risk off the trade"**, meaning move
the stop to break even — still had nowhere to go: the extractor had no stop field, the
state machine no stop branch, the emitter no call.

So the surviving half of the short kept the stop order-listener injected at entry:

```
side  | entry_price | size    | tp_price | sl_price
short | 65385.3     | 0.00155 |          | 68361.6
```

A stop at 68361.6 on a short entered at 65385.3 is a **losing** stop, sitting there
while the trade was ~1000 points in profit. The trader had explicitly asked for it to
be moved.

## What was built

**Stop fields ride alongside `action_type`, not instead of it.** One card routinely
trims *and* de-risks, so a stop is extra instruction attached to whatever the post
already does — not a competing action. Extractor v4 adds `stop_price` (a named level)
and `stop_to_breakeven` (the trader wants the stop at entry and names no price), valid
on any verdict that leaves a position open. A new `STOP` action_type covers the case
where moving the stop is the post's entire content (migration 067 widens the
`action_type` check constraint).

**`statemachine.evaluate_stop()`** is a pure function returning `(stop_price, reason)`,
with three guards. Guard 1 settles the open question the backlog raised —
**tighten-only**: a social stop may only ever sit at break-even or better. It can
tighten what order-listener's guaranteed SL already put in place; it can never widen
it. Widening is the only direction that increases how much a trade can lose, and a
misread post must not be able to do that. Guard 2 refuses a stop already past the mark,
which is not a stop but a market exit wearing a stop's name. Guard 3 is monotonic: once
tightened, a later post may only tighten further, tracked in the new
`social_position_state.stop_price` and cleared whenever the stance changes.

**`emitter.adjust_stop()`** posts order-listener's existing
`/strategies/{id}/adjust-stops` (same webhook token). No order-listener or
order-executor change was needed. Two details that matter:

- It passes the position's existing `tp_price` through. `modify-stops` is
  cancel-then-place across *every* trigger, so sending a lone `sl_price` would silently
  delete a resting take-profit.
- It requires `sl_ok is True`, not just `success`. That endpoint's own docstring says
  cancel-then-place is not atomic and a caller must inspect `sl_ok`/`tp_ok` to know
  whether the position is currently unprotected. A failure is logged as an error naming
  that risk, and is never recorded as a stop we hold.

**Ordering.** Stops are applied *before* the position half of a decision.
order-listener re-applies a position's pre-close TP/SL after a partial reduce
(`_resize_stops_after_partial_close`), so a stop moved *after* a trim would race that
resize and could be silently reverted. Moved before it, the resize picks up the new
stop and rescales it to the smaller size. Stops are also skipped entirely unless the
post leaves us on the side we are already on — a CLOSE or FLIP takes its stops with it,
and an OPEN has no position yet.

## Verification

Migration applied to the live DB:

```
$ docker compose exec -T postgres psql -U matp -d matp -v ON_ERROR_STOP=1 < db/migrations/067_social_stop_management.sql
ALTER TABLE
COMMENT
COMMENT
ALTER TABLE
ALTER TABLE
ALTER TABLE
COMMENT
COMMENT
ALTER TABLE
COMMENT
NOTICE:  Migration 067 verified OK: social stop-management schema present
DO
```

18 stop checks, run in the running container:

```
$ docker compose exec -T social-listener python /app/test_stop.py
PASS  risk-off moves the stop to entry                    got=(65385.3, 'ok')
PASS  named stop below entry accepted (locks profit)      got=(65000.0, 'ok')
PASS  stop above entry on a short refused                 got=(None, 'stop_would_widen_risk')
PASS  stop below entry on a long refused                  got=(None, 'stop_would_widen_risk')
PASS  stop already crossed refused (short)                got=(None, 'stop_already_crossed')
PASS  stop already crossed refused (long)                 got=(None, 'stop_already_crossed')
PASS  tighter than the last stop accepted                 got=(65100.0, 'ok')
PASS  looser than the last stop refused                   got=(None, 'stop_not_tighter')
PASS  break-even refused once we are already tighter      got=(None, 'stop_not_tighter')
PASS  long risk-off moves to entry                        got=(65000.0, 'ok')
PASS  long tightens upward                                got=(65500.0, 'ok')
PASS  no stop instruction is a no-op                      got=(None, 'no_stop_instruction')
PASS  no position, no stop                                got=(None, 'no_position_for_stop')
PASS  stale post does not move a stop                     got=(None, 'signal_too_old')
PASS  break-even with no known entry refused              got=(None, 'no_entry_price')
PASS  explicit level beats break-even                     got=(65100.0, 'ok')
PASS  STOP post moves no position                         got=('skipped', 'stop_only', False, False, 'SHORT')
PASS  a card that trims AND de-risks still parks its trim got=('pending', 'trim_level_pending', True, 64400.0)

all checks passed
```

The 23 trim checks from the previous session still pass unchanged (`test_trim.py`:
`all checks passed`).

End-to-end against the live service, using the endpoint's own `dry_run` so nothing
reached the exchange:

```
$ docker compose exec -T social-listener python /app/verify_stop.py
stance=SHORT last_stop_we_set=None mark=64564.6
position: short 0.001550000000000000000 @ 65385.3 tp=None sl=68361.6

extractor v4: {"action_type": "TRIM", "direction": "SHORT", "trigger_price": 64400.0,
               "stop_price": null, "stop_to_breakeven": true, "confidence": 0.62}
evaluate_stop -> 65385.3 (ok)
adjust-stops DRY RUN -> ok=True dry run: intended sl=65385.3
guard check: stop at 68385.3 -> None (stop_would_widen_risk)
```

So v4 reads "Risk off the trade" as `stop_to_breakeven=true`, the guards resolve it to
the entry price 65385.3, the HTTP path to order-listener works, and the same path
refuses a widened stop.

## Not applied to the open position

The live short (0.00155 BTC @ 65385.3) still carries its entry-time stop at 68361.6.
Moving it to 65385.3 is a real change to how that trade can end — it would close at
break-even on a bounce instead of running — so it is an operator decision, not a deploy
step. Everything is in place to do it; only the instruction is missing.

Every future management post gets this automatically.

## Known limits

- **Break-even means exactly entry.** Fees are not added as a buffer, so a stop-out at
  break-even is a small net loss (about 0.06% of notional per side). Adding a buffer is
  a one-line change if that turns out to matter.
- **A widened stop is silently ignored**, recorded as `stop_would_widen_risk`. If the
  channel ever gives a genuinely wider stop that is worth following, this rule is what
  would have to change — deliberately, and with the same reasoning re-examined.
- **ADD / scale-in** remains unbuilt. See the Deferred Backlog entry: it needs a
  capital rule, which TRIM and stop moves did not.
