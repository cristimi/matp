# social-listener: three partial closes where there should have been one

Date: 2026-07-28
Reported: "last night the social listener acted 3 times at ~the same price with 3 partial
profits taken"

Confirmed, and it was worse than three — **four** partial closes ran against one short.
Two separate defects, plus a third that let the damage compound.

## What actually happened

```
$ ... SELECT id, signal, size, actual_fill_price, pnl, received_at FROM orders
      WHERE strategy_id='social-btc-astro' ORDER BY received_at DESC;

 0ba799dd | close_short | 0.00009687 | 63588.7 | 0.17966 | 2026-07-28 00:11:21
 7f8c2f1b | close_short | 0.0003875  | 63660.2 | 0.69004 | 2026-07-27 23:09:09   <- duplicate
 bb1c9b4c | close_short | 0.000775   | 63643.4 | 1.39352 | 2026-07-27 23:09:01
 afb473a8 | close_short | 0.00155    | 64420   | 1.44795 | 2026-07-27 15:38:09
 21e4d664 | open_short  | 0.00305874 | 65385.3 |       0 | 2026-07-26 23:17:32
```

A 0.00306 BTC short is now a **0.00029 BTC** stub — 9.4% of what was opened. Every fill
was profitable (total realized **+$3.71**), so nothing was lost, but the position was
whittled away against the trader's intent.

## Defect 1 — the live handler and the catchup loop judged the same post at once

This is the "3 times at ~the same price" you saw. Two orders, eight seconds apart, from
one instruction:

```
23:08:52,365 merged 2 messages into one post: [9793, 9794]     <- catchup loop
23:08:59,416 merged 2 messages into one post: [9793, 9794]     <- live buffer
23:08:59,486 msg 9794 [ACTIONABLE] TRIM BTC ref=65500.0 conf=0.72
23:09:01,262 emitted partial_close_short for BTC size=0.000775   -> bb1c9b4c
23:09:07,630 (second extraction returns)
23:09:09,438 emitted partial_close_short for BTC size=0.0003875  -> 7f8c2f1b
```

`handle()` opens with `already_shadow_evaluated(key_id)`, but that row is only written at
the *end*, after the orders have gone out. Between the two sits an LLM call of several
seconds. Both callers read False, both acted. The second took 50% of what the first had
already left, which is why the sizes halve.

`insert_shadow_order`'s `ON CONFLICT DO NOTHING` then hid it — three orders behind two
audit rows, which is why the double was not obvious from the decision table.

This is the same class of bug `ai-signal-generator/app/cycle_lock.py` was written for on
2026-07-25, in a service that has the same shape: independent tasks calling one pipeline.

**Fix.** `social-listener/app/post_lock.py` — non-blocking per-post slot, synchronous
check-and-add with no await between the two statements. The second caller drops the post
rather than queuing behind the first, because queuing would re-run it the moment the
first finished, which is exactly the duplicate. Every call site is labelled
(`live-buffer` / `catchup` / `backfill`) so a drop names who lost.

## Defect 2 — a re-posted trade card was executed again

Post 9794 (23:07) is post 9790's card again, with TP2 filled in:

```
9790 (14:41)                      9794 (23:07)
➡️Entry: 65.5k                    ➡️Entry: 65.5k
➡️Risk off the trade              ➡️Risk off the trade: feel free to thank me
➡️Lock in W 64.4k                 ➡️Lock in W 64.4k: celebrations
➡️TP 2...                         ➡️TP 2: 63.5k: more celebrations
```

The 64.4k trim it asks for had already been taken at 15:38. But by 23:09 the mark was
63643 — long past 64400 — so `_evaluate_trim` read `trim_level_reached` and fired again,
at a worse price than the level it was supposedly honouring.

The author re-posts the card as a trade develops. A repost is not a new instruction.

**Fix.** Every trim now enters one ledger, whether it waits or fires immediately
(`record_fired_trim`), and `trim_already_taken()` checks a new trim against the ones
already carried out **for the current stance** — scoped by the open position's
`opened_at`, so a trim from a trade that has been and gone never blocks a new one. Two
checks: the same named level is refused outright; a `min_trim_interval_minutes` (30)
backstop catches an at-market trim, which has no level to compare.

## Defect 3 — nothing stopped a position being shaved into dust

The last fill closed 0.0000969 BTC — about $6 of notional. Repeated partials converge on
dust, and a stub that small should leave whole on a CLOSE post, not be quartered again.

**Fix.** `fire_trim` refuses when the position is already below
`min_trim_position_fraction` (0.2) of one standard entry.

## A real bug the verification caught

The first live check failed with `KeyError: 'opened_at'` — `db.open_position()` did not
select the column the new dedupe scopes on. Fixed and re-verified rather than shipped.

## Verification

All four suites, 71 checks, in the running container:

```
test_dupe      all checks passed
test_trim      all checks passed
test_stop      all checks passed
test_add_tp    all checks passed
```

The new suite:

```
$ docker compose exec -T social-listener python /app/test_dupe.py
post 9794 already being judged — dropping the live-buffer copy
PASS  only one caller judges a post                         got=1
PASS  lock releases afterwards                              got=set()
PASS  different posts do not block each other               got=(True, True)
PASS  clean stance allows a trim                            got=None
PASS  the same level is refused as a repost                 got='trim_already_taken'
PASS  a genuinely different level still runs                got=None
PASS  but not within the interval backstop                  got='trim_too_soon'
PASS  an at-market trim is caught by the interval too       got='trim_too_soon'
PASS  a trim from a previous stance does not block a new one got=None
PASS  the other side is unaffected                          got=None
PASS  the live stub is below the trim floor                 got=True
PASS  a healthy half-position is not                        got=False
```

And against the real live position, not fixtures:

```
position: short 0.000290630000000000000 @ 65385.3  mark=63388.0
stance opened: 2026-07-26 23:17:36.260262+00:00

  msg 9794 re-posted (the 64.4k card again)  -> trim_already_taken (msg 9790 at 64400.0)
  msg 9792 re-posted (the 63597 level)       -> trim_already_taken (msg 9792 at 63597.0)
  a fresh at-market trim right now           -> ALLOWED
  a genuinely new level                      -> ALLOWED

  fire_trim on the current stub -> ok=False
    position 0.00029063 is below the trim floor 0.00063103 (0.2 of a standard entry)
    — close it whole instead
```

So both instructions that ran last night are now refused as reposts, a genuinely new
level still gets through, and the surviving stub cannot be trimmed again.

## Audit correction

Post 9794's trim fired but left no ledger row (`record_fired_trim` did not exist yet). A
row was inserted for it, labelled as backfilled and naming both duplicate order ids, so
the ledger matches what actually happened.

## Current state — unchanged, and worth a decision

The remaining **0.00029 BTC short @ 65385.3** still has its break-even stop resting at
65385.3. Mark is ~63388, so it is in profit, and the trader's own TP2 (63.5k) has already
been passed. The listener will not trim it again (below the floor) and will not close it
without a CLOSE post. Closing the stub is a trading decision — say the word and I will,
or leave it to ride.
