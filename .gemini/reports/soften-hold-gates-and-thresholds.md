# Softened template hold-gates + threshold/prompt changes (entry-frequency fix)

Follow-up to the 2026-07-14 opening-frequency analysis: three strategies
(bnb scalper, sol trend, xrp breakout) produced ZERO entry proposals in 14
days — every hold cited a Phase-1 binary gate; the confidence gate never got
anything to judge.

## 1. Migration 055 — categorical "output hold" gates → confidence penalties

`db/migrations/055_soften_template_hold_gates.sql` (applied, self-verified):

- **scalper**: liquidity gate now judges exit-viable DEPTH as the hard part;
  below-average volume is a graded penalty (-0.05 / -0.10) instead of a hold.
  Breaking-news gate → -0.10 penalty + "unambiguous trigger" requirement.
  KEPT hard: event-risk window, too-thin depth, stop-width identity rule.
- **trend_following**: 4h trend is now the primary signal; 1d "sideways" is
  tradeable with confidence capped 0.70. KEPT hard: direct 4h-vs-1d
  opposition. Squeeze/compression regime → -0.10 penalty instead of hold.
- **breakout**: compression demoted from prerequisite to A+ context (without
  it, confirmed break of a real level tradeable, capped 0.70). Participation
  leg relaxed from "+50% over 20MA" to "above average" (-0.05 between average
  and +50%). KEPT hard: below-average-volume/CVD-divergence trap signature,
  uneaten-wall absorption.

```
psql:<stdin>:103: NOTICE:  Migration 055 verified OK: hold gates softened in scalper, trend_following, breakout
```

## 2. Prompt + thresholds

- `builder.py` CONFIDENCE SCALE: removed "below threshold, will be rejected"
  / "meets minimum threshold" (self-censorship teaching); bands are now
  neutral evidence descriptions, task text tells the model to propose what it
  sees and score honestly — "downstream risk controls decide whether it
  executes."
- Thresholds 0.72 → 0.65 via dashboard API (scheduler reconcile fired):

```
bnb-ai-scalper-edbb → 0.65
sol-ai-6486 → 0.65
xrp-ai-3844 → 0.65
tao-ai-range-rotation-d257 → 0.65
```

(#3 from the analysis — scalper interval 1h→15m — intentionally left to the
user via UI. #4 was folded into #1: the breakout template got the same
treatment.)

## 3. Bonus bug found & fixed during verification

The first post-change manual cycle held citing "no depth_imbalance_ratio, no
cvd_trend…" — traced to `/internal/trigger` selecting a hand-picked column
list that omitted every newer `use_*` flag, so ALL manual dashboard cycles
ran with orderbook/CVD/geometry/mtf disabled. Now `SELECT s.*, a.*` like the
scheduler. ROADMAP row added.

## Verification

Live prompt (preview endpoint, scalper strategy):

```
absent   'will be rejected'
absent   'Thin tape: output hold'
PRESENT  'a penalty, not a disqualifier'
PRESENT  'weak or mixed evidence'
PRESENT  'score it honestly'
PRESENT  'graded confidence penalty'
```

Live dry-run cycle (id 1629) after all changes — the template now grades
instead of gating, with full flow data visible:

```
**PHASE 1 — TAPE CONDITIONS: PASSED (with penalties)**
- **Event risk:** No scheduled high-impact events ... Gate passes.
- **Liquidity:** Bid depth ±1% = $896M, Ask depth ±1% = $942M — extremely thick book ... Gate passes.
- **Volume:** 17.9% above 20MA — no penalty; mild positive.
```

(It still held at 0.52 — honestly scored, no flow trigger present that
cycle — which is the intended behavior: judgment, not categorical refusal.)

Full ai-signal-generator suite before redeploy: `1 failed, 78 passed`
(the 1 = known pre-existing ccxt-drift `test_ohlcv`). Redeployed via
`./scripts/redeploy.sh ai-signal-generator`, health OK.

## What to watch

Entry-proposal rate on bnb/sol/xrp over the next week (was 0/14d). If they
still never propose, the next lever is the models themselves (llama-3.3 and
gpt-oss-120b are conservative) or the scalper's 1h cadence (#3, user-owned).
