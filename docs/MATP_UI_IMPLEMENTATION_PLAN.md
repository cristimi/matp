# MATP Mobile UI — Implementation Plan
**Design reference:** `matp-ui-v0.33.html` (attached in this repo)  
**Target:** `dashboard-ui/` — React + TypeScript, served at port 3000  
**Executor:** Gemini Flash CLI running in project root

---

## 0. Before you start

1. Read `matp-ui-v0.33.html` in full. It is the single source of truth for every visual decision — colors, spacing, typography, card anatomy, action buttons, decimal precision, section ordering.
2. Read `dashboard-ui/` directory structure and note the existing component files, routing setup, and any existing CSS.
3. Read `dashboard-api/` routes to understand all available REST endpoints and WebSocket events.
4. Do **not** invent field names. Every API field used in the UI must map to a real endpoint response key. If a field is not yet in the API, note it with a `// TODO: API` comment.

---

## 1. Design tokens — global CSS variables

Create `dashboard-ui/src/styles/tokens.css` with **exactly** these variables (taken verbatim from v0.33):

```css
:root {
  --bg:              #f4f6fa;
  --bg2:             #ffffff;
  --bg3:             #f8fafc;
  --border:          #e2e8f0;
  --border-hi:       #cbd5e1;
  --text:            #0f172a;
  --muted:           #475569;
  --dim:             #64748b;
  --green:           #00a877;
  --green-a:         rgba(0,168,119,.08);
  --green-b:         rgba(0,168,119,.22);
  --red:             #e11d48;
  --red-a:           rgba(225,29,72,.08);
  --red-b:           rgba(225,29,72,.22);
  --failed-color:    #E69802;
  --failed-color-a:  rgba(230,152,2,.08);
  --failed-color-b:  rgba(230,152,2,.24);
  --gray:            #64748b;
  --gray-a:          rgba(100,116,139,.10);
  --gray-b:          rgba(100,116,139,.25);
  --blue:            #2563eb;
  --blue-a:          rgba(37,99,235,.08);
  --blue-b:          rgba(37,99,235,.22);
  --r:               14px;
  --pill-r:          6px;
}
```

