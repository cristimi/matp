# Phase 1.6 — External-close booking via `sync_position_pnl`

**Date:** 2026-06-22  
**File changed:** `order-listener/app/webhook_handler.py` — `sync_position_pnl` function  
**Status:** Complete — container running, all checks passed

---

## Why this exists

Phase 1.5 added booking to `close_strategy_position` (signal/manual/flip) and
`_recover_manual_close_pnl`, but the dominant external-close path still didn't book:

1. `_handle_full_external_close` creates a **synthetic closing order** with `pnl` set — it does NOT
   set `pnl_realized`.
2. `reconcile_once` calls `sync_position_pnl` (sets `pnl_realized = SUM(order.pnl)`) **before**
   `_recover_manual_close_pnl`.
3. `_recover_manual_close_pnl` excludes positions that already have a closing order
   (`NOT EXISTS (SELECT 1 FROM orders ... WHERE closes_position_id = sp.id)`) — so positions filled
   by step 2 are never reached.
4. Net: `sync_position_pnl` set `pnl_realized` but never called `_book_realized_pnl` → external
   closes never compounded `capital_allocation`.

---

## The fix — diff

```diff
-async def sync_position_pnl(pool) -> None:
-    """
-    Propagate realized PnL from closing orders to positions.
-    Runs after every reconcile pass. Partial-safe: sums all close orders per position.
-    """
-    async with pool.acquire() as conn:
-        await conn.execute(
-            """
-            UPDATE strategy_positions sp
-            SET pnl_realized = sub.total,
-                updated_at   = NOW()
-            FROM (
-                SELECT closes_position_id AS pid,
-                       COALESCE(SUM(pnl), 0) AS total
-                FROM orders
-                WHERE closes_position_id IS NOT NULL
-                  AND pnl IS NOT NULL
-                GROUP BY closes_position_id
-            ) sub
-            WHERE sp.id = sub.pid
-              AND sp.pnl_realized IS DISTINCT FROM sub.total
-            """
-        )
+async def sync_position_pnl(pool) -> None:
+    """Propagate realized PnL from closing orders to positions, and book the strategy's
+    allocation on the pnl_realized NULL->value transition (the external-close booking point).
+    Idempotent: a row is booked once, when it first goes NULL -> value."""
+    async with pool.acquire() as conn:
+        # (1) First attribution (NULL -> value): set AND book.
+        newly = await conn.fetch(
+            """
+            UPDATE strategy_positions sp
+            SET pnl_realized = sub.total,
+                updated_at   = NOW()
+            FROM (
+                SELECT closes_position_id AS pid, COALESCE(SUM(pnl), 0) AS total
+                FROM orders
+                WHERE closes_position_id IS NOT NULL AND pnl IS NOT NULL
+                GROUP BY closes_position_id
+            ) sub
+            WHERE sp.id = sub.pid
+              AND sp.pnl_realized IS NULL
+            RETURNING sp.id, sp.strategy_id, sp.pnl_realized
+            """
+        )
+        # (2) Corrections (already-booked, value changed): update only, do NOT re-book.
+        await conn.execute(
+            """
+            UPDATE strategy_positions sp
+            SET pnl_realized = sub.total,
+                updated_at   = NOW()
+            FROM (
+                SELECT closes_position_id AS pid, COALESCE(SUM(pnl), 0) AS total
+                FROM orders
+                WHERE closes_position_id IS NOT NULL AND pnl IS NOT NULL
+                GROUP BY closes_position_id
+            ) sub
+            WHERE sp.id = sub.pid
+              AND sp.pnl_realized IS NOT NULL
+              AND sp.pnl_realized IS DISTINCT FROM sub.total
+            """
+        )
+    for r in newly:
+        await _book_realized_pnl(pool, str(r['strategy_id']), r['pnl_realized'])
```

**Why exactly-once and disjoint:**
- `close_strategy_position` (signal/manual/flip) sets `pnl_realized` non-NULL at close time → part (1) skips it (IS NULL → false).
- `_recover_manual_close_pnl` handles positions with **no** closing order (NOT EXISTS clause) — those with a closing order go to sync part (1). Mutually exclusive.
- Part (2) only touches already non-NULL rows where the order-sum changed; does not call `_book_realized_pnl`.

---

## Deploy

```
./scripts/redeploy.sh order-listener --clean
docker compose up -d --force-recreate order-listener
```

Container health:
```
$ docker compose ps order-listener
NAME                    IMAGE                 COMMAND     SERVICE          CREATED         STATUS
matp-order-listener-1   matp-order-listener   ...         order-listener   ~1min ago       Up 45s (healthy)
$ curl -sf http://localhost:8001/health
{"status":"ok","service":"order-listener"}
```

