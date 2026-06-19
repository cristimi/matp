# Follow-up B Report — Divergence badge on Positions card (Bug 3 completion)

**Date:** 2026-06-19  
**Scope:** `dashboard-ui/src/pages/Positions.tsx` only. No other files touched.

---

## Problem

The positions API (Phase 3/4) now returns `size_exchange`, `size_divergent`, and
`margin_exchange` per position, but the UI ignored all three. Operators had no visual
indicator when the reconciler flagged a size mismatch.

---

## Changes — `dashboard-ui/src/pages/Positions.tsx`

### B1 — Extended `interface Position`

Added three optional fields to the local `Position` interface (the snake_case one read by
`PositionCard`):

```ts
size_exchange?:   number | null;
size_divergent?:  boolean;
margin_exchange?: number | null;
```

### B2 — Divergence badge in the Size cell

The `topRow` Size cell now branches on `position.size_divergent`:

**Normal (no divergence):** renders exactly as before — `formatSize(symbol, position.size)` in
`var(--text)` at 13px/700.

**Divergent:** two-line layout:
- Top: tracked DB size in `var(--failed-color)` bold with a `⚠` suffix
- Bottom: `exch <exchange_size>` in `var(--dim)` at 10px (only rendered when
  `size_exchange != null`)

Uses existing CSS tokens (`--failed-color`, `--dim`) and the already-imported `formatSize`
helper. No new dependencies introduced.

---

## Build verification

```
docker compose build --no-cache dashboard-ui
```
TypeScript compiled without errors (a `tsc` error would abort the build stage).

```
docker compose exec dashboard-ui grep -rl "size_divergent" /usr/share/nginx/html/assets/
→ /usr/share/nginx/html/assets/index-CL7c1k40.js

docker compose exec dashboard-ui grep -o "size_divergent|size_exchange|margin_exchange|failed-color|exch " \
  /usr/share/nginx/html/assets/index-CL7c1k40.js | sort | uniq -c
      1 exch
     36 failed-color
      1 size_divergent
      2 size_exchange
```

All badge symbols present in the compiled bundle. ✓

**Visual check note:** A live divergent position is required to exercise the warning branch in
the browser. No such position exists in the current environment (reconciler has not flagged
any row). The badge code is correct and type-safe; it will render automatically the next time
the reconciler sets `reconcile_divergent = TRUE` on a position and the dashboard polls.

---

## Files changed

```
dashboard-ui/src/pages/Positions.tsx             — interface + Size cell badge
.gemini/reports/FOLLOWUP_B_divergence_badge.md   — this report
```
