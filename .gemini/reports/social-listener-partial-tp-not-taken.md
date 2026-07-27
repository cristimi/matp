# social-listener: partial profit-take was read but never executed

Date: 2026-07-27
Scope: investigation only — no code changed.

## What happened

Telegram post `9787..9790` (AstronomerZero, posted 14:41 UTC 2026-07-27) is a
trade-management card for the short we are already in:

```
➡️Entry: 65.5k
➡️Risk off the trade: feel free to thank me
➡️Lock in W 64.4k: celebrations
➡️TP 2...
....
You know what to do, etc etc, too lazy to type
counting my cash
good night
```

The extractor got asset + direction right and even reasoned about the profit
level, but classified the whole card as a **new OPEN**:

```
$ docker compose exec -T postgres psql -U matp -d matp -x \
    -c "SELECT channel_msg_id, jsonb_pretty(raw_llm_json) FROM social_signal_log WHERE channel_msg_id=9790;"

channel_msg_id | 9790
    "asset": "BTC",
    "evidence": "text",
    "direction": "SHORT",
    "reasoning": "The post presents a trade card with Entry at 65.5k. The profit level
                  (\"Lock in W\", \"celebrations\") is at 64.4k, which is BELOW the entry.
                  Per the rules, profit levels below the entry indicate a SHORT. The stop
                  (\"Risk off the trade\") is not given a specific price. The framing
                  (\"you know what to do\", \"counting my cash\", \"good night\") suggests
                  this is a new call being made now, not a retrospective recap. ...",
    "confidence": 0.72,
    "action_type": "OPEN",
    "is_actionable": true,
    "reference_price": 65500.0
```

Because the recorded stance was already SHORT, the state machine dropped it:

```
$ docker compose exec -T postgres psql -U matp -d matp \
    -c "SELECT channel_msg_id, action_type, from_state, to_state, intended_signal,
               reference_price, mark_price, decision, reason, mode
        FROM social_shadow_orders ORDER BY id DESC LIMIT 2;"

 channel_msg_id | action_type | from_state | to_state | intended_signal | reference_price | mark_price | decision |     reason      | mode
----------------+-------------+------------+----------+-----------------+-----------------+------------+----------+-----------------+--------
           9790 | OPEN        | SHORT      | SHORT    | none            |           65500 |  64608.30  | skipped  | no_state_change | shadow
           9782 | OPEN        | FLAT       | SHORT    | open_short      |        65473.40 |  65383.80  | acted    | ok              | live
```

Log line:

```
social-listener-1 | 2026-07-27 14:42:08,924 INFO social-listener msg 9790 [ACTIONABLE] OPEN BTC ref=65500.0 conf=0.72 img=n
social-listener-1 | 2026-07-27 14:42:08,966 INFO social-listener BRAIN msg 9790 SHORT->SHORT none [skipped/no_state_change] mode=shadow
```

Live position at the time — in profit, no take-profit attached, only the
auto-injected stop:

```
$ ... SELECT ... FROM strategy_positions WHERE strategy_id='social-btc-astro';
side=short entry_price=65385.3 size=0.0031 leverage=20 status=open

$ ... SELECT id, side, signal, size, tp_price, sl_price, status FROM orders ...
 21e4d664 | sell | open_short | 0.00305874 | tp_price=(null) | sl_price=68361.6 | filled
```

Mark at evaluation was 64608.3, i.e. ~777 points in our favour on a 0.0031 BTC
short. The trader's "Lock in W 64.4k" was never acted on.

## Root cause — two independent gaps

**1. Classification.** `social-listener/app/extractor.py` `SYSTEM_PROMPT` teaches
the trade-card format only as a *direction* puzzle (lines 42-62). It has no rule
for a card that restates an entry we are already in and adds profit/stop
management. `OPEN` is defined as "newly entering a position", but nothing tells
the model that a repeated entry price plus TP/stop wording is management of the
open trade. The reasoning shows the model leaned on tone ("counting my cash",
"good night") to call it a fresh entry.

**2. There is no partial-profit path at all**, so even a correct `TRIM` verdict
would have done nothing. Three places block it:

- `extractor.py:249` — `is_actionable = result.is_actionable and result.action_type not in ("ADD", "TRIM")`.
  Scaling events are forced non-actionable by contract. The prompt says the same
  at line 39: `ADD / TRIM - scaling an existing position (always set is_actionable=false for these)`.
- `statemachine.py:19-27` — `_target_state()` returns `None` for anything that is
  not `OPEN`/`FLIP`/`CLOSE`, so `evaluate()` returns `skip("no_target")`.
  The machine has only three states (FLAT/LONG/SHORT); a partial has no state to
  move to.
- `emitter.py:27-34` — `_STEPS` maps only the six full-position signals. There is
  no `partial_close` step and no size fraction in the webhook payload.

So the ceiling is: the social listener can open, flip and fully close. It cannot
trim, add, move a stop, or set a take-profit. Every management post on an open
trade lands as `no_state_change` / `no_target` and is discarded.

Note this also means the card's "Risk off the trade" (move stop to break-even)
was discarded for the same reason.

## Not a regression

This is by design as built, not a bug introduced recently — the ADD/TRIM
suppression and the three-state machine were both in the original contract. What
the post exposes is that the design has no answer for the most common kind of
post this channel makes after an entry.

## Options if we want it to act (not implemented)

1. **Prompt-only fix.** Add a rule: a card whose entry matches a position we are
   already in, plus TP/stop wording, is `TRIM` (or a new `MANAGE`), not `OPEN`.
   Cheap, but on its own it changes nothing downstream — it would only move the
   skip reason from `no_state_change` to `no_target`.
2. **Partial close end-to-end.** Extract a `TRIM` with a fraction and a trigger
   price, add a non-state-changing branch to `evaluate()` (a partial does not
   change FLAT/LONG/SHORT), and add a `partial_close` step to `emitter.py` with a
   size fraction. order-listener already has a `partial_close` action, so the
   receiving side exists.
3. **Take-profit on entry.** Simpler subset: when the post gives a TP level at
   open time, pass `tp_price` in the webhook payload so the exchange holds the
   target. Does not need any new state.

Recommend deciding between 2 and 3 before touching the prompt, since the prompt
change alone has no effect.
