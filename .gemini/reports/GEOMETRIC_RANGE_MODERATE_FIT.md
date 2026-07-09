# geometric_range: allow moderate-fit patterns through to the LLM

Context: over the first 2 days of live AI trading, the ETH geometric_range strategy was
skipped before the LLM on 22 of 24 cycles because `fit_quality` was binary
(strong = both trendline R² ≥ 0.70, else weak) and only strong fits passed the gate.
Decision (user-approved): introduce a middle tier rather than blanket-relaxing
confidence thresholds — analysis showed the confidence gate rejected only 1 signal in
2 days, so the pre-LLM geometry gate was the actual bottleneck.

## Changes

1. **`app/data/geometry.py`** — new `MODERATE_R2 = 0.50`; `fit_quality` is now
   three-tier: `strong` (min R² ≥ 0.70), `moderate` (≥ 0.50), `weak` (< 0.50, still
   classifiable down to 0.30).
2. **`app/graph/gating.py`** — `should_skip_llm_no_range` now skips only when
   `fit_quality not in ('strong', 'moderate')`; moderate fits reach the LLM.
   Position-open carve-out unchanged.
3. **`app/graph/nodes/node_skip.py`** — reasoning/log wording updated
   ("strong- or moderate-fit").
4. **`app/prompt/builder.py`** — GEOMETRIC PATTERN section renders moderate honestly:
   not "UNRELIABLE", but position-in-range flagged "moderate fit — boundaries carry
   some noise"; unclassified-structure label now names the actual fit tier.
5. **`db/migrations/051_geometric_range_moderate_fit.sql`** — surgical `replace()`
   amendments to the live `geometric_range` system prompt:
   - Phase 1: new resting orders allowed for strong **or moderate**; moderate requires
     ≥ 3 touches on EACH boundary.
   - Confidence calibration: moderate caps at 0.75 and may only exceed 0.72 (the ETH
     gate threshold) when the worked boundary has HVN/value-area confluence — i.e.
     moderate-fit trades fire only in a narrow, confluence-backed band.
   - Weak stays hold-only for new placements; Phases 3–5 order management unchanged.
6. **Tests** — new: moderate does-not-skip (gating), moderate render (builder);
   updated: fit-quality value set.

## Verification (pasted output)

Migration applied to live DB:

```
BEGIN
UPDATE 1
UPDATE 1
COMMIT
psql:<stdin>:57: NOTICE:  Migration 051 verified OK: geometric_range template now trades moderate fits (3+ touches, confidence cap 0.75)
```

Full test suite in a container with the tests mounted (`docker compose run --rm
--no-deps -v .../tests:/app/tests ... python -m pytest tests/ -q`):

```
............................................                             [100%]
44 passed in 15.24s
```

New code confirmed inside the redeployed container:

```
$ docker compose exec -T ai-signal-generator grep -n "MODERATE_R2      = 0.50" /app/app/data/geometry.py
48:MODERATE_R2      = 0.50
$ docker compose exec -T ai-signal-generator grep -n "not in ('strong', 'moderate')" /app/app/graph/gating.py
26:    return gd.get('fit_quality') not in ('strong', 'moderate')
```

Health:

```
{"status":"ok","service":"ai-signal-generator","collector":{"running":true,...}}
```

## Expected effect

ETH (and BTC regime_router's geometry section) will now run the LLM on moderate-fit
channels instead of auto-holding. New entries from moderate fits still need: ≥ 3
touches per boundary, confidence ≥ 0.72 (gate) with a 0.75 cap, and HVN/value-area
confluence to exceed 0.72 — deliberately a narrow band, so expect somewhat more LLM
evaluations and occasionally more trades, not a flood. Post-only entry orders
(deployed earlier today) bound the execution risk of any additional signals.
