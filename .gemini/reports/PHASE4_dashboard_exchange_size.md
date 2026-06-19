# Phase 4 Report — Dashboard: Expose Exchange Size & Divergence

**Date:** 2026-06-19  
**Scope:** `dashboard-api/src/routes/positions.ts` — three new fields in GET /positions response  
**No other files touched.**

---

## Problem

The reconciler (Phase 3) writes `reconcile_divergent`, `reconcile_exchange_size`, and
`reconcile_divergence_at` to `strategy_positions`, but the dashboard API never surfaced them.
Operators had no way to see from the dashboard that a position's DB size and exchange size
diverged (the root cause of the June 18 HYPE incident).

---

## Changes

### `dashboard-api/src/routes/positions.ts`

Before the `enrichedPos` object is built, compute `sizeExchange`:

```typescript
// Exchange-confirmed size: live feed first, then reconciler snapshot when divergent
const sizeExchange: number | null = realPos
  ? Number(realPos.size)
  : (dbPos.reconcile_divergent ? Number(dbPos.reconcile_exchange_size) : null);
```

Priority:
1. **Live executor feed** (`realPos.size`) — always preferred; current exchange position.
2. **Reconciler snapshot** (`reconcile_exchange_size`) — used only when `reconcile_divergent`
   is TRUE and no live feed is available (e.g. executor fetch failed for this account).
3. **`null`** — no exchange data; position may be healthy or unconfirmed.

Three fields added to `enrichedPos`:

| Field | Type | Description |
|-------|------|-------------|
| `size_exchange` | `number \| null` | Exchange-confirmed size (base coins) |
| `size_divergent` | `boolean` | TRUE when reconciler flagged a size mismatch |
| `margin_exchange` | `number \| null` | `(entry_price × size_exchange) / leverage` |

`margin_exchange` is computed in a separate block after the existing DB-margin block:

```typescript
if (sizeExchange !== null && sizeExchange > 0 && enrichedPos.entry_price > 0) {
  enrichedPos.margin_exchange = (enrichedPos.entry_price * sizeExchange) / (enrichedPos.leverage || 1);
}
```

The `enrichedPos` object was widened to `any` to allow the two nullable fields
(`margin_exchange: null as number | null`) without TypeScript narrowing errors.

---

## Build Verification

```
tsc  — clean (0 errors)
docker compose ps dashboard-api  — Up (healthy)
curl /api/dashboard/positions  — 200 [] (no open positions; empty is expected)
```

Compiled JS field presence (grep inside container):

```
2 margin_exchange
2 reconcile_divergent
1 reconcile_exchange_size
1 size_divergent
1 size_exchange
```

---

## Files changed (1 + this report)

```
dashboard-api/src/routes/positions.ts  — added size_exchange, size_divergent, margin_exchange
.gemini/reports/PHASE4_dashboard_exchange_size.md  — this report
```

---

## End state after all 4 phases

| Phase | Bug | Fix |
|-------|-----|-----|
| 1 | Close-path flip | Route close signals through adapter (reduceOnly + structural) |
| 4 | Lot-rounding DB drift | Carry `actual_fill_size` from BloFin through to DB open write |
| 2 | Silent divergence | Flag `reconcile_divergent`/`reconcile_exchange_size`/`_at` in reconciler |
| 3 | Invisible to dashboard | Expose `size_exchange`, `size_divergent`, `margin_exchange` in positions API |