Import `tokens.css` in `main.tsx` (or the root entry point). Also add the two Google Font imports:

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
```

Body font: `Inter, system-ui, sans-serif`. All numbers, labels, badges, timestamps: `JetBrains Mono, monospace`.

---

## 2. Shared component library

Create `dashboard-ui/src/components/shared/`. These components are used across all three screens.

### 2.1 `HeaderPill.tsx`

A single pill component used for LONG/SHORT/BUY/SELL, leverage, margin mode, status, and route nodes.

Props:
- `variant`: `'long' | 'short' | 'tech' | 'open' | 'stale' | 'closed' | 'neutral' | 'paused'`
- `children`: string

CSS rules (from `.header-pill` and its color variants in v0.33):
- Base: `font-family: JetBrains Mono; font-size: 10px; font-weight: 600; text-transform: uppercase; border-radius: var(--pill-r); padding: 2px 6px; border: 1px solid; display: inline-block; flex-shrink: 0; line-height: 1;`
- `long`:    bg `var(--green-a)`, color `var(--green)`, border `var(--green-b)`
- `short`:   bg `var(--red-a)`, color `var(--red)`, border `var(--red-b)`
- `tech`:    bg `var(--blue-a)`, color `var(--blue)`, border `var(--blue-b)`
- `open`:    same as `long`
- `stale`:   bg `var(--failed-color-a)`, color `var(--failed-color)`, border `var(--failed-color-b)`
- `closed`:  bg `var(--gray-a)`, color `var(--gray)`, border `var(--gray-b)`
- `neutral`: bg `var(--bg2)`, color `var(--muted)`, border `var(--border)`, `text-transform: none`
- `paused`:  same as `stale`

### 2.2 `StatusChip.tsx`

Used only on Orders for execution status. Different from `HeaderPill` — larger padding, always uppercase.

Props: `status: 'filled' | 'lag-fail' | 'route-fail' | 'pending'`

CSS (from `.chip`):
- Base: `font-family: JetBrains Mono; font-size: 10px; font-weight: 700; letter-spacing: .04em; border-radius: var(--pill-r); padding: 2px 6px; border: 1px solid; text-transform: uppercase; flex-shrink: 0;`
- `filled`:     green-a / green / green-b
- `lag-fail`:   failed-color-a / failed-color / failed-color-b
- `route-fail`: same as `lag-fail`
- `pending`:    blue-a / blue / blue-b

### 2.3 `DataLabel.tsx`

The small uppercase label used above every value in data grids.

CSS (from `.dl`): `font-size: 9px; font-weight: 600; letter-spacing: .11em; text-transform: uppercase; color: var(--dim); margin-bottom: 2px;`

### 2.4 `DataGrid.tsx`

The shared 3-column data grid used in all cards (positions top row, positions bottom row, orders row).

Props:
- `rows`: Array of rows, each row is an array of `{ label: string, value: ReactNode }` (max 3 cells per row)
- `topRowBorder?: boolean` — if true, renders a border below the first row

CSS (from `.pc-matrix-table`, `.pc-matrix-row`, `.pc-cell`):
- Container: `display: flex; flex-direction: column; margin: 8px 12px 8px 18px; border-radius: var(--pill-r); overflow: hidden; border: 1px solid var(--border); background: rgba(226,232,240,.4);`
- Row: `display: flex; width: 100%;`
- Top row gets `border-bottom: 1px solid var(--border);`
- Cell: `flex: 1; padding: 6px 10px; display: flex; flex-direction: column; gap: 1px; border-right: 1px solid var(--border);`
- Last cell: `border-right: none;`

### 2.5 `ActionBand.tsx`

The action strip at the bottom of cards.

Props: `buttons: Array<{ label: string, color: 'red' | 'blue' | 'green' | 'orange', onClick: () => void }>`

CSS (from `.pc-action-band`, `.pc-close-btn`, `.pc-refresh-btn`, `.pc-resume-btn`, `.pc-pause-btn`):
- Container: `border-top: 1px solid var(--border); background: var(--bg2); display: flex;`
- Button: `flex: 1; background: transparent; border: none; font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; padding: 10px; cursor: pointer; text-align: center;`
- All buttons except the last get `border-right: 1px solid var(--border);`
- Colors: red → `var(--red)`, blue → `var(--blue)`, green → `var(--green)`, orange → `var(--failed-color)`

### 2.6 `SummaryBar.tsx`

The three-cell counter bar at the top of each screen.

Props: `cells: Array<{ count: number, label: string, variant: 'live' | 'stale' | 'closed' }>`

CSS (from `.summary-bar`, `.sum-cell`, `.sum-num`, `.sum-lbl`):
- Bar: `display: flex; background: var(--bg2); border-bottom: 1px solid var(--border); flex-shrink: 0;`
- Cell: `flex: 1; display: flex; flex-direction: column; align-items: center; padding: 10px 0 9px; border-right: 1px solid var(--border); gap: 3px; position: relative;`
- Last cell: `border-right: none;`
- Bottom accent line (pseudo): `position: absolute; bottom: 0; left: 18%; right: 18%; height: 2px; border-radius: 2px;`
  - live → `var(--green)`, stale → `var(--failed-color)`, closed → `var(--gray)`
- Number: `font-family: JetBrains Mono; font-size: 24px; font-weight: 700; letter-spacing: -.02em; line-height: 1;`
- Label: `font-size: 10px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--dim);`

### 2.7 `SectionHeader.tsx`

The colored section divider used between groups.

Props: `label: string; count: number; variant: 'live' | 'stale' | 'closed'`

CSS (from `.sec-head`, `.sec-dot`, `.sec-label`, `.sec-count`):
- Container: `display: flex; align-items: center; gap: 8px; padding: 4px 2px 10px; margin-top: 14px;`
- First child: `margin-top: 0;`
- Dot: `width: 9px; height: 9px; border-radius: 50%;` — live → green, stale → `var(--failed-color)`, closed → gray
- Label: `font-size: 12px; font-weight: 800; letter-spacing: .07em; text-transform: uppercase;` — colored by variant
- Count pill: `font-family: JetBrains Mono; font-size: 11px; font-weight: 700; border-radius: 20px; padding: 2px 9px; border: 1px solid;` — bg/color/border from variant

### 2.8 `TopBar.tsx`

Props: `title: string; right?: ReactNode`

CSS (from `.topbar`): `display: flex; align-items: center; justify-content: space-between; padding: 18px 20px 12px; background: var(--bg2); border-bottom: 1px solid var(--border); flex-shrink: 0;`  
Title: `font-size: 23px; font-weight: 800; letter-spacing: -.02em;`

### 2.9 `BottomNav.tsx`

Props: `active: 'strategies' | 'positions' | 'orders'; hasDot?: boolean`

CSS (from `.bnav`, `.ni`, `.ni-label`, `.ndot`):
- Bar: `background: var(--bg2); border-top: 1px solid var(--border); display: flex; padding: 8px 0 16px; flex-shrink: 0;`
- Item: `flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 5px 0; position: relative;`
- Label: `font-size: 9px; font-weight: 600; text-transform: uppercase; color: var(--dim);`
- Active label: `color: var(--blue);`
- Notification dot: `position: absolute; top: 5px; right: 24px; width: 6px; height: 6px; border-radius: 50%; background: var(--red);`
- Tabs: ⚙️ Strats · 📈 Positions · 📋 Orders

### 2.10 `FilterBar.tsx`

Props: `filters: Array<{ label: string, active?: boolean, clear?: boolean, onClick: () => void }>`

CSS (from `.filter-bar`, `.fp`):
- Bar: `display: flex; gap: 6px; padding: 10px 14px; border-bottom: 1px solid var(--border); overflow-x: auto; flex-shrink: 0;`  
- Pill: `white-space: nowrap; background: var(--bg2); border: 1px solid var(--border); border-radius: 20px; padding: 5px 12px; font-size: 10px; font-weight: 500; color: var(--muted); cursor: pointer;`
- Active: `background: var(--blue-a); border-color: var(--blue); color: var(--blue);`
- Clear: `color: var(--red);`

---

## 3. Screen: Strategies (`/strategies`)

### 3.1 Data

Fetch from: `GET /api/strategies` — returns list of strategies.

Expected fields per strategy:
```ts
{
  id: string;           // e.g. "str_08f1"
  name: string;         // e.g. "grid-v2"
  status: 'active' | 'paused' | 'inactive';
  hook_type: string;    // e.g. "TV Hook", "Telegram"
  open_positions: number;
  pairs: string[];      // e.g. ["BTC", "DOT", "SOL"]
  source: string;       // e.g. "TradingView", "Telegram"
  destination: string;  // e.g. "blofin / hl", "blofin"
  uptime_label: string; // e.g. "14d 02h" — or compute from started_at
  stopped_at?: string;  // ISO timestamp if paused/inactive
  total_orders: number;
  win_rate: number;     // 0–100
  allocated: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_return: number; // percentage
  close_reason?: string; // e.g. "Target Benchmark Achieved"
}
```

### 3.2 Layout

Summary bar: **Active** (green) · **Paused** (orange/failed-color) · **Inactive** (gray)

Sections in order: Active → Paused → Inactive

### 3.3 Strategy card anatomy

Each strategy uses the shared `.pc` card component:

```
[left bar: green=active, failed-color=paused, gray=inactive]

