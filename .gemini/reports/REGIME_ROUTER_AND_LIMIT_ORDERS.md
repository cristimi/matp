# Regime Router template + resting-limit expansion (migration 048)

**Date:** 2026-07-08

## What changed

1. **Migration `db/migrations/048_limit_orders_expansion.sql`**
   - New `public.ai_strategy_config.use_limit_orders` boolean (default `false` — no
     existing strategy changes behaviour). Grants the
     `place_limit_long/short` / `amend_order` / `cancel_order` action set to
     non-geometry strategies.
   - `mean_reversion` prompt: new **PHASE 4 — RESTING LIMIT EXECUTION** (rest the fade
     at the exhaustion level; amend on re-fit; cancel when the gate dies or an event
     approaches; never chase with a limit; placement confidence cap 0.75).
   - `range_rotation` prompt: new **PHASE 2B — WORKING THE EDGES WITH RESTING LIMITS**
     plus a Phase-3 rule: on a confirmed break, cancel the resting order on the broken
     side FIRST. Same conditional pattern.
   - Both blocks are conditional on the OPEN ORDERS section being present in the
     context — with the toggle off, the prompts read exactly as before (honest-absence
     pattern, as in migration 046).
   - New 8th template **`regime_router` ("Regime Router")**: Phase-0 regime
     classification (TRENDING / RANGING / COMPRESSED / EXTENDED / UNCLEAR, ≥2
     independent confirmations required, UNCLEAR ⇒ hold) + four condensed playbooks.
     Hybrid order policy: resting limits allowed only in fade playbooks (RANGING,
     EXTENDED); momentum playbooks (TRENDING, COMPRESSED) are market-entry only — a
     limit beyond price in the break direction fills instantly as an unconfirmed taker
     order. Reasoning must open by naming the regime (attribution). Explicit hold bias.

2. **ai-signal-generator** — decoupled the OPEN ORDERS gate from `use_geometry`:
   - `app/graph/nodes/node_ingest.py`: open orders fetched when
     `use_geometry OR use_limit_orders`.
   - `app/prompt/builder.py`: `_render_open_orders` + section 2.6 gate on either flag.

3. **dashboard-api `src/routes/ai.ts`**: `use_limit_orders` added to
   `ALLOWED_CONFIG_FIELDS` and the prompt-preview mock state.

4. **dashboard-ui `src/pages/Strategies.tsx`**: new data-source toggle
   ("Resting Limit Orders (place/amend/cancel)"), form state/defaults/PUT+POST bodies,
   template presets: added to `mean_reversion`, `range_rotation`, `geometric_range`,
   and the new `regime_router` preset row.

5. **Docs**: `docs/design/ai_prompts/17_regime_router.md` (design rationale; applied
   text lives in the migration) + addenda in `11_mean_reversion.md` and
   `15_range_rotation.md`.

## Proof of verification (pasted output)

Migration applied with self-verification:

```
BEGIN
ALTER TABLE
UPDATE 1
UPDATE 1
INSERT 0 1
COMMIT
NOTICE:  Migration 048 verified OK: use_limit_orders column present, 8 templates, limit-order phases in place
DO
```

Generator test suite (run inside the redeployed container):

```
..........................................                               [100%]
42 passed in 8.36s
```

Builder gate check (in-container, use_limit_orders alone, no geometry):

```
limit_orders-only gate: OK ->   order_id=abc-123  side=buy  price=61000  size=0.01  status=new
both-off gate: OK (empty)
```

Template list via live API (8 rows, regime_router present):

```
8 templates: breakout, conservative, geometric_range, mean_reversion, range_rotation, regime_router, scalper, trend_following
```

Live UI bundle contains the new toggle string:

```
/usr/share/nginx/html/assets/index-BqWPB6pK.js
```

Health + container state after redeploy:

```
{"status":"ok","service":"dashboard-api"}
{"status":"ok","service":"ai-signal-generator","collector":{"running":true,"streams":12,"alive":11,"started_at":1783492197.4571705}}
matp-ai-signal-generator-1   Up 11 minutes (healthy)
matp-dashboard-api-1         Up 8 minutes (healthy)
matp-dashboard-ui-1          Up About a minute
```

Config GET round-trips the new flag:

```
strategy: ai-btc-6f8c
use_limit_orders in config GET: False
```

## Notes / follow-ups

- `use_limit_orders` defaults to false everywhere; no live strategy's behaviour changed
  by this deploy. Enabling it per strategy is a dashboard toggle.
- The router is intended to be trialled in dry_run / strategy-tester alongside the
  specialists before getting live capital, and its reasoning names the chosen regime so
  classification errors can be separated from playbook-execution errors.
- Breakout-style resting entries (stop-market / conditional triggers) remain
  unsupported by design — the action space has no trigger orders; see
  `17_regime_router.md`.
