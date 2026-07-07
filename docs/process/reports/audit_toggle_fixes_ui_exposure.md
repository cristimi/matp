# Audit Follow-up — Toggle Alignment (Findings 1–3) + Dashboard Toggle Exposure

**Date:** 2026-07-07
**Trigger:** AI-engine implementation audit found three live strategies whose data-source
toggles didn't match their template's consumption table — root cause: the nine `use_*`
toggles were DB-settable only (the ROADMAP "dashboard toggle exposure" gap).
**Scope:** one SQL pass (data fix) + `dashboard-api/src/routes/ai.ts` +
`dashboard-ui/src/pages/Strategies.tsx`. No ai-signal-generator change, no migration
(all columns existed), no template edits.

---

## 1. Findings fixed (SQL, effective next cycle — scheduler re-reads config per cycle)

- **sol-ai-6486 (trend_following)** was gate-locked: template's single-TF fallback caps
  confidence at 0.70 < its 0.72 threshold with MTF off. Enabled its consumption set
  (`mtf, momentum_divergence, cvd, volatility_regime`).
- **xrp-ai-3844 (breakout)** ran with every consumed field off. Enabled
  (`volatility_regime, volume_profile, orderbook, cvd, mtf`).
- **hype-breakout-da2e (mean_reversion)** kept geometric_range-era toggles after its
  template switch. Enabled (`momentum_divergence, funding_history, volume_profile,
  volatility_regime`); disabled the unconsumed (`mtf, orderbook, cvd, geometry` — the
  mean_reversion prompt has no geometry/resting-limit rules, and the
  `no_range_llm_skipped` guard keys on template_id so it was already inert).

Post-fix matrix (enabled strategies; matches `docs/design/ai_prompts/1*.md` headers
exactly):

```
    strategy_id     |   template_id   | geo | mtf | ob | vp | cvd | md | vr | fh
--------------------+-----------------+-----+-----+----+----+-----+----+----+----
 ai-btc-6f8c        | geometric_range | t   | t   | t  | t  | t   | f  | f  | f
 hype-breakout-da2e | mean_reversion  | f   | f   | f  | t  | f   | t  | t  | t
 sol-ai-6486        | trend_following | f   | t   | f  | f  | t   | t  | t  | f
 xrp-ai-3844        | breakout        | f   | t   | t  | t  | t   | f  | t  | f
```

(`use_economic_calendar` intentionally left false everywhere — provider blocked,
paid-tier; `use_liquidations` false — no source until the Phase-2 collector.)

## 2. Dashboard exposure (closes the ROADMAP item's core)

**API (`dashboard-api/src/routes/ai.ts`):** the 8 new toggles added to
`ALLOWED_CONFIG_FIELDS` (`use_economic_calendar` was already allowed), so PUT
`/api/ai/strategies/:id/config` accepts them; GET already returned them via `SELECT *`.
The preview-prompt mockState now passes all data-source toggles through.

**UI (`dashboard-ui/src/pages/Strategies.tsx`):**
- `DATA_SOURCES` grid (Add + Edit modals) extended from 7 to 16 checkboxes, honest
  labels on the two dormant ones: "Economic Calendar (needs API key)",
  "Liquidations (no source yet)". The `TemplatePreview` active-sources chips pick them
  up automatically (driven by the same list).
- `AiFormState`/defaults/edit-load/both submit bodies carry the nine new fields.
- **Drift prevention — the actual fix for the root cause:** a
  `TEMPLATE_DATA_SOURCES` consumption map (transcribed from the design-doc headers) +
  `templateDataSourcePresets()`. Selecting a template in either modal now presets the
  ten data-source toggles (the nine + `use_geometry`) to that template's consumption
  set; they stay editable afterwards. This is exactly the mechanism whose absence
  produced findings 1–3.

## 3. Verification (pasted)

Deployed via `./scripts/redeploy.sh dashboard-api` and `... dashboard-ui`. Served
bundle is the new build and contains the new UI strings:

```
$ docker compose exec -T dashboard-ui grep -rlo "Multi-Timeframe Structure" /usr/share/nginx/html | head -2
/usr/share/nginx/html/assets/index-DbC_-dKJ.js
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-DbC_-dKJ.js
```

GET exposes all toggles (sol-ai, post-fix):

```
{'use_btc_dominance': False, 'use_cvd': True, 'use_economic_calendar': False, 'use_fear_greed': True, 'use_funding_history': False, 'use_funding_rate': True, 'use_geometry': False, 'use_liquidations': False, 'use_macro': False, 'use_momentum_divergence': True, 'use_mtf_structure': True, 'use_news': True, 'use_open_interest': True, 'use_orderbook': False, 'use_technical': True, 'use_volatility_regime': True, 'use_volume_profile': False}
```

PUT round-trip through nginx on a previously-unsettable field, then reverted:

```
PUT -> True
revert -> False
```

## 4. Follow-ups

- The exposure gap was tracked in `20_plumbing_specs.md` §11 (not a ROADMAP bullet) —
  now closed; tester `ai_strategy_config` parity remains its own ROADMAP item.
- The audit's finding 4 (`_render_task` `increase` action vs output schema → periodic
  `llm_failed`) remains open on the backlog — separate fix.
- Watch sol-ai/xrp-ai's next cycles: both should now produce their templates' full
  confluence reads (sol-ai was the gate-locked one).