pc-row1:   [strategy name (pc-sym)] [ACTIVE/PAUSED/INACTIVE pill] [hook type pill (c-tech)] [X pos pill (c-open/c-closed)]
strat-row: [ID: str_XXXX (id-tag, dashed border)] [pairs list tag]
pc-row1b:  [source → destination (header-pill c-neutral)] . . . [Uptime: Xd Yh | Paused: time | Stopped: date]
DataGrid top row:  Total Orders | Win Rate | Allocated
DataGrid bot row:  Realized P&L | Unrealized P&L | Total Return
ActionBand:
  - active:   [⏸ Pause Strategy]  (orange/failed-color)
  - paused:   [▶ Resume] (green, right-border) | [✕ Close Pos] (red)
  - inactive: closed-band with close_reason label
```

Left bar color logic:
- `active` → `var(--green)`
- `paused` → `var(--failed-color)`
- `inactive` → `var(--gray)`

Closed band CSS (from `.pc-closed-band`, `.pc-closed-lbl`):
- `background: var(--gray-a); border-top: 1px solid var(--border); padding: 6px 12px 6px 18px; display: flex; align-items: center;`
- Label: `text-transform: uppercase; font-family: JetBrains Mono; font-size: 9px; letter-spacing: .05em; font-weight: 700; color: var(--gray); margin-left: auto;`

### 3.4 Actions (API calls)

- Pause: `POST /api/strategies/:id/pause`
- Resume: `POST /api/strategies/:id/resume`
- Close positions: `POST /api/strategies/:id/close-positions`

---

## 4. Screen: Positions (`/positions`)

### 4.1 Data

Fetch from: `GET /api/positions` — returns list of positions.  
Also consider WebSocket event `position.updated` for live P&L ticks.

Expected fields:
```ts
{
  id: string;
  symbol: string;         // e.g. "BTC-USDT"
  side: 'long' | 'short';
  status: 'open' | 'stale' | 'closed';
  leverage: number;
  margin_mode: 'Cross' | 'ISO';
  strategy_name: string;
  source: string;         // e.g. "MATP Engine"
  destination: string;    // e.g. "blofin", "hyperliquid"
  opened_at: string;      // ISO
  closed_at?: string;     // ISO, if closed
  entry_price: number;
  mark_price?: number;    // null if stale/closed
  close_price?: number;   // if closed
  size: number;
  margin: number;
  realized_pnl: number;
  realized_pnl_fees: number;  // shown as secondary "(−0.42)" in P&L cell
  unrealized_pnl?: number;    // null if stale/closed
  pnl_pct: number;        // percentage of margin
  total_pnl?: number;     // if closed: final total
  total_pnl_pct?: number;
  close_reason?: string;  // e.g. "Take Profit Hit", "Stop Loss Triggered"
}
```

### 4.2 Layout

TopBar: "Positions" + count pill + ↺ Refresh button  
FilterBar: Asset ▾ · All statuses ▾ · All strategies ▾ · ✕ Clear  
SummaryBar: **Live** · **Stale** · **Closed**

Sections in order: **Live → Stale → Closed**

### 4.3 Position card anatomy

```
[left bar: long=green, short=red, stale=failed-color, closed=gray]

