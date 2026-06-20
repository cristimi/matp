# Phase 2 Report — order-listener High-Water Drawdown + Compounding UPDATEs

**Date:** 2026-06-20  
**Status:** COMPLETE — deployed, container healthy, all grep checks pass

---

## Changes

### `order-listener/app/webhook_handler.py`

#### 2a. Guard 5 — replaced doubled block with single high-water stop

The old Guard 5 was pasted identically twice (lines 545–573 and 575–603) and used a `pnl_total − drawdown_anchor_pnl` delta model.

Both copies removed. Replaced with one block using the peak-based model:

```python
# Guard 5: High-water drawdown stop (opening signals only).
# Trip when the live compounding allocation falls max_drawdown_pct below its peak.
if payload.signal in ("open_long", "open_short"):
    _cap_alloc  = float(strategy.get("capital_allocation") or 0)
    _peak       = float(strategy.get("allocation_peak") or _cap_alloc)
    _max_dd_pct = float(strategy.get("max_drawdown_pct") or 50)
    _floor      = _peak * (1.0 - _max_dd_pct / 100.0)
    if _peak > 0 and _cap_alloc <= _floor:
        ...auto-disable + 429...
```

- Reads `capital_allocation` and `allocation_peak` directly from the strategy dict (already present via `SELECT *` in `_get_strategy` after Phase 1).
- No reference to `pnl_total`, `drawdown_anchor_pnl`, or the old loss-limit arithmetic.

#### 2b. Three close-path UPDATEs — compounding + peak ratchet

All three `UPDATE strategies SET pnl_today/pnl_total` sites (flat-signal handler, close_long/close_short handler, executor-result handler) now also compound the balance and ratchet the peak:

```sql
SET pnl_today          = pnl_today + $1,
    pnl_total          = pnl_total + $1,
    capital_allocation = capital_allocation + $1,
    allocation_peak    = GREATEST(COALESCE(allocation_peak, capital_allocation),
                                  capital_allocation + $1),
    updated_at         = NOW()
WHERE id = $2
```

`capital_allocation + $1` on the `GREATEST` line is correct: Postgres evaluates all SET expressions against the pre-update row, so this equals the new allocation. Parameter list unchanged (`$1` = realized pnl, `$2` = strategy id).

---

## Verification output (in-container grep)

```
docker compose exec order-listener grep -c "High-water drawdown stop" /app/app/webhook_handler.py
1

docker compose exec order-listener grep -c "capital_allocation = capital_allocation + \$1" /app/app/webhook_handler.py
3

docker compose exec order-listener grep -c "drawdown_anchor_pnl" /app/app/webhook_handler.py
0
```

Container status: `Up (healthy)` ✓

---

## Notes

- The `_get_strategy` helper uses `SELECT *`, so `allocation_peak` and `initial_allocation` are available in the `strategy` dict with no query changes.
- `drawdown_anchor_pnl` is fully removed from all logic; the column remains in the schema (drop deferred).
