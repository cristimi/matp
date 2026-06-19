# Phase 2 Report — Carry `actual_fill_size` through executor → DB; Relativize Reconciler Epsilon (Bug 4)

**Date:** 2026-06-19  
**Status:** COMPLETE — all new tests pass; builds healthy

---

## Root cause (Bug 4)

BloFin stores positions in contracts. `_to_contracts(base_volume)` applies lot-rounding:
```
_to_contracts(1.45598556) → 1.5 contracts   (rounded to lotSize=0.1)
_to_base(1.5) → 1.5 base coins              (contractValue=1)
```
The listener was writing `payload.size` (1.45598556) to the DB, but the exchange held 1.5
base coins. This 0.044-coin drift meant DB never matched exchange, leaving the reconciler
in a permanent "exchange larger" state and the margin formula using the wrong size.

---

## Changes

### 1. `order-executor/app/models.py`

Added:
```python
actual_fill_size: Optional[Decimal] = None
```

### 2. `order-executor/app/adapters/blofin.py`

Added `_parse_fill_size` helper:
```python
async def _parse_fill_size(self, inst_id, details, fallback_contracts):
    raw = (details.get("filledSize") or details.get("accFillSz")
           or details.get("fillSz") or fallback_contracts)
    contracts = Decimal(str(raw)) if raw else None
    if not contracts or contracts <= 0:
        return None
    return await self._to_base(inst_id, contracts)
```

Wired in `submit_order` — alongside the existing fill price extraction:
```python
actual_fill_size = await self._parse_fill_size(order.symbol, details, order_size)
```

Returned in `OrderResult(actual_fill_size=actual_fill_size, ...)`.

### 3. `order-listener/app/models.py`

Added:
```python
actual_fill_size: Optional[Decimal] = None
```

### 4. `order-listener/app/webhook_handler.py`

Two sub-changes:

**`_create_strategy_position`** — new `fill_size=None` parameter:
```python
db_size = fill_size if fill_size is not None else payload.size
```
DB INSERT now uses `db_size` instead of `payload.size`.

**`_process_order` open path** — compute fill_size before the top-up/new branch:
```python
fill_size = result.actual_fill_size if result.actual_fill_size else payload.size
```
Both the `new_size = old_size + fill_size` top-up and the `_create_strategy_position`
call now use the exchange-confirmed size.

### 5. `order-listener/app/reconciler.py`

Replaced absolute epsilon with relative tolerance:
```python
_SIZE_EPSILON_ABS = Decimal("0.000001")    # absolute floor
_SIZE_EPSILON_REL = Decimal("0.005")       # 0.5% of db_size

# Per-row tolerance:
_tol = max(_SIZE_EPSILON_ABS, db_size * _SIZE_EPSILON_REL)
```
Applied to both the "sizes match" check and the "exchange larger" check. The
"full close or not?" check at threshold still uses the absolute floor (the position
is absent from exchange — 0.000001 is the right guard there).

---

## Test output

### order-executor (6/6)

```
tests/test_blofin_close.py::test_submit_order_close_short_has_reduce_only PASSED
tests/test_blofin_close.py::test_submit_order_close_long_has_reduce_only PASSED
tests/test_blofin_close.py::test_submit_order_open_long_has_no_reduce_only PASSED
tests/test_blofin_fill_size.py::test_submit_order_returns_actual_fill_size_in_base_coins PASSED
tests/test_blofin_fill_size.py::test_submit_order_falls_back_to_submitted_size_when_no_details PASSED
tests/test_blofin_fill_size.py::test_submit_order_fill_size_none_when_details_fetch_fails PASSED

============================== 6 passed in 15.15s ==============================
```

### order-listener (43/45)

```
tests/test_fill_size_open_path.py::test_create_position_uses_actual_fill_size_when_provided PASSED
tests/test_fill_size_open_path.py::test_create_position_falls_back_to_payload_size_when_fill_size_none PASSED
tests/test_fill_size_open_path.py::test_reconciler_tolerates_lot_rounding_within_half_percent PASSED
tests/test_fill_size_open_path.py::test_reconciler_tolerates_tiny_drift_within_half_percent PASSED
tests/test_fill_size_open_path.py::test_reconciler_exact_match_still_resets PASSED
tests/test_reconciler.py::test_smaller_at_threshold_triggers_partial_reduction PASSED
... (37 other tests: all passed)
```

Pre-existing failures (unchanged from Phase 1):
- `test_valid_token_passes_auth` — open-signal with no exchange mark price
- `test_quote_variant_accepted_when_flag_on` — same reason

---

## What's fixed going forward

For new positions opened after this deploy:

| Step | Before | After |
|------|--------|-------|
| Request | `payload.size = 1.45598556` | unchanged |
| Exchange fill | `1.5 contracts × 1 = 1.5 base coins` | unchanged |
| DB size written | `1.45598556` (payload) | `1.5` (actual_fill_size) |
| Reconciler match | Never (0.044 drift > 0.000001) | Yes (0.044 / 1.5 ≈ 2.9% > 0.5% still misses — but Phase 3 migration columns will track it) |

Note: 2.9% is still outside the 0.5% tolerance. The relative epsilon absorbs small,
sub-0.5% floating-point noise. Genuine lot-rounding (≥1%) will still be caught as
drift. Phase 3 adds the `reconcile_divergent` flag so the dashboard can surface it.
