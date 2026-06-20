# Phase 5 Report — Behavioural Verification + Roadmap

**Date:** 2026-06-20  
**Status:** COMPLETE — all end-to-end checks passed, ROADMAP updated

---

## 5a. End-to-end behavioural checks

**Test strategy:** `hype-test-7db4`  
**Baseline:** `capital_allocation=200, initial_allocation=200, allocation_peak=200, max_drawdown_pct=75`

### Step 1 — Winning close (+100 realized)

```sql
UPDATE strategies
   SET pnl_total          = pnl_total + 100,
       capital_allocation = capital_allocation + 100,
       allocation_peak    = GREATEST(allocation_peak, capital_allocation + 100)
 WHERE id = 'hype-test-7db4';
```

Result:
```
 capital_allocation | initial_allocation | allocation_peak
--------------------+--------------------+-----------------
                300 |                200 |             300
```

- `capital_allocation` +100 ✓
- `allocation_peak` ratcheted up to 300 ✓
- `initial_allocation` unchanged at 200 ✓

### Step 2 — Losing closes (−210 then −20; total −230)

Two sequential simulated closes drive allocation below the drawdown floor.

After −210:
```
 capital_allocation | initial_allocation | allocation_peak
--------------------+--------------------+-----------------
                 90 |                200 |             300
```
Peak holds at 300 (GREATEST(300, 90) = 300). Allocation 90 > floor 75 → not yet breached.

After −20:
```
 capital_allocation | initial_allocation | allocation_peak
--------------------+--------------------+-----------------
                 70 |                200 |             300
```

Condition check:
```
 capital_allocation | allocation_peak | max_drawdown_pct | floor | breached
--------------------+-----------------+------------------+-------+---------
                 70 |             300 |               75 | 75.00 | t
```
`70 <= 300 × (1 − 0.75) = 75` → **breached** ✓

### Step 3 — Webhook open_long fires drawdown stop (429)

```bash
POST /webhook/hype-test-7db4
{"base_asset":"HYPE","quote_asset":"USDT","side":"buy","signal":"open_long",
 "order_type":"market","size":"0.001","timestamp":...,"token":"<secret>"}
```

Response — HTTP 429:
```json
{"detail":"Drawdown stop hit for strategy hype-test-7db4: allocation $70.00 <= floor $75.00 (75% below peak $300.00). Strategy auto-disabled."}
```
✓ Guard 5 high-water logic fires correctly.

### Step 4 — Strategy auto-disabled

```
 enabled | capital_allocation | allocation_peak
---------+--------------------+-----------------
 f       |                 70 |             300
```
`enabled = false` — auto-disabled by the guard ✓

### Step 5 — Re-enable via `/start` re-anchors peak

```bash
POST /api/dashboard/strategies/hype-test-7db4/start
→ {"started":"hype-test-7db4","enabled":true}
```

Post-enable DB state:
```
 enabled | capital_allocation | initial_allocation | allocation_peak
---------+--------------------+--------------------+-----------------
 t       |                 70 |                200 |              70
```
`allocation_peak` re-anchored to `capital_allocation=70` (was 300) — fresh runway, not performance ✓  
`initial_allocation` unchanged at 200 ✓

### Restore

Strategy restored to `capital_allocation=initial_allocation=allocation_peak=200, pnl_total=0`.

---

## 5b. Roadmap update

`docs/ROADMAP.md` updated:

- **Deferred Backlog item 3** ("Dynamic strategy allocation") marked **COMPLETE** with implementation summary:
  - `capital_allocation` compounds on close
  - `initial_allocation` = committed seed (total_return denominator)
  - `allocation_peak` = high-water mark (drawdown reference)
  - Guard 5 replaced with peak-based model
  - Deposit/withdraw via `allocation_delta`
  - UI shows Allocation (live) + Committed (seed)
  - `drawdown_anchor_pnl` retired

- **Known Issues Fixed** table: new row added (2026-06-20) documenting the static allocation bug and doubled Guard 5 fix.

- **Tester parity note** added: strategy-tester backtests still use the static `capital_allocation` seed; compounding parity is a separate accepted backlog item.

---

## Full feature summary (all 5 phases)

| Phase | Service | Key change |
|-------|---------|-----------|
| 1 | PostgreSQL | Migration 023: `initial_allocation`, `allocation_peak` columns; init.sql regenerated |
| 2 | order-listener | Guard 5 → peak-based high-water model; three close-path UPDATEs compound balance + ratchet peak |
| 3 | dashboard-api | `getAllocatedOnAccount` uses committed capital; `total_return` denominator fixed; POST seeds new columns; PUT → `allocation_delta`; start/enable re-anchor peak |
| 4 | dashboard-ui | Delta deposit/withdraw edit modal; "Committed" cell on card; `initial_allocation`/`allocation_peak` in interface |
| 5 | Verification + docs | All checks passed; ROADMAP marked complete |
