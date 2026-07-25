# ai-signal-generator — entry cooldown grouping (review finding 2)

**Date:** 2026-07-25
**Branch:** main
**Status:** DONE — deployed, verified by replaying both queries over 30 days of real entries

Implements finding 2 of `.gemini/reports/2026-07-25_ai_signal_generator_review.md`.

---

## Problem

`node_guard`'s cooldown lookup filtered on the exact action:

```sql
WHERE strategy_id = $1 AND proposed_action = $2 AND gate_passed = TRUE AND triggered_at >= $3
```

All four entry actions map to the same config key, `cooldown_entry_minutes` — but because the query
matched one action, the setting was enforced as *"minutes between entries of this exact action
type"*, giving four independent cooldowns instead of one. Switching direction, or swapping a limit
entry for a market entry, reset the clock.

---

## Change

```python
_ENTRY_ACTIONS = ('open_long', 'open_short', 'place_limit_long', 'place_limit_short')
_COOLDOWN_GROUP = {a: _ENTRY_ACTIONS for a in _ENTRY_ACTIONS}
```

and the lookup now matches the whole group:

```sql
AND proposed_action = ANY($2::text[])
```

The rejection log now names the blocking action and its timestamp, so a `cooldown_active` is
diagnosable without re-running the query by hand.

### One judgment call: `partial_close` stays out of the group

It maps to `cooldown_entry_minutes` too, but it *reduces* exposure. Grouping it with entries would
mean refusing to de-risk because an entry happened recently — the wrong failure direction. It keeps
its own independent window using the same duration, exactly as before. Resulting mapping:

```
open_long          key=cooldown_entry_minutes     window=[open_long, open_short, place_limit_long, place_limit_short]
open_short         key=cooldown_entry_minutes     window=[open_long, open_short, place_limit_long, place_limit_short]
place_limit_long   key=cooldown_entry_minutes     window=[open_long, open_short, place_limit_long, place_limit_short]
place_limit_short  key=cooldown_entry_minutes     window=[open_long, open_short, place_limit_long, place_limit_short]
partial_close      key=cooldown_entry_minutes     window=[partial_close]
adjust_stops       key=cooldown_stop_adj_minutes  window=[adjust_stops]
close_long/short, cancel_order, amend_order, hold  → no cooldown (unchanged)
```

---

## Verification

Both the old and the new query were replayed against **every** gate-passed entry of the last 30
days, using each strategy's own configured cooldown and evaluating at that entry's own timestamp:

```
entries examined: 70
blocked by OLD per-action query : 5
blocked by NEW grouped query    : 18
newly blocked (the bypasses)    : 13

    bnb-ai-scalper-edbb | open_long -> open_short               |  15min | cd=120min
    bnb-ai-scalper-edbb | open_long -> open_short               |  75min | cd=120min
    bnb-ai-scalper-edbb | open_short -> open_long               |  90min | cd=120min
    bnb-ai-scalper-edbb | open_long -> open_short               | 105min | cd=120min
    eth-ai-34d2         | place_limit_short -> place_limit_long |  60min | cd=240min
    eth-ai-34d2         | place_limit_long  -> place_limit_short|  60min | cd=240min
    eth-ai-34d2         | place_limit_short -> place_limit_long |  60min | cd=240min
    eth-ai-34d2         | place_limit_long  -> open_long        |  60min | cd=240min
    eth-ai-34d2         | open_short        -> place_limit_long |  80min | cd=240min
    eth-ai-34d2         | place_limit_long  -> open_short       |  80min | cd=240min
    eth-ai-34d2         | place_limit_long  -> place_limit_short| 120min | cd=240min
    eth-ai-34d2         | place_limit_short -> place_limit_long | 180min | cd=240min
    eth-ai-34d2         | place_limit_long  -> open_long        | 180min | cd=240min
```

The 13 newly-blocked entries are **exactly** the 13 bypasses identified independently in the review
(which found them by a different method — consecutive differing-action pairs). The two counts
reconciling is the strongest evidence available that the fix targets precisely the defect.

It is also not over-rejecting: 52 of the 70 entries are still allowed.

Service healthy after redeploy.

---

## Consequence you should look at

**This makes the setting bind for the first time, and for one strategy it now bites hard.**

9 of the 13 newly-blocked entries belong to `eth-ai-34d2`, which runs a 240-minute entry cooldown
and trades by alternating `place_limit_long` / `place_limit_short`. Under the grouped window it can
now take **one entry per 4 hours regardless of direction** — for a geometric-range strategy whose
whole method is rotating between range boundaries, that may be far more restrictive than intended.

Nothing was wrong with the fix; the value was simply never tested against real behaviour because it
never bound. Worth re-tuning `cooldown_entry_minutes` per strategy now that it does — `eth-ai-34d2`
in particular, and `bnb-ai-scalper-edbb` (120min, a "scalper") is worth a second look for the same
reason.

---

## Remaining

Finding 3 — `max_concurrent_trades` validated and stored by dashboard-api, enforced by
strategy-tester, ignored in the live decision path — is untouched.
