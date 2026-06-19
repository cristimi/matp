# Follow-up A Report — actual_fill_size: HL fills + BloFin limit orders (Bug 4 completion)

**Date:** 2026-06-19  
**Scope:** `order-executor/app/adapters/hyperliquid.py`, `order-executor/app/adapters/blofin.py`,
two test files. No other files touched.

---

## Problem

The open-path listener stores `result.actual_fill_size` when set, else falls back to
`payload.size`. Two gaps remained after Phase 2:

| Gap | Effect |
|-----|--------|
| HL `_place_order` never set `actual_fill_size` | DB always stored TV payload size, ignoring HL rounding |
| BloFin `submit_order` only set `actual_fill_size` for `market` orders | Limit orders stored `None` → fallback to payload size |

---

## A1 — Hyperliquid (`hyperliquid.py`)

Inserted immediately before the final `return OrderResult(...)` in `_place_order`:

```python
ts = filled.get("totalSz")
actual_fill_size = (
    Decimal(str(ts)) if ts not in (None, "", "0")
    else Decimal(str(size_rounded))
)
```

Added `actual_fill_size=actual_fill_size` to `OrderResult(...)`.

**Priority:** `filled.totalSz` (exchange-confirmed total filled base size) → fallback to
`size_rounded` (the `_round_size`-quantised submitted size, which is what HL actually registered).

Scope: opening fills only. `close_position` is not modified (it uses realized_pnl, not stored size).

---

## A2 — BloFin (`blofin.py`)

Added `else` branch on the `if order.order_type == "market":` block:

```python
else:
    # No immediate fill to query (limit/resting). Store the rounded
    # submitted size so the DB matches what the exchange will hold.
    actual_fill_size = await self._to_base(
        order.symbol, Decimal(str(order_size))
    )
```

`order_size` is the lot-rounded contract string already computed earlier; `_to_base` converts
contracts → base coins, matching the contract spec on the exchange.

---

## A3 — Tests

### pytest output

```
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
asyncio: mode=Mode.STRICT
collected 10 items

tests/test_blofin_close.py::test_submit_order_close_short_has_reduce_only PASSED [ 10%]
tests/test_blofin_close.py::test_submit_order_close_long_has_reduce_only PASSED [ 20%]
tests/test_blofin_close.py::test_submit_order_open_long_has_no_reduce_only PASSED [ 30%]
tests/test_blofin_fill_size.py::test_submit_order_returns_actual_fill_size_in_base_coins PASSED [ 40%]
tests/test_blofin_fill_size.py::test_submit_order_falls_back_to_submitted_size_when_no_details PASSED [ 50%]
tests/test_blofin_fill_size.py::test_submit_order_fill_size_none_when_details_fetch_fails PASSED [ 60%]
tests/test_blofin_fill_size.py::test_limit_order_returns_actual_fill_size PASSED [ 70%]
tests/test_blofin_fill_size.py::test_market_order_still_uses_details_fetch PASSED [ 80%]
tests/test_hyperliquid_fill_size.py::test_hl_fill_size_from_total_sz PASSED [ 90%]
tests/test_hyperliquid_fill_size.py::test_hl_fill_size_fallback_to_rounded_size PASSED [100%]

============================= 10 passed in 18.30s ==============================
```

### grep in container

```
/app/app/adapters/hyperliquid.py:494:        actual_fill_size = (
/app/app/adapters/hyperliquid.py:504:            actual_fill_size=actual_fill_size,
/app/app/adapters/blofin.py:280:                actual_fill_size  = None
/app/app/adapters/blofin.py:288:                            actual_fill_size  = await self._parse_fill_size(
/app/app/adapters/blofin.py:298:                    actual_fill_size = await self._to_base(
/app/app/adapters/blofin.py:308:                    actual_fill_size=actual_fill_size,
```

---

## New test coverage

| Test file | Test | What it verifies |
|-----------|------|-----------------|
| `test_hyperliquid_fill_size.py` | `test_hl_fill_size_from_total_sz` | `filled.totalSz` used as `actual_fill_size` |
| `test_hyperliquid_fill_size.py` | `test_hl_fill_size_fallback_to_rounded_size` | Missing `totalSz` falls back to `_round_size` result |
| `test_blofin_fill_size.py` | `test_limit_order_returns_actual_fill_size` | Limit order sets `actual_fill_size = _to_base(order_size)` |
| `test_blofin_fill_size.py` | `test_market_order_still_uses_details_fetch` | Market path still uses `filledSize` from details (regression) |

---

## Files changed

```
order-executor/app/adapters/hyperliquid.py   — added actual_fill_size from filled.totalSz
order-executor/app/adapters/blofin.py        — added else branch for limit orders
order-executor/tests/test_hyperliquid_fill_size.py  — new (2 tests)
order-executor/tests/test_blofin_fill_size.py       — extended (2 new tests, 4 → 5 total blofin + 1 regression)
.gemini/reports/FOLLOWUP_A_fill_size.md      — this report
```
