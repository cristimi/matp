# Unify TV & AI Strategy Config Layouts

**Date:** 2026-06-16  
**Branch:** main  
**File changed:** `dashboard-ui/src/pages/Strategies.tsx` only. No backend changes, no migrations.

---

## Files changed

| File | Change |
|------|--------|
| `dashboard-ui/src/pages/Strategies.tsx` | Added `StrategyCommonFields` component; refactored Add + Edit modals for both TV and AI; fixed AI submit payloads; extended capital validation to AI |

---

## New `StrategyCommonFields` component

**Signature:**
```tsx
function StrategyCommonFields({
  form, setForm, accounts, lockSymbolAccount = false, originalCapitalAllocation,
}: {
  form: any;
  setForm: (updater: (f: any) => any) => void;
  accounts: { id: string; label: string; exchange: string; mode: string }[];
  lockSymbolAccount?: boolean;
  originalCapitalAllocation?: number;  // when set, shows drawdown-anchor warning on change
})
```

Renders: `SectionDivider "Identity"` → Name / Symbol / Account → `SectionDivider "Capital & Risk"` → Default Leverage / Max Leverage grid → Margin Mode (only when `'margin_mode' in form`) → Capital / Margin / Drawdown grid → size hint → Quote Variants + Cross-Charting checkboxes.

Used at:
- **Add modal** (line 1387): single call outside the `addType` branches — shared by both TV and AI
- **AI Edit modal** (line 1644): `lockSymbolAccount` + `originalCapitalAllocation` set
- **TV Edit modal** (line 1811): same props

---

## AI submit payload diffs

### Add — AI POST `/api/dashboard/strategies`

```diff
  body: JSON.stringify({
    name:                 addForm.name,
    symbol:               addForm.symbol,
    account_id:           addForm.account_id,
    default_leverage:     parseInt(addForm.default_leverage),
+   max_leverage:         parseInt(addForm.max_leverage),
    strategy_source:      'ai_engine',
+   capital_allocation:   parseFloat(addForm.capital_allocation),
+   margin_per_trade:     parseFloat(addForm.margin_per_trade),
+   max_drawdown_pct:     parseFloat(addForm.max_drawdown_pct),
+   allow_quote_variants: addForm.allow_quote_variants,
+   allow_cross_charting: addForm.allow_cross_charting,
  }),
```

### Edit — AI s1 PUT `/api/dashboard/strategies/:id`

```diff
  body: JSON.stringify({
    name:                 editForm.name,
    symbol:               editForm.symbol,
    account_id:           editForm.account_id,
    margin_mode:          editForm.margin_mode,
    default_leverage:     parseInt(editForm.default_leverage),
    max_leverage:         parseInt(editForm.max_leverage),
+   capital_allocation:   parseFloat(editForm.capital_allocation),
+   margin_per_trade:     parseFloat(editForm.margin_per_trade),
+   max_drawdown_pct:     parseFloat(editForm.max_drawdown_pct),
+   allow_quote_variants: editForm.allow_quote_variants,
+   allow_cross_charting: editForm.allow_cross_charting,
  }),
```

### Capital validation guard (now applies to both types)

```diff
- if (editTarget.strategy_source !== 'ai_engine') {
-   if (parseFloat(editForm.capital_allocation ?? '0') <= 0 || ...) { ... }
- }
+ if (parseFloat(editForm.capital_allocation ?? '0') <= 0 || ...) { ... }
```

Same change applied to `handleAddStrategy` (removed `addType === 'tradingview'` wrapper).

### `handleEdit` — interval added to editForm

```diff
+ interval:                   String(strategy.interval ?? '1h'),
  max_daily_signals:          String(strategy.max_daily_signals ?? 500),
```

---

## §5 Verification — raw output

### Build

```
> matp-dashboard-ui@1.0.0 build
> tsc && vite build

vite v5.4.21 building for production...
✓ 860 modules transformed.
dist/index.html                   0.81 kB │ gzip:   0.40 kB
dist/assets/index-BQKF-5_P.css   22.88 kB │ gzip:   4.66 kB
dist/assets/index-DvGiQb5U.js   686.58 kB │ gzip: 184.91 kB
✓ built in 1m 3s
```

`tsc --noEmit` exit code: **0** (no TypeScript errors).

### Source grep

```
grep -n "StrategyCommonFields" dashboard-ui/src/pages/Strategies.tsx
529:function StrategyCommonFields({
1387:            <StrategyCommonFields form={addForm} setForm={setAddForm} accounts={accounts} />
1644:                <StrategyCommonFields
1811:                <StrategyCommonFields

grep -nc 'SectionDivider label="Capital & Risk"' dashboard-ui/src/pages/Strategies.tsx
1

grep -n 'SectionDivider label="Signal Source"' dashboard-ui/src/pages/Strategies.tsx
1392:                <SectionDivider label="Signal Source" />
1819:                <SectionDivider label="Signal Source" />

grep -n 'SectionDivider label="Dry-Run"' dashboard-ui/src/pages/Strategies.tsx
1546:                <SectionDivider label="Dry-Run" />
1777:                <SectionDivider label="Dry-Run" />
```

### Built bundle grep

```
grep -rl 'Capital & Risk' dashboard-ui/dist/
dashboard-ui/dist/assets/index-DvGiQb5U.js

grep -rl 'Signal Source' dashboard-ui/dist/
dashboard-ui/dist/assets/index-DvGiQb5U.js
```

### AI capital field end-to-end

```
POST /api/dashboard/strategies (capital_allocation=250)
→ {"id":"verify-ai-cap-373b","name":"verify-ai-cap",...}

curl -s http://localhost/api/dashboard/strategies | python3 -c "..."
→ verify-ai-cap 250   ✅

DELETE verify-ai-cap-373b (after stop)
→ {"deleted":"verify-ai-cap-373b"}   ✅
```

### Visual checklist

Cannot open a browser in this environment. Visual checks were not run. The following are confirmed by source inspection only:

- [x] `StrategyCommonFields` renders Identity + Capital & Risk identically for all four modal paths (single shared component, zero per-branch drift possible)
- [x] AI Add/Edit now shows Capital Allocation / Margin Per Trade / Max Drawdown % / routing checkboxes (previously absent — now in common block)
- [x] TV-only Interval + Max Daily Signals appear under `Signal Source` divider in both add and edit tails
- [x] AI-only sections (Operational Parameters → LLM → Strategy Prompt → Dry-Run) appear in the tail
- [ ] Visual confirmation of modal render — not run (no browser access)
- [ ] Editing an AI strategy's Capital Allocation and re-opening modal — not run
