# social-listener: stale legs, and an ADD with nothing open

Date: 2026-08-07

## The incident

Msg 9821 (AstronomerZero, 2026-08-07 13:29:47) — *"POI reached, during NYO, I wrote
extensively why I went back to full size here on the compounded short"* — was
extracted correctly (ADD / BTC / SHORT / conf 0.82 / add_multiple 1) and then dropped:

```
2026-08-07 13:31:06,218 ERROR social-listener EMIT FAILED msg 9821 add_short: no open BTC SHORT position to add to — state left at SHORT
2026-08-07 13:31:06,230 INFO  social-listener BRAIN msg 9821 SHORT->SHORT add_short leg=SHORT [skipped/emit_failed] mode=shadow
```

The listener believed it was short. It was not — and had not been for four days:

```
        id       | side  |  size  | entry_price | closing_price | pnl_realized | status |          closed_at           | close_reason
d38c3ee5-...     | short | 0.0024 |     63454.9 |    63362.0250 |   0.05371150 | closed | 2026-08-03 14:18:50.18474+00 | Stop-loss hit
```

Timeline: opened short 2026-08-02 → msg 9816 said *"Risk off the trade"* so the stop
moved to break-even (63454.9) → 2026-08-03 14:18 price returned and the stop fired →
position closed flat. `social_position_state` was never updated, because it is only
ever written by posts. Four days later the ADD was judged against a position that no
longer existed.

Two independent defects, both fixed here.

## Fix 1 — reconcile recorded legs against real positions

`social_position_state` is written by `apply_leg_changes`, called only from the
brain. A stop-loss, take-profit or liquidation closes a position with no message at
all, so those exits were invisible to the listener.

New `_sweep_closed_legs()` in `social-listener/app/main.py`: for every leg recorded
OPEN, if `strategy_positions` has no matching open row, the leg is cleared and its
parked trims cancelled. Runs at startup (before backfill) and on every
`pending_trim_check_seconds` tick, ahead of the trim and standing-stop sweeps.

Deliberate limits:

- Only OPEN→flat is swept. A position with no recorded leg is left alone — it may be
  a manual trade or another strategy on the same account, and adopting it would let
  this listener manage something it never opened.
- `closed_leg_grace_seconds` (180, new in `config.py`): a leg is only declared stale
  after it has had time to settle. The leg row and the position row are written by
  two different services, so a sweep landing between them would clear a leg that is
  about to be real.
- Shadow mode returns immediately — there is no real position to compare against.

Supporting query `db.recorded_open_legs()` returns the rows (with `updated_at`)
rather than a `Legs`, which is what the grace check needs.

### Proof — it cleared the stale leg by itself on first boot

```
2026-08-07 14:01:52,035 WARNING social-listener LIVE execution armed: strategy=social-btc-astro (Social BTC (AstronomerZero)) account=blofin-blofin-demo-v5vr allocation=107.17 margin/trade=15 leverage=20x isolated position_mode=net
2026-08-07 14:01:52,277 WARNING social-listener RECONCILE BTC SHORT: no open position — leg cleared (recorded open since 2026-08-03 08:35:45.475004+00:00)
2026-08-07 14:01:52,913 INFO    social-listener Backfilling last 50 messages from AstronomerZero
2026-08-07 14:01:54,029 INFO    social-listener Backfill complete (50 messages, 17 post(s))
```

```
matp=# SELECT * FROM social_position_state;
 source | asset | state | last_msg_id | updated_at | stop_price | stop_mode | tp_price | side
--------+-------+-------+-------------+------------+------------+-----------+----------+------
(0 rows)
```

## Fix 2 — an ADD with nothing open is an entry

`resolve_leg` refused an ADD when the named leg was not open, so *"back to full size
on the short"* after a stop-out could never enter. That is the trader getting back
in, not scaling in.

In `statemachine.evaluate()`, an ADD is now re-routed to the OPEN path when **both
legs are flat and the post names a side**:

```python
if action == "ADD":
    if legs.flat and _direction(rec) is not None:
        action, add_as_open = "OPEN", True
    else:
        return _evaluate_add(rec, phase, legs, mark, now, base, skip)
```