---

## V4 — Structure

```
$ docker compose exec order-listener grep -c "capital_allocation = capital_allocation +" /app/app/webhook_handler.py
1

$ docker compose exec order-listener grep -n "RETURNING sp.id, sp.strategy_id, sp.pnl_realized" /app/app/webhook_handler.py
956:            RETURNING sp.id, sp.strategy_id, sp.pnl_realized
```

Single `_book_realized_pnl` call site. RETURNING clause present at line 956.

---

## V1 — External-close booking via sync (the bug, now fixed)

**Setup:** `tv_test_harness`, A=300, pnl_today=0, pnl_total=0.

Inserted:
- A `status='closed'` position `eeeeeeee-...` ETH-USDT long, `pnl_realized=NULL`.
- A synthetic closing order `ffffffff-...` with `closes_position_id=eeeeeeee-...`, `pnl=12.50`.

This is exactly the state `_handle_full_external_close` leaves: a closed position with no `pnl_realized`
and a synthetic order carrying the PnL. `_recover_manual_close_pnl` excludes it (closing order exists).
Only `sync_position_pnl` part (1) can book it.

**Pre-reconcile state:**
```
 id                                   | symbol   | status | pnl_realized | order_pnl
--------------------------------------+----------+--------+--------------+-----------
 eeeeeeee-0001-0001-0001-000000000001 | ETH-USDT | closed |              |     12.50

 capital_allocation | pnl_today | pnl_total
--------------------+-----------+-----------
                300 |         0 |         0
```

**First `POST /reconcile`:**
```
$ curl -sf -X POST http://localhost:8001/reconcile
{"success":true,"message":"Reconcile pass complete"}
```

**After first reconcile:**
```
 id                                   | symbol   | status | pnl_realized
--------------------------------------+----------+--------+--------------
 eeeeeeee-0001-0001-0001-000000000001 | ETH-USDT | closed |        12.50

 capital_allocation | pnl_today | pnl_total
--------------------+-----------+-----------
              312.5 |      12.5 |      12.5
```

`capital_allocation` 300 → 312.5 (delta = +12.50). `pnl_realized` set to 12.50. Booking via sync
path confirmed. ✓

**Second `POST /reconcile` (idempotency):**
```
$ curl -sf -X POST http://localhost:8001/reconcile
{"success":true,"message":"Reconcile pass complete"}

 capital_allocation | pnl_today | pnl_total
--------------------+-----------+-----------
              312.5 |      12.5 |      12.5
```

Unchanged. Part (1) skips: `pnl_realized` is now non-NULL. ✓

---

## V2 — Signal/manual close not re-booked by reconcile

`ai-btc-6f8c` has 4 closed positions booked via `close_strategy_position` (Phase 1 path).
`pnl_realized` is non-NULL on all of them.

**Before:**
```
 capital_allocation | pnl_today | pnl_total
--------------------+-----------+-----------
 100.578168         | 0.578168  | 0.578168
```

**After `POST /reconcile`:**
```
 capital_allocation | pnl_today | pnl_total
--------------------+-----------+-----------
 100.578168         | 0.578168  | 0.578168
```

Unchanged. Part (1) condition (`pnl_realized IS NULL`) skips all non-NULL rows. ✓

---

## V3 — Correction path (part 2) doesn't book

`eeeeeeee-...` already booked (`pnl_realized=12.50`). Closing order pnl updated to 13.75
(simulates partial accumulation — later order arriving).

**Pre-reconcile:**
```
 pnl_realized | order_pnl
--------------+-----------
        12.50 |     13.75

 capital_allocation | pnl_today | pnl_total
--------------------+-----------+-----------
              312.5 |      12.5 |      12.5
```

**After `POST /reconcile`:**
```
 pnl_realized | order_pnl
--------------+-----------
        13.75 |     13.75

 capital_allocation | pnl_today | pnl_total
--------------------+-----------+-----------
              312.5 |      12.5 |      12.5
```

`pnl_realized` corrected from 12.50 → 13.75 (part (2) fired). `capital_allocation` unchanged — no
re-booking. ✓

---

## Out of scope / follow-up noted

- **Delta-booking on multi-order partial external reductions:** When part (2) fires (pnl_realized
  correction), the delta between old and new value is not booked. Booking the delta correctly requires
  tracking which increment was previously booked — deferred as a follow-up, not in scope here.
- Pre-existing non-NULL unbooked HYPE row — separate data-correction task, untouched.
- Phase 2 (lifecycle endpoints, `webhook_enabled` gate removal) — awaiting human approval.
