# Reconciler Unit Tests Report — Parts 1–4 Regression Armor

**Date:** 2026-06-13
**New file:** `order-listener/tests/test_reconciler.py`
**Run location:** inside `matp-order-listener-1` container (Python 3.12, pytest 9.0.3)
**Production files modified:** none

---

## pytest -v output

```
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.0.3, pluggy-1.6.0 -- /usr/local/bin/python
cachedir: .pytest_cache
rootdir: /app
plugins: asyncio-1.4.0, anyio-4.5.0
asyncio: mode=Mode.STRICT

tests/test_reconciler.py::test_unknown_read_skips_account_no_increment_no_close PASSED [ 12%]
tests/test_reconciler.py::test_unknown_read_does_not_advance_seeded_counter      PASSED [ 25%]
tests/test_reconciler.py::test_present_equal_resets_counter_no_close             PASSED [ 37%]
tests/test_reconciler.py::test_present_larger_resets_counter_no_close            PASSED [ 50%]
tests/test_reconciler.py::test_absent_below_threshold_increments_no_close        PASSED [ 62%]
tests/test_reconciler.py::test_absent_at_threshold_triggers_full_close_scoped_by_opened_at PASSED [ 75%]
tests/test_reconciler.py::test_smaller_at_threshold_triggers_partial_reduction   PASSED [ 87%]
tests/test_reconciler.py::test_unknown_account_isolated_from_healthy_account     PASSED [100%]

======================== 8 passed, 2 warnings in 3.95s =========================
```

(Warnings are pre-existing pydantic/starlette deprecations, unrelated to this test file.)

---

## Test coverage

| Test | Fix | Assertion |
|------|-----|-----------|
| `test_unknown_read_skips_account_no_increment_no_close` | Part 1 | `gap_ret=None` → zero DB writes, `close` not awaited |
| `test_unknown_read_does_not_advance_seeded_counter` | Part 1 | counter at threshold−1, UNKNOWN → still zero writes |
| `test_present_equal_resets_counter_no_close` | Part 2 | exact-match → `reconcile_miss_count=0`, no close |
| `test_present_larger_resets_counter_no_close` | Part 2 | exchange>db → reset (the ratchet fix), no close |
| `test_absent_below_threshold_increments_no_close` | Inc. | empty list → increment written, no close |
| `test_absent_at_threshold_triggers_full_close_scoped_by_opened_at` | Inc. + Part 4 | threshold reached → close called; `gph.await_args.args[2] == OPENED_AT` |
| `test_smaller_at_threshold_triggers_partial_reduction` | Inc. | exchange smaller at threshold → `close(skip_exchange=True)` |
| `test_unknown_account_isolated_from_healthy_account` | Part 1 | two accounts; UNKNOWN acct leaves no writes; healthy acct resets |

---

## Test adjustments

None. The test file ran green as written on first attempt. No production code was changed.

---

## How it was run

```bash
docker compose cp order-listener/tests/test_reconciler.py order-listener:/app/tests/test_reconciler.py
docker compose exec -T order-listener python -m pytest tests/test_reconciler.py -v
```

(`docker compose cp order-listener/tests ...` nested the directory; copying the single file directly resolved it.)
