# Shorter no-position intervals: UI options + fleet retune

Follow-up to the entry-frequency work (see
`soften-hold-gates-and-thresholds.md`): the UI's "No Position" interval
dropdown bottomed out at 1h, blocking #3 (scalper → 15m).

## UI change

`Strategies.tsx`: both AI forms' `interval_no_position` option lists extended
from `['1h','2h','4h','8h','1d']` to
`['5m','15m','30m','1h','2h','4h','8h','1d']` (add + edit forms).
Deployed via `./scripts/redeploy.sh dashboard-ui`; live asset
`index-CYmszwP3.js` contains the new list (grep verified in the container).

## Fleet retune (via dashboard API — scheduler reconcile fired per strategy)

| Strategy | interval_no_position | cooldown_entry_minutes | Rationale |
|---|---|---|---|
| bnb scalper (dry-run) | 1h → **15m** | 240 → **60** | a scalper sampling once/hour at the quietest second of the hour can't see bursts; 4h entry cooldown vs a <2h hold target was self-contradictory |
| xrp breakout | 1h → **30m** | 240 → **120** | breaks must be caught near the break, not up to an hour later |
| hype mean_reversion | 1h → **30m** | 0 (kept) | reversion windows are short-lived |
| sol trend_following | 2h → **1h** | 240 → **120** | 12 looks/day was the lowest in the fleet; trend needs no faster than 1h |
| tao range_rotation | 2h → **1h** | 240 → **120** | same |
| btc regime_router | 1h (kept) | 0 (kept) | trades fine (4 opens/7d) |
| eth geometric_range | 1h (kept) | 240 (kept) | best performer of the fleet (7 opens/7d) — untouched |

Live confirmation (scheduler log after the PUTs):

```
bnb-ai-scalper-edbb  sleeping 733s  until candle-close+buffer wake (12.2min)   ← next 15m close
xrp-ai-3844          sleeping 1633s until candle-close+buffer wake (27.2min)   ← next 30m close
hype-breakout-da2e   sleeping 1633s until candle-close+buffer wake (27.2min)
sol-ai-6486          sleeping 3433s until candle-close+buffer wake (57.2min)   ← next 1h close
tao-ai-range-rotation-d257 sleeping 3433s (57.2min)
```

## Cost / load notes

- bnb: 96 cycles/day, but scout-tiered (gemini-2.5-flash-lite ~1.9k tokens
  per scout-final cycle; sonnet only on escalation/every 6th cycle) and
  dry-run.
- xrp on groq free tier gets 48 cycles/day — historical 429s are now
  absorbed by the fallback chain (0 llm_failed since it deployed).
- The differing intervals also thin the top-of-hour thundering herd; the
  2-slot ingest semaphore covers the full-alignment hours.

## What to watch (same as previous report, now with the cadence lever pulled)

Entry proposals on bnb/sol/xrp over the coming week (baseline 0/14d before
the gate-softening + threshold + cadence changes). Remaining lever if still
silent: swap llama-3.3-70b (xrp) / gpt-oss-120b (sol) for less conservative
models.
