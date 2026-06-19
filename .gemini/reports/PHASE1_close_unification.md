# Phase 1 Report — Close-path Unification + BloFin `reduceOnly` (Bug 1)

**Date:** 2026-06-19  
**Status:** COMPLETE — all new tests pass; build healthy

---

## Changes

### 1. `order-executor/app/adapters/blofin.py`

Added `is_close` detection in `submit_order`. When `order.signal` is `close_long` or
`close_short`, the request body now includes:

```python
body_data["reduceOnly"]    = "true"
body_data["positionSide"]  = "net"
```

This prevents BloFin (net-mode) from flipping an oversized close into an opposing position.
The fix is belt-and-suspenders: the structural routing change below means `submit_order` should
never be reached for close signals at all, but the guard remains in case a direct call bypasses
the listener.

### 2. `order-listener/app/webhook_handler.py` — `_process_order`

Close signals (`close_long` / `close_short`) now exit early through
`close_strategy_position(skip_exchange=False)` instead of falling through to
`call_executor` → `submit_order`. This routes the close through the adapter's
`close_position()` endpoint (`/api/v1/trade/close-position` or `_partial_close`), which
has always been `reduceOnly`-safe.

The old dead branch that called `close_strategy_position(skip_exchange=True)` for close
signals was removed; the downstream open-path `elif` was changed to `if`.

### 3. New tests

| Service | File | Tests | Result |
|---------|------|-------|--------|
| order-executor | `tests/test_blofin_close.py` | 3 | **PASSED** |
| order-listener | `tests/test_close_unification.py` | 4 | **PASSED** |

### 4. Infrastructure

- `order-executor/requirements.txt`: added `pytest`, `pytest-asyncio`
- `order-executor/Dockerfile`: added `COPY tests/ ./tests/`
- `order-executor/tests/__init__.py`: created (empty)

---

## Test output

### order-executor

```
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.0, pluggy-1.6.0 -- /usr/local/bin/python
cachedir: .pytest_cache
rootdir: /app
plugins: asyncio-1.4.0, anyio-4.14.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 3 items

tests/test_blofin_close.py::test_submit_order_close_short_has_reduce_only PASSED [ 33%]
tests/test_blofin_close.py::test_submit_order_close_long_has_reduce_only PASSED [ 66%]
tests/test_blofin_close.py::test_submit_order_open_long_has_no_reduce_only PASSED [100%]

============================== 3 passed in 8.57s ===============================
```

### order-listener

```
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.0, pluggy-1.6.0 -- /usr/local/bin/python
cachedir: .pytest_cache
rootdir: /app
plugins: asyncio-1.4.0, anyio-4.14.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 40 items

tests/test_close_unification.py::test_close_short_routes_through_close_strategy_position PASSED [  2%]
tests/test_close_unification.py::test_close_long_routes_through_close_strategy_position PASSED [  5%]
tests/test_close_unification.py::test_oversized_close_still_routes_to_close_not_executor PASSED [  7%]
tests/test_close_unification.py::test_close_with_no_open_position_updates_order_status PASSED [ 10%]
...
(36 pre-existing tests: 34 passed, 2 failed)
```

Pre-existing failures in `test_webhook_handler.py`:
- `test_valid_token_passes_auth` — FAILED (pre-existing; open-signal test requires live mark
  price for BTC-USDT, not available in test env; unrelated to Phase 1)
- `test_quote_variant_accepted_when_flag_on` — FAILED (same reason)

---

## Verify: `reduceOnly` present in running image

```
$ docker compose exec order-executor grep -n "reduceOnly" /app/app/adapters/blofin.py
231:                # Without reduceOnly, an oversized close flips the position on BloFin.
232:                body_data["reduceOnly"] = "true"
426:            "reduceOnly":   "true",
680:                    "reduceOnly": "true",
```

Line 232 is the new belt-and-suspenders guard in `submit_order`.  
Lines 426 and 680 are the pre-existing safe paths in `close_position` / `_partial_close`.

---

## Root cause addressed

The June 18 incident: `close_short 5` was sent as a plain BUY without `reduceOnly`.
BloFin had only `-0.7` contracts open; the excess `4.3` contracts created a net LONG,
which compounded when `open_long 1.45` arrived shortly after → `5.8` contract LONG on
exchange vs `1.45` in DB.

Phase 1 prevents recurrence via two independent guards:
1. **Structural:** close signals never reach `submit_order` (they use `close_position` safe path).
2. **Belt-and-suspenders:** if `submit_order` *is* called with a close signal, `reduceOnly`
   prevents the position flip.

---

## Container health after deploy

```
Container matp-order-executor-1  Started → Healthy
Container matp-order-listener-1  Started (running)
```
