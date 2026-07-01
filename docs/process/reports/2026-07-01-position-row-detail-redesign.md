# Position Row + Detail Redesign — 2026-07-01

## Phase 1 — Surface TP (backend)

### Changes

- `dashboard-api/src/routes/strategies.ts`:
  - Added `o_open.tp_price` to SELECT (mirrors `sl_price`)
  - Added `tp_price: r.tp_price != null ? Number(r.tp_price) : null` in `res.json` map block
- `dashboard-ui/src/api.ts`:
  - Added `tp_price: number | null` to `TreePosition` interface

### Verification

```
$ docker compose exec dashboard-api curl -s \
    "http://localhost:8003/strategies/sui-manual-59d9/positions?scope=all&limit=2" \
  | python3 -m json.tool | grep -E '"(sl_price|tp_price|id)"' | head -12

        "id": "3cb26cf7-c672-4daa-9fac-3f5e383a828e",
        "sl_price": 0.6593,
        "tp_price": 1.5,
```

`tp_price` key is present (1.5 for this position). A position without a TP would return `null` — field always present.

---

## Phase 2 — Main row redesign (UI)

### Changes (`dashboard-ui/src/pages/StrategyTree.tsx`)

- Added `priceGridCols` array (computed before `return`) for Open/Closed price grids.
- **Side cell**: `LONG`/`SHORT` pill + `HeaderPill variant="neutral"` leverage pill stacked below it, centered.
- **Asset/size/notional cell** (header only): `{base_asset} {size}` on line 1, `≈$…` on line 2 (dim). Hidden when expanded; replaced by `{base_asset}-{quote_asset}` symbol name.
- **PnL cell** (header only): absolute PnL on line 1, percent on line 2 (smaller, same color, centered). Trimmed leading space from `fmtPnlPct` output.
- **Two-row price grid** (header only): CSS `display: grid; grid-template-columns: repeat(N, auto)` — label row then value row, Mark column green, missing values `—`. Open: 5 cols (Open/Mark/SL/TP/Liq); Closed: 4 cols (Open/Close/SL/TP).
- **Collapse when expanded**: when `posState !== 'header'`, hides size/notional, PnL cell, and price grid. Only side cell + symbol name (+ ✕) remain.

### Verification

```
$ ./scripts/redeploy.sh dashboard-ui
  live dashboard-ui asset: index-C-dNgAHj.js
✓ dashboard-ui redeployed.

$ docker compose exec -T dashboard-ui grep -c 'priceGridCols\|var(--green)' \
    /usr/share/nginx/html/assets/index-C-dNgAHj.js
2

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-C-dNgAHj.js
```

New bundle live. Visual confirmation required: position card in `header` state shows stacked side+lev cell, asset/size/notional, PnL with stacked percent, and a two-row grid (Open/Mark/SL/TP/Liq, Mark green, `—` for missing). Tap to expand — row collapses to side pill + lev pill + `SUI-USDC` symbol name + ✕.

---

## Phase 3 — Detail panel regrouping (UI)

### Changes (`dashboard-ui/src/pages/StrategyTree.tsx`)

- Added `DRSeg` type and `DR` component (label + segments joined by `sep`, defaults to ` · `; price-journey rows pass `sep=" → "`; empty segs array hides the row).
- Replaced flat `KV` list in detail panel with grouped `DR` rows.

**Open detail order:** PnL (colored, with `%`) → Levels (`Liq · SL · TP`, all show `—` when null) → Price (`Entry → Mark`) → Size (`size · ≈$notional`) → Margin (`$margin · leverage×`) → Opened.

**Closed detail order:** PnL (`Realized … %`, colored) → Levels (`SL · TP`, hidden if both null) → Price (`Entry → Close`) → Size → Margin → Close reason (if present) → Time (`Opened … · Closed …`).

### Verification

```
$ ./scripts/redeploy.sh dashboard-ui
   live dashboard-ui asset: index-uk89rrF4.js
✓ dashboard-ui redeployed.

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-uk89rrF4.js

$ docker compose exec -T dashboard-ui grep -o \
    '"Realized[^"]*"\|"Levels"\|"Margin"\|"Close reason"\|"Time"\|"PnL"' \
    /usr/share/nginx/html/assets/index-uk89rrF4.js | head -20
"Margin"
"Margin"
"PnL"
"Levels"
"Margin"
"PnL"
"Levels"
"Margin"
"Close reason"
"Time"
"Realized"
```

All new row labels confirmed in the deployed bundle. Visual confirmation required: open detail shows PnL/Levels/Price/Size/Margin/Opened in order with TP in Levels as `—` when absent, Margin row present; closed detail shows grouped Price (`Entry → Close`) and combined Time row.
