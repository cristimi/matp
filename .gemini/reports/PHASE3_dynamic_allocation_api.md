# Phase 3 Report — dashboard-api: Delta Allocation, Honest Return Denominator, Peak Re-anchor

**Date:** 2026-06-20  
**Status:** COMPLETE — deployed, container healthy, all verification checks pass

---

## Changes

### `dashboard-api/src/routes/strategies.ts`

#### 3a. `getAllocatedOnAccount` — committed capital, not compounding balance

Fund-cap checks must reflect real deposited capital, not paper profits.

```diff
- SELECT COALESCE(SUM(capital_allocation), 0) AS total
+ SELECT COALESCE(SUM(COALESCE(initial_allocation, capital_allocation)), 0) AS total
```

#### 3b. GET `total_return` — honest denominator

`total_return` now divides by committed seed (`initial_allocation`), not the live compounding balance. Without this, a strategy that has grown its balance would show a lower return % on the next trade.

```diff
- WHEN COALESCE(s.capital_allocation, 0) = 0 THEN 0::float
+ WHEN COALESCE(s.initial_allocation, s.capital_allocation, 0) = 0 THEN 0::float
  ...
- NULLIF(s.capital_allocation, 0)::numeric * 100
+ NULLIF(COALESCE(s.initial_allocation, s.capital_allocation), 0)::numeric * 100
```

`s.*` is already selected so `initial_allocation` and `allocation_peak` surface to the UI with no extra column listing.

#### 3c. POST create — seed new columns from the seed value

```diff
  capital_allocation, initial_allocation, allocation_peak,
  ...
  $12, $12, $12,   -- all three seeded from the same capital_allocation value
```

Creation remains an absolute seed value (the "enter a total" path); only PUT edits become a delta.

#### 3d. PUT — absolute `capital_allocation` replaced by signed `allocation_delta`

- Destructure: `capital_allocation` removed; `allocation_delta` added.
- `allocationChanging = allocation_delta !== undefined && Number(allocation_delta) !== 0`
- Pre-flight fetch: reads `capital_allocation`, `margin_per_trade`, `initial_allocation`, `account_id` from the row.
- **Floor check**: rejects 422 if `capital_allocation + delta < margin_per_trade`.
- **Deposit cap** (delta > 0 only): `alreadyCommitted + (initial_allocation + delta) > availableFunds` → 422.
- SQL: all three allocation columns shift by the delta; `drawdown_anchor_pnl` reset line removed.
- RETURNING: adds `initial_allocation`, `allocation_peak`; removes `drawdown_anchor_pnl`.
- Response: `allocation_delta_applied` (number) replaces `drawdown_anchor_reset` (boolean).

```sql
capital_allocation = capital_allocation + COALESCE($13, 0),
initial_allocation = initial_allocation + COALESCE($13, 0),
allocation_peak    = allocation_peak    + COALESCE($13, 0),
```

`margin_per_trade` and `max_drawdown_pct` remain ordinary absolute edits — only allocation is a delta.

#### 3e. `/:id/start` and `/:id/enable` — re-anchor peak on re-enable

Both handlers now include:

```sql
allocation_peak = CASE WHEN enabled = false THEN capital_allocation ELSE allocation_peak END
```

A redundant enable on an already-enabled strategy leaves the peak untouched. Re-enabling after an auto-disable gives the strategy a fresh runway from current balance.

---

## Verification output

```
GET /api/dashboard/strategies:
  tv-btc-test-hl-94e1  capital_allocation=102.30  initial_allocation=100  allocation_peak=102.30  total_return=2.3%
  hype-test-7db4       capital_allocation=200      initial_allocation=200  allocation_peak=200     total_return=0%
  ai-btc-6f8c          capital_allocation=100      initial_allocation=100  allocation_peak=100     total_return=0%

tv-btc-test-hl-94e1 shows live compounding already active from Phase 2:
  capital_allocation and allocation_peak at 102.30 (PnL compounded in),
  initial_allocation unchanged at 100 (committed seed),
  total_return=2.3% = 2.30/100 * 100 — correct denominator. ✓

PUT {"allocation_delta": 50}  → cap=250, initial=250, peak=250, delta_applied=50   ✓
PUT {"allocation_delta": -50} → cap=200, initial=200, peak=200, delta_applied=-50  ✓  (round-trip)
DB: capital_allocation = initial_allocation = allocation_peak = 200                ✓

PUT {"allocation_delta": -196}  (would leave 4, below margin_per_trade=20)
→ 422: "Withdrawal would drop allocation below margin_per_trade ($20.00)."         ✓
```

---

## Notes

- `drawdown_anchor_pnl` is fully removed from all API logic; the column remains in the schema (drop deferred).
- The POST create fund-cap check (`alreadyAlloc + newAlloc > availableFunds`) continues to use `capital_allocation` from the body (the initial seed), which is correct since `initial_allocation` does not exist yet at creation time.
