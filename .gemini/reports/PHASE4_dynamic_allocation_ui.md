# Phase 4 Report — dashboard-ui: Deposit/Withdraw Delta Edit + Committed Allocation Display

**Date:** 2026-06-20  
**Status:** COMPLETE — deployed, bundle verified, all key strings confirmed

---

## Changes

### `dashboard-ui/src/pages/Strategies.tsx` and `dashboard-ui/src/api.ts`

#### 4a. `Strategy` interface — new fields

Added to both `Strategies.tsx` (local interface) and `api.ts` (shared type):

```typescript
initial_allocation?: number;   // committed capital (seed + net deposits)
allocation_peak?: number;      // high-water mark (informational; used by listener guard)
```

#### 4b. Strategy card — "Committed" cell replaces "Spare (—)"

The data grid previously had a "Spare" cell showing `—`. It now shows **Committed** = `initial_allocation` (falling back to `capital_allocation` if the field is absent):

```typescript
const committed = Number(strategy.initial_allocation ?? strategy.capital_allocation ?? 0);
```

The "Allocated" label was renamed to **"Allocation"** to signal it is the live compounding balance, not the static seed. The card now shows:

| Top row | Positions | Win Rate | Allocation (live) |
|---------|-----------|----------|-------------------|
| **Bottom row** | **Committed (seed)** | **P&L (Realized)** | **Total Return** |

#### 4c. Edit modal — deposit/withdraw delta input

`StrategyCommonFields` now branches on `originalCapitalAllocation !== undefined` (edit mode vs. create mode):

**Edit mode** (originalCapitalAllocation defined):
- Shows a signed **Deposit / Withdraw ($)** input bound to `form.allocation_delta`.
- Live preview: when delta ≠ 0, shows `New allocation: $X.XX` in blue.
- Guidance text: *"Deposits/withdrawals shift the high-water mark by the same amount; they do not reset the drawdown."*
- Old warning ("Changing capital allocation will reset the drawdown anchor") removed.

**Create mode** (originalCapitalAllocation undefined): unchanged — absolute capital seed input.

#### 4d. Edit submit — send delta, drop absolute, remove toast

Both the TV and AI edit PUT bodies now send:
```typescript
allocation_delta: parseFloat(editForm.allocation_delta ?? '0')
```
`capital_allocation` removed from both bodies. The API (Phase 3) ignores unknown fields anyway, but the explicit removal keeps intent clear.

Validation change: removed the `capital_allocation > 0` check (meaningless now that the field is read-only in edit mode); `margin_per_trade > 0` check retained.

The `drawdown_anchor_reset` toast was removed — that response field no longer exists.

---

## Verification output (in-bundle grep on served asset)

```
Asset: index-9vdKO14B.js  (new hash — fresh build confirmed)

allocation_delta    → 9 occurrences  ✓  (present in submit bodies + delta input binding)
Committed           → 1              ✓  (card cell label)
Deposit / Withdraw  → 1              ✓  (edit modal input label)
high-water mark     → 1              ✓  (guidance text)
drawdown_anchor_reset → 0            ✓  (fully removed)
```

---

## Notes

- The create form (`addForm`) continues to use `capital_allocation` as an absolute seed — no change needed there since POST create still accepts `capital_allocation` directly.
- `allocation_peak` is surfaced in the interface but not yet displayed on the card; it is available for future use (e.g. a drawdown progress bar).