pc-row1:   [symbol] [LONG/SHORT pill] [Nx leverage pill (c-tech)] [Cross/ISO pill (c-tech)] [open/stale/closed status pill → margin-left:auto]
strat-row: [strategy_name tag]
pc-row1b:  [MATP Engine → destination (c-neutral pills)] . . . [Opened: time  |  Opened/Closed: time pair]
DataGrid top row:  Entry price | Size | Margin
DataGrid bot row:  Mark (green/red/stale-color) | P&L (Realized) | P&L %
ActionBand:
  - open:   [✕ Close Position] (red, full width)
  - stale:  [↺ Refresh] (blue, right-border) | [✕ Close Position] (red)
  - closed: [closed-band with close_reason label]
```

#### P&L (Realized) cell — open/stale positions

Display on one line using flexbox baseline:
```
+14.85  (−0.55)
^13px bold green/red   ^10px dim red 70% opacity
```
The secondary value `(−0.55)` represents `realized_pnl_fees` (fees or partial close impact).

For **stale** positions: Mark, P&L value, secondary value, and P&L % all use `color: var(--failed-color)`.

#### Closed position card differences

- Left bar: always `var(--gray)` regardless of side
- `pc-row1` side pill (LONG/SHORT) uses `c-closed` variant (gray)
- Bottom row shows: **Close price** | **P&L** | **P&L %** (no secondary parenthetical)
- Close price: plain `var(--text)` color — not green/red
- Append `pc-closed-band` with `close_reason`

#### Decimal precision per exchange (CRITICAL — do not round differently)

Apply these rules when formatting `entry_price`, `mark_price`, `close_price`:

| Symbol | Exchange | Price decimals | Size decimals |
|--------|----------|---------------|---------------|
| BTC-USDT | blofin / hyperliquid | 1 dp  | 3 dp |
| ETH-USDT | blofin / hyperliquid | 2 dp  | 3 dp |
| SOL-USDT | blofin               | 3 dp  | 2 dp |
| AVAX-USDT| hyperliquid          | 3 dp  | 2 dp |
| DOGE-USDT| blofin               | 5 dp  | integer (no decimals) |
| DOT-USDT | blofin               | 3 dp  | 1 dp |
| XRP-USDT | blofin               | 4 dp  | integer (no decimals) |

Implement as a utility `formatPrice(symbol, exchange, value)` and `formatSize(symbol, exchange, value)` in `dashboard-ui/src/utils/precision.ts`. This utility must be used everywhere prices and sizes are rendered — positions, orders, and any future screens.

#### Timestamp formatting

- Open positions: `Opened: Today HH:MM` or `Opened: Yesterday HH:MM`
- Closed positions: Two lines — `Opened: YY-DD-MM HH:MM` and `Closed: YY-DD-MM HH:MM`
- Font: `JetBrains Mono; font-size: 10px; font-weight: 500; color: var(--muted);`

### 4.4 Actions (API calls)

- Close position: `POST /api/positions/:id/close`
- Refresh stale: `POST /api/positions/:id/refresh` (or re-fetch mark price)

---

## 5. Screen: Orders (`/orders`)

### 5.1 Data

Fetch from: `GET /api/orders`  
Consider WebSocket event `order.updated` for status changes.

Expected fields:
```ts
{
  id: string;
  symbol: string;
  side: 'buy' | 'sell';
  leverage: number;
  status: 'filled' | 'lag-fail' | 'route-fail' | 'pending';
  strategy_name: string;
  source: string;       // e.g. "TradingView", "Telegram", "Internal"
  destination: string;  // e.g. "blofin", "hyperliquid"
  created_at: string;   // ISO
  price?: number;       // null if lag-fail
  size?: number;
  margin?: number;      // null if lag-fail
}
```

### 5.2 Layout

TopBar: "Orders" + total count pill  
FilterBar: Asset ▾ · All statuses ▾ · All strategies ▾ · ✕ Clear  
(No SummaryBar on orders — not in v0.33 design)

### 5.3 Order card anatomy

```
[left bar: buy=green, sell=red, failed=failed-color]

