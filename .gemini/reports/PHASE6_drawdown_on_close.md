# Phase 6 Report — Auto-Disable on the Breaching Close

**Date:** 2026-06-20  
**Status:** COMPLETE — deployed, verified, ROADMAP updated

---

## Motivation

Phases 1–5 implemented Guard 5 as an open-time check: the drawdown stop fired
when the *next* open signal arrived. A position could close below the floor, leaving
the strategy enabled but broken until the next signal. Phase 6 closes that gap:
`_disable_if_drawdown_breached` runs immediately after every close-path UPDATE.

---

## Changes — `order-listener/app/webhook_handler.py`

### New module-level helpers (added after `_verify_token`)

```python
def _is_drawdown_breached(cap_alloc: float, peak: float, max_dd_pct: float) -> bool:
    """Single source of truth for the high-water drawdown stop."""
    if peak <= 0:
        return False
    floor = peak * (1.0 - max_dd_pct / 100.0)
    return cap_alloc <= floor

async def _disable_if_drawdown_breached(pool, strategy_id: str) -> None:
    """After a close, auto-disable if breached. Fires on close not next signal."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled, capital_allocation, allocation_peak, max_drawdown_pct "
                "FROM strategies WHERE id = $1",
                strategy_id,
            )
            if not row or not row["enabled"]:
                return
            _cap  = float(row["capital_allocation"] or 0)
            _peak = float(row["allocation_peak"] or _cap)
            _dd   = float(row["max_drawdown_pct"] or 50)
            if _is_drawdown_breached(_cap, _peak, _dd):
                _floor = _peak * (1.0 - _dd / 100.0)
                await conn.execute(
                    "UPDATE strategies SET enabled = false, updated_at = NOW() WHERE id = $1",
                    strategy_id,
                )
                logger.warning(
                    f"DRAWDOWN STOP (on close) strategy={strategy_id}: "
                    f"alloc={_cap:.2f} peak={_peak:.2f} floor={_floor:.2f} — auto-disabled"
                )
    except Exception as _e:
        logger.error(f"drawdown-on-close check failed for {strategy_id}: {_e}")
```

### Guard 5 refactored to share `_is_drawdown_breached`

The open-time Guard 5 inline condition was replaced:

```python
# Before:
if _peak > 0 and _cap_alloc <= _floor:

# After:
if _is_drawdown_breached(_cap_alloc, _peak, _max_dd_pct):
```

### `_disable_if_drawdown_breached` call sites

Called after each of the three close-path debug lines:

| Line | Handler | Indent |
|------|---------|--------|
| ~1043 | flat signal handler | 20 spaces |
| ~1111 | close_long/short handler | 20 spaces |
| ~1233 | executor result handler | 16 spaces |

---

## Grep verification (in-container)

```
=== _is_drawdown_breached call sites (excl def) ===
114:            if _is_drawdown_breached(_cap, _peak, _dd):
593:        if _is_drawdown_breached(_cap_alloc, _peak, _max_dd_pct):

=== _disable_if_drawdown_breached call sites (excl def) ===
1043:                    await _disable_if_drawdown_breached(pool, strategy['id'])
1111:                    await _disable_if_drawdown_breached(pool, strategy['id'])
1233:                await _disable_if_drawdown_breached(pool, strategy['id'])
```

- `_is_drawdown_breached`: 2 call sites (line 114 = inside helper; line 593 = Guard 5) ✓
- `_disable_if_drawdown_breached`: 3 call sites (all three close paths) ✓

---

## Behavioural verification

Direct helper exercise against a pre-breached row (`hype-test-7db4`):

```bash
docker compose exec order-listener python -c "
import asyncio, asyncpg, os
from app.webhook_handler import _disable_if_drawdown_breached

async def main():
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])
    await pool.execute(\"UPDATE strategies SET allocation_peak=100000, max_drawdown_pct=50, capital_allocation=1, enabled=true WHERE id='hype-test-7db4'\")
    row_before = await pool.fetchrow(\"SELECT enabled, capital_allocation, allocation_peak, max_drawdown_pct FROM strategies WHERE id='hype-test-7db4'\")
    print('BEFORE:', dict(row_before))
    await _disable_if_drawdown_breached(pool, 'hype-test-7db4')
    row_after = await pool.fetchrow(\"SELECT enabled, capital_allocation, allocation_peak FROM strategies WHERE id='hype-test-7db4'\")
    print('AFTER: ', dict(row_after))
    await pool.execute(\"UPDATE strategies SET allocation_peak=200, max_drawdown_pct=75, capital_allocation=200, enabled=true WHERE id='hype-test-7db4'\")
    print('RESTORED')

asyncio.run(main())
"
```

Output:
```
DRAWDOWN STOP (on close) strategy=hype-test-7db4: alloc=1.00 peak=100000.00 floor=50000.00 — auto-disabled
BEFORE: {'enabled': True, 'capital_allocation': Decimal('1'), 'allocation_peak': Decimal('1.0E+5'), 'max_drawdown_pct': Decimal('50')}
AFTER:  {'enabled': False, 'capital_allocation': Decimal('1'), 'allocation_peak': Decimal('1.0E+5')}
RESTORED
```

- Helper imported from live container ✓
- Pre-breach state set: alloc=1, peak=100000, floor=50000 → 1 ≤ 50000 = breached ✓
- `DRAWDOWN STOP (on close)` warning logged with correct alloc/peak/floor values ✓
- `enabled` flipped to `false` ✓
- `capital_allocation` and `allocation_peak` unchanged by disable ✓
- Strategy restored to baseline ✓

---

## ROADMAP.md update

Under the completed dynamic-allocation entry, added:

> **Phase 6 (2026-06-20):** the high-water drawdown stop now also fires on the
> breaching close (immediate auto-disable via `_disable_if_drawdown_breached`), not
> only on the next open signal. Open-time Guard 5 is retained as the backstop. Both
> paths share the `_is_drawdown_breached` pure helper (single source of truth).

---

## Architecture summary

```
on every OPEN signal
  → Guard 5 (_is_drawdown_breached)  →  429 + auto-disable if floor breached

on every CLOSE (all three paths)
  → _disable_if_drawdown_breached    →  auto-disable if floor breached
  (no 429 needed; close already completed)
```

Both paths share `_is_drawdown_breached` — one formula, no drift.
