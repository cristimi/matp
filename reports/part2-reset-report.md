# Part 2 — Miss Streak Reset on Confirmed-Present Read

**Date:** 2026-06-13
**Branch:** main
**File changed:** `order-listener/app/reconciler.py`

---

## Change applied

Single branch in the per-row reconcile loop (`reconcile_once`), lines 124–130 → 124–147.

**Before:**
```python
if ex_size is not None and ex_size_dec > db_size + _SIZE_EPSILON:
    # Exchange size is LARGER than DB — never grow from reconciliation
    logger.warning(
        f"reconciler: position {pos_id} ({symbol} {side}) exchange_size={ex_size_dec}"
        f" > db_size={db_size} — ignoring (will not grow)"
    )
    continue
```

**After:**
```python
if ex_size is not None and ex_size_dec > db_size + _SIZE_EPSILON:
    # Exchange size is LARGER than DB — never grow from reconciliation.
    # The position IS confirmed present, so reset the miss streak: positive evidence
    # it is NOT disappearing. Previously this branch reset nothing, which turned
    # reconcile_miss_count into a one-way ratchet whenever db_size never matched the
    # exchange (e.g. a size/units tracking mismatch), letting transient failures
    # accumulate to a false close.
    logger.warning(
        f"reconciler: position {pos_id} ({symbol} {side}) exchange_size={ex_size_dec}"
        f" > db_size={db_size} — ignoring (will not grow)"
    )
    if miss_count != 0:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE strategy_positions SET reconcile_miss_count = 0,"
                " updated_at = NOW() WHERE id = $1",
                pos_id,
            )
        logger.info(
            f"reconciler: position {pos_id} ({symbol} {side}) confirmed present "
            f"(exchange_size={ex_size_dec}) — miss streak reset"
        )
    continue
```

---

## Build verification

```
docker compose exec -T order-listener grep -n "miss streak reset" app/reconciler.py
144:                    f"(exchange_size={ex_size_dec}) — miss streak reset"
```

New code confirmed in running container at line 144.

Health check: `{"status":"ok","service":"order-listener"}`

---

## Live test — Step 0 result

```sql
SELECT sp.id, s.account_id, sp.symbol, sp.side, sp.size, sp.status, sp.reconcile_miss_count
FROM strategy_positions sp JOIN strategies s ON s.id = sp.strategy_id
WHERE sp.status='open' ORDER BY sp.opened_at;

 id | account_id | symbol | side | size | status | reconcile_miss_count
----+------------+--------+------+------+--------+----------------------
(0 rows)
```

**No open positions in DB.** Per the safety gate in the test spec, the live test is not
performed: seeding a miss count on a position the exchange does not return would (correctly)
trigger a close after 3 passes.

The reset path mirrors the already-verified exact-match reset (same DB update, same `if
miss_count != 0` guard, same `continue`). It will be re-tested when a present position exists.

---

## Final service state

```
matp-order-executor-1   Up (healthy)   8004/tcp
matp-order-listener-1   Up (healthy)   0.0.0.0:8001->8001/tcp
```

All other services healthy and unchanged.