oc-row1:   [symbol (oc-sym)] [BUY/SELL pill (c-long/c-short)] [Nx pill (c-tech)] [status chip → margin-left:auto]
strat-row: [strategy_name tag]
oc-row1b:  [source → destination (c-neutral pills)] . . . [timestamp]
oc-row2:   Price | Size | Margin   (single row, same DataGrid styling)
oc-foot:
  - lag-fail:   [✕ Delete Log] (red, full width, no right border)
  - route-fail: [↺ Retry] (blue, right-border) | [✕ Delete] (red)
  - pending:    [✕ Cancel Order] (red, full width)
  - filled:     no footer
```

#### Order timestamp format

`YY-DD-MM HH:MM` for absolute dates, `Today HH:MM` / `Yesterday HH:MM` for recent.  
Font: `JetBrains Mono; font-size: 10px; font-weight: 500; color: var(--muted);`

#### Decimal precision

Use the same `formatPrice` / `formatSize` utility from §4.3. For `lag-fail` orders where price/margin is null, render `—` in `color: var(--text)`.

### 5.4 Actions (API calls)

- Retry: `POST /api/orders/:id/retry`
- Delete: `DELETE /api/orders/:id`
- Cancel pending: `POST /api/orders/:id/cancel`

---

## 6. Routing

The app has three top-level routes. Bottom nav is persistent across all three.

```
/strategies   →  StrategiesScreen
/positions    →  PositionsScreen
/orders       →  OrdersScreen
```

Default route: redirect `/` → `/positions`

The bottom nav dot indicator on Orders tab is driven by: any order with `status === 'lag-fail' || status === 'route-fail'`.

---

## 7. Global layout shell

The outermost layout must be a full-height flex column:

```
<div style="display:flex; flex-direction:column; height:100vh; max-width:480px; margin:0 auto; background:var(--bg2);">
  <TopBar />
  {screen-specific bars (FilterBar, SummaryBar)}
  <main style="flex:1; overflow-y:auto; padding:14px 14px 80px; scrollbar-width:none;">
    {cards}
  </main>
  <BottomNav />
