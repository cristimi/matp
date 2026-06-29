# Strategy Tree — Filter + Sort Bar

**Date:** 2026-06-29  
**Branch:** main

---

## What was built

### Part 1 — Server: two timestamps on the L1 `/tree` payload

Added two correlated subqueries to `GET /strategies/tree` in `dashboard-api/src/routes/strategies.ts`:

```sql
(SELECT MAX(sp.opened_at)    FROM strategy_positions sp WHERE sp.strategy_id = s.id) AS last_position_opened_at,
(SELECT MAX(o.received_at)   FROM orders o              WHERE o.strategy_id  = s.id) AS last_activity_at
```

Also added `s.strategy_source` to the SELECT (needed for the Type filter).

Both fields + `strategy_source` are now in the response mapping. Added all three to the `StrategyTreeItem` TypeScript interface in `dashboard-ui/src/api.ts`.

**Verification — `curl /strategies/tree` (key fields):**

```
HYPE Breakout      src=ai_engine   opened_at=None                      activity=None
TV Test Harness    src=signal_engine opened_at=2026-06-29T15:38:44.799Z  activity=2026-06-29T17:15:38.657Z
TV BTC Test HL     src=tradingview  opened_at=2026-06-29T17:15:32.038Z  activity=2026-06-29T17:15:28.474Z
HYPE Test          src=tradingview  opened_at=2026-06-29T17:13:47.328Z  activity=2026-06-29T17:13:39.936Z
AI BTC             src=ai_engine    opened_at=2026-06-28T20:08:01.191Z  activity=2026-06-29T00:11:44.449Z
```

- Strategies with positions/orders have populated timestamps ✓
- Strategies with no orders (`HYPE Breakout`) correctly return `null` ✓

---

### Part 2 — Schema: migration 033

**File:** `db/migrations/033_strategy_source_social_internal.sql`

`strategy_source` is a plain `VARCHAR(20)` with **no CHECK constraint** (verified). Migration updates the column comment only:

```sql
COMMENT ON COLUMN public.strategies.strategy_source IS
  'Signal source: tradingview | ai_engine | social | internal | manual';
```

**Migration output:**
```
BEGIN
COMMENT
COMMIT
NOTICE:  Migration 033 verified OK
DO
```

**Column comment after migration:**
```
Signal source: tradingview | ai_engine | social | internal | manual
```

**Row counts before/after (no rows changed):**
```
 strategy_source | count
-----------------+-------
 ai_engine       |     2
 signal_engine   |     1
 tradingview     |     2
```

**Also updated:**
- `strategy_source` union type in `Strategies.tsx` extended to include `'social' | 'internal'`
- Filter predicate in `Strategies.tsx` handles `social` and `internal` filter keys
- Source filter `<select>` in `Strategies.tsx` now has Social / Internal / Manual options

**Auto-stamping decision:** Neither the social-copy pipeline nor any internal/deterministic engine creates strategy rows today — so auto-stamping is NOT a clean one-line change. Added backlog item to `docs/ROADMAP.md`:
> Auto-stamp `strategy_source = 'social' | 'internal'` at creation — when those pipelines gain a strategy-row creation point.

The Social and Internal filter buckets work correctly now; they simply show empty until such strategies exist.

---

### Part 3 — UI: filter + sort bar in `StrategyTree.tsx`

Added to `StrategyTreePage`:

**Filter state** (all persisted in `sessionStorage` with `matp_tree_*` keys):
- `matp_tree_symbol`  — symbol filter (`all` | any symbol string)
- `matp_tree_status`  — status filter (`all` | `active` | `inactive`)
- `matp_tree_openpos` — open positions filter (`all` | `hasopen`)
- `matp_tree_type`    — type filter (`all` | `tradingview` | `ai` | `social` | `internal`)
- `matp_tree_sortkey` — sort key (`symbol` | `last_opened` | `activity`); default: `activity`
- `matp_tree_sortdir` — sort direction (`asc` | `desc`); default: `desc`

**Controls rendered (one horizontal scrollable chip row):**
- Symbol: `<select>` with "All Symbols" + one option per distinct symbol (styled as chip)
- Status: cycling chip `All Status → Active → Inactive`
- Open pos: toggle chip `All ↔ Has Open`
- Type: cycling chip `All Type → TradingView → AI → Social → Internal`
- Sort — three chips: `Symbol`, `Opened`, `Activity`; active chip shows `↑`/`↓`; clicking inactive activates with desc; clicking active toggles direction
- Reset chip (red, shown only when any filter/sort is non-default)

**Filter logic (client-side, no re-fetch):**
- Symbol: exact match on `s.symbol`
- Status: `s.enabled` boolean
- Open positions: uses live PnL snapshot `position_ids.length > 0` when available, falls back to `s.open_positions_count > 0`
- Type: maps filterType → `strategy_source` value (`tradingview` / `ai_engine` / `social` / `internal`); `manual`/unknown only shown under "All"

**Sort logic:**
- Nulls sort last regardless of direction (explicit `nullSafeCompare` helper)
- Default: Last Activity descending

**Reset:** clears all `matp_tree_*` keys from `sessionStorage`, restores defaults.

---

## Redeploy

```
live dashboard-ui asset: index--MgwhEB4.js
✓ dashboard-ui redeployed.
```

Both `dashboard-api` and `dashboard-ui` redeployed via `./scripts/redeploy.sh`.

API health: healthy (confirmed via `docker compose logs dashboard-api` — live PnL ticks running, no errors).

UI bundle verification:
```
docker compose exec -T dashboard-ui grep -rl 'matp_tree_symbol' /usr/share/nginx/html
→ /usr/share/nginx/html/assets/index--MgwhEB4.js
```
New sessionStorage key confirmed in the deployed bundle ✓

---

## Files changed

- `db/migrations/033_strategy_source_social_internal.sql` (new)
- `dashboard-api/src/routes/strategies.ts` — `/tree` query + response mapping
- `dashboard-ui/src/api.ts` — `StrategyTreeItem` interface (3 new fields)
- `dashboard-ui/src/pages/StrategyTree.tsx` — filter/sort state + controls + filtered/sorted rendering
- `dashboard-ui/src/pages/Strategies.tsx` — `strategy_source` union type, filter predicate, select options
- `docs/ROADMAP.md` — backlog item for auto-stamp social/internal
