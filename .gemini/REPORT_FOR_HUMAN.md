# MATP — Display Strategy Instructions + Active Data Sources on AI Config
## Implementation Report

---

## 7a. API returns system_prompt

`curl -s http://localhost/api/ai/templates | python3 -m json.tool` (scalper object):

```json
{
    "id": "scalper",
    "name": "Scalper",
    "description": "High-frequency short-duration trades on lower timeframes with tight risk management.",
    "system_prompt": "You are a quantitative crypto analyst specializing in scalping strategies on perpetual futures.\nYou trade on short timeframes (15m-1H). Your primary signals are VWAP positioning, order flow imbalance, and momentum bursts.\nYou use very tight stop losses (0.3-0.8%). You close positions quickly — target hold time under 2 hours.\nYou avoid entering during low-volume periods or major news events."
}
```

All 6 templates (breakout, conservative, mean_reversion, range_rotation, scalper, trend_following) include a non-empty `system_prompt`. PASS.

---

## 7b. Compiled API contains the new column

```
/app/dist/routes/ai.js:        const { rows } = await (0, db_1.getPool)().query('SELECT id, name, description, system_prompt FROM ai_prompt_templates ORDER BY name');
```

PASS — SELECT string confirmed in compiled dist.

---

## 7c. UI bundle contains the new component

```
/usr/share/nginx/html/assets/index-MozenZls.js
```

PASS — `Active Data Sources` string found in the live UI bundle.

---

## 7d. Health

`curl -sf http://localhost/health` → `OK` (returns 200). PASS.

---

## 7e. Visual check

Not performed (no browser access from this environment). Code verified: `TemplatePreview` component correctly renders `tmpl.system_prompt` inside a `<pre style="white-space:pre-wrap">` block and derives active data source pills from `DATA_SOURCES.filter(s => form[s.key])`. Both create and edit forms wire the component. The `range_rotation` template's multi-line system_prompt will wrap due to `pre-wrap`.

---

## Actual line numbers edited

| File | Description | Actual location |
|------|-------------|-----------------|
| `dashboard-api/src/routes/ai.ts` | Added `system_prompt` to SELECT | line 71 |
| `dashboard-ui/src/pages/Strategies.tsx` | `aiTemplates` state type extended | line 741 |
| `dashboard-ui/src/pages/Strategies.tsx` | `TemplatePreview` component added before `interface AiFormState` | after line 675 |
| `dashboard-ui/src/pages/Strategies.tsx` | Create-form template preview replaced | ~line 1527 (shifted after component insert) |
| `dashboard-ui/src/pages/Strategies.tsx` | Edit-form template preview replaced | ~line 1758 (shifted after component insert) |

No field names or selectors differed from the prompt. All edits were exact matches.