Handled by falling through rather than by teaching `_evaluate_add` to open, so the
entry inherits every gate a real entry has — age backstop, chase gate, implied
reference price — with no second copy to drift. It is also sized as a whole standard
entry rather than by `add_multiple`, which is what "full size" means.

Scope kept tight on purpose:

- **Requires a named side.** With both legs flat there is nothing to infer direction
  from, and guessing the direction of a fresh entry is not a near miss.
- **Only the flat case converts.** Holding a long and being told to add to a short
  stays `add_side_mismatch` — that is a state disagreement, not a re-entry.
- Every reason it produces is prefixed `add_as_open:` so the audit row says plainly
  that an ADD post became an entry.

## Tests

Five new cases in `social-listener/tests/test_statemachine_multi.py`, covering the
conversion in both net and hedge mode, the two refusals that must survive, the gates,
and the ordinary scale-in.

```
$ docker compose run --rm --no-deps -v /home/cristi/matp/social-listener:/app -w /app \
    --entrypoint sh social-listener -c "python -m pytest tests/ -q"
.............................................................           [100%]
61 passed in 12.89s
```

## Recovering the missed trade

By the time the fixes were live, msg 9821 was ~35 minutes old — past
`max_signal_age_seconds` (900) — so re-evaluating it would correctly have been
refused as stale. The entry it asked for was placed manually instead, through the
listener's own `emitter.emit` + `db.open_leg`, so the order, sizing, guaranteed
stop-loss and recorded leg all came from the normal path. Only the trigger was
manual. The throwaway script was removed from the container afterwards.

```
2026-08-07 14:03:26,727 INFO httpx HTTP Request: POST http://order-listener:8001/webhook/social-btc-astro "HTTP/1.1 200 OK"
2026-08-07 14:03:26,740 INFO app.emitter emitted open_short for BTC size=0.00461536 -> 200
mark=65000.3 standard entry=0.00461536 BTC
emit ok=True detail=open_short->c142ac9e-31a0-4081-a64a-27160d4e1cb1
state + audit row updated
```

Position, with order-listener's guaranteed SL attached:

```
                  id                  |  symbol  | side  |  size  | entry_price | status |          opened_at
 f815b9be-1e09-424b-a022-75da06534816 | BTC-USDT | short | 0.0046 |       64969 | open   | 2026-08-07 14:03:32.56636+00

                  id                  |   signal   |  signal_source  | status |  size  | sl_price | actual_fill_price
 c142ac9e-31a0-4081-a64a-27160d4e1cb1 | open_short | social_listener | filled | 0.0046 |  67951.1 |             64969
```

State and audit row, both attributed to msg 9821:

```
         source          | asset | side  | state | last_msg_id |          updated_at
 telegram:AstronomerZero | BTC   | SHORT | OPEN  |        9821 | 2026-08-07 14:03:26.741593+00

 channel_msg_id | action_type | from_state | to_state | intended_signal | decision |          reason          | mode | mark_price
           9821 | ADD         | FLAT       | SHORT    | open_short      | acted    | add_as_open:forced_entry | live | 65000.30
```

## Post-deploy check

The reconcile sweep must not eat the leg it just gained. Five minutes of logs after
the entry contain exactly one RECONCILE line — the intended one from boot — and no
errors:

```
$ docker compose logs --since 5m social-listener | grep -iE "RECONCILE|error|exception|Traceback"
social-listener-1  | 2026-08-07 14:01:52,277 WARNING social-listener RECONCILE BTC SHORT: no open position — leg cleared (recorded open since 2026-08-03 08:35:45.475004+00:00)

$ docker compose ps social-listener --format '{{.Name}} {{.Status}}'
matp-social-listener-1 Up 2 minutes

matp=# SELECT asset,side,state,last_msg_id,updated_at FROM social_position_state;
 asset | side  | state | last_msg_id |          updated_at
 BTC   | SHORT | OPEN  |        9821 | 2026-08-07 14:03:26.741593+00
```

## Files changed

- `social-listener/app/main.py` — `_sweep_closed_legs()`, wired into startup and the
  trim loop
- `social-listener/app/db.py` — `recorded_open_legs()`
- `social-listener/app/config.py` — `closed_leg_grace_seconds`
- `social-listener/app/statemachine.py` — ADD-with-nothing-open routes to OPEN
- `social-listener/tests/test_statemachine_multi.py` — 5 new cases