</div>
```

Hide scrollbar: `scrollbar-width: none;` + `::-webkit-scrollbar { display: none; }`

---

## 8. Utility functions

Create `dashboard-ui/src/utils/`:

### `precision.ts`
```ts
// formatPrice(symbol, exchange, value) → string with correct decimal places
// formatSize(symbol, exchange, value)  → string with correct decimal places
// See §4.3 table for exact rules
```

### `datetime.ts`
```ts
// formatTimestamp(iso: string, mode: 'relative' | 'absolute') → string
// relative: "Today HH:MM" | "Yesterday HH:MM"
// absolute: "YY-DD-MM HH:MM"
```

### `pnl.ts`
```ts
// formatPnl(value: number) → string  e.g. "+6.14" | "−3.15"
// formatPct(value: number) → string  e.g. "+0.41%" | "−3.66%"
// pnlColor(value: number) → 'pos' | 'neg' | 'zero'
```

---

## 9. Implementation order

Work through screens in this order to reuse components as they are built:

1. **Tokens + shared components** (§1–§2) — no API calls, pure UI
2. **Orders screen** (§5) — simplest card structure, single data row, tests `formatPrice`/`formatSize`
3. **Positions screen** (§4) — builds on orders, adds two-row grid, P&L logic, stale/closed variants
4. **Strategies screen** (§3) — builds on positions card, adds strategy-specific pills and action states
5. **Routing + BottomNav** (§6–§7) — wire everything together

---

## 10. Do not change

- `dashboard-api/` — no backend changes unless a `// TODO: API` comment explicitly calls one out
- `docker-compose.yml` — no changes
- Any existing component not related to these three screens

---

## 11. Verification checklist

Before marking complete, verify each item visually against `matp-ui-v0.33.html`:

- [ ] All CSS variables match tokens exactly
- [ ] Left bar color correct for every card state (long/short/stale/closed/active/paused/inactive)
- [ ] `header-pill` variants all render correctly
- [ ] Status chips (`chip`) render correctly on orders
- [ ] `DataGrid` matches `pc-matrix-table` padding, borders, and background
- [ ] `ActionBand` button order correct (primary/blue left with right-border, destructive/red right)
- [ ] P&L (Realized) inline format: `+14.85 (−0.55)` on one line, baseline-aligned
- [ ] Stale position: Mark, P&L, P&L% all in `var(--failed-color)`
- [ ] Closed position: left bar gray, side pill gray, close price plain text color
- [ ] Timestamps: 10px / 500 / `var(--muted)` on both open and closed cards
- [ ] Decimal precision: BTC 1dp price/3dp size, ETH 2dp/3dp, SOL 3dp/2dp, etc.
- [ ] Bottom nav dot appears when any order has `lag-fail` or `route-fail` status
- [ ] Strategies action band: active→pause only, paused→resume+close, inactive→closed band
- [ ] `id-tag` on strategy cards has dashed border, transparent background, lighter weight
- [ ] Scrollbar hidden on all scroll containers
