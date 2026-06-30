# Position Price Display + Close-Order Integrity — 2026-06-30

## Phase 1 — UI: surface SL + close price + header price strip ✓

### Changes

**`dashboard-api/src/routes/strategies.ts`**
- Added `sp.closing_price` and `o_open.sl_price` to the `GET /:id/positions` SELECT.
- Mapped both with null-safe `Number()` wrapping in the response object.

**`dashboard-ui/src/api.ts`**
- Added `closing_price: number | null` and `sl_price: number | null` to `TreePosition`.

**`dashboard-ui/src/pages/StrategyTree.tsx` → `PositionCard`**
- Header row restructured to `flexDirection: column`; top row unchanged. Second row: compact price strip (`Open … · Mark/Close … · SL …`), 11px mono muted, `·`-separated, null entries omitted.
- Open detail panel: `SL` KV added after Mark (only when `sl_price != null`).
- Closed detail panel: `Close` KV added after Entry (only when `closing_price != null`).

### Verification

**dashboard-api bundle — field count:**
```
$ docker compose exec dashboard-api sh -c "grep -c 'closing_price\|sl_price' /app/dist/routes/strategies.js"
4
```

**Live endpoint — fields present and populated (`scope=all` on hype-test-7db4):**
```
$ docker compose exec nginx wget -qO- "http://dashboard-api:8003/strategies/hype-test-7db4/positions?scope=all" \
    | python3 -m json.tool | grep -E 'closing_price|sl_price' | head -20

        "closing_price": null,
        "sl_price": 71.0571,
        "closing_price": 64.89,
        "sl_price": 71.7264,
        "closing_price": 64.733,
        "sl_price": 56.5392,
        "closing_price": 67.897,
        "sl_price": 62.8009,
        "closing_price": 66.185,
        "sl_price": 61.7189,
        "closing_price": 68.429,
        "sl_price": 62.0183,
```

**dashboard-ui bundle — new fields present:**
```
$ docker compose exec dashboard-ui grep -rl 'closing_price\|sl_price' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-CgvuQuyt.js

$ docker compose exec dashboard-ui grep -c 'closing_price\|sl_price' /usr/share/nginx/html/assets/index-CgvuQuyt.js
1
```

Both services deployed successfully. `closing_price` is populated for closed positions; `sl_price` is populated from the opening order's SL for all positions. The open position shows `closing_price: null` (expected — not yet closed).

---

## Phase 2 — Reconciler: always emit a correct, linked closing order ✓

### Changes

**`order-listener/app/reconciler.py`**

`_handle_full_external_close`:
- Added `db_size: Decimal` parameter; call site passes `db_size` from the outer loop.
- Moved synthetic order creation outside the `if not pnl_unconfirmed` guard — order is always created.
- Uses `float(db_size)` as the order `size` instead of hardcoded `0`.
- Sets `pnl=NULL` when `pnl_unconfirmed=True`; `pnl=pnl_float` otherwise.

`_recover_manual_close_pnl`:
- Added `sp.size` and `sp.closing_price` to the SELECT.
- After booking `pnl_realized` via `_book_realized_pnl`, creates a linked synthetic close order using an idempotent `WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.closes_position_id = $8)` guard.
- Order carries real `size`, `closing_price`, `pnl`, and `closes_position_id`.

### Verification

**Deployed container — no hardcoded `size=0`, `closes_position_id` wired:**
```
$ docker compose exec order-listener sh -c "grep -n 'closes_position_id\|float(db_size)\|pos_size\|pos_closing_price' /app/app/reconciler.py"
295:                float(db_size),
351:                WHERE o.closes_position_id = sp.id AND o.pnl IS NOT NULL
368:        pos_size         = row["size"]
369:        pos_closing_price = row["closing_price"]
440:                               pnl, raw_webhook, signal_source, closes_position_id)
445:                                SELECT 1 FROM orders o WHERE o.closes_position_id = $8
449:                            float(pos_size),
451:                            float(pos_closing_price) if pos_closing_price else None,
```

**Reconciler loop running, no errors:**
```
$ docker compose logs order-listener --since 2m | grep -i "reconcil\|error\|exception"
order-listener-1  | 2026-06-30 17:20:21,769 [INFO] app.main: Reconciler loop started (interval=60s, threshold=3)
```

## Phase 3 — Timeline label: full close vs partial close ✓

### Changes

**`dashboard-api/src/routes/positions.ts`** — `GET /:id/orders` CASE added a `close` branch before the `ELSE 'partial-close'` fallback:

```sql
WHEN o.closes_position_id = sp.id
  AND sp.status = 'closed'
  AND sp.size > 0
  AND (
    SELECT COALESCE(SUM(o2.size), 0)
    FROM orders o2
    WHERE o2.closes_position_id = sp.id
      AND o2.received_at <= o.received_at
  ) >= sp.size * 0.99
THEN 'close'
```

Running cumulative-sum of close-order sizes (ordered by `received_at`) reaches ≥ 99% of `sp.size` → labeled `close`. Handles single close orders (most common) and multi-step partial-close chains alike. 0.99 tolerance absorbs lot-rounding. Guard `sp.size > 0` avoids matching stale rows with zeroed size.

### Verification

**Bundle:**
```
$ docker compose exec dashboard-api sh -c "grep -n \"'close'\\|partial-close\" /app/dist/routes/positions.js"
154:            ) >= sp.size * 0.99                                          THEN 'close'
155:          ELSE 'partial-close'
```

**Live timeline for BTC position 630102d5 (size=0.002, one close order with real size=0.002):**
```
$ docker compose exec nginx wget -qO- "http://dashboard-api:8003/positions/630102d5-b9c1-467c-aafd-26e335445d51/orders" | python3 -m json.tool
[
    {
        "id": "51ab5e8a-4ce6-402c-a645-2771e2fe2e81",
        "time": "2026-06-30T00:34:49.066Z",
        "type": "entry",
        "fill": 59852,
        "delta": 0.004,
        "status": "filled",
        "key": { "avg_fill": 59852, "realized": 0, "fee": 0 }
    },
    {
        "id": "ba7c5580-4779-41fc-867b-46e38fbffcb6",
        "time": "2026-06-30T02:48:35.888Z",
        "type": "close",
        "fill": 60015,
        "delta": 0.002,
        "status": "filled",
        "key": { "avg_fill": 60015, "realized": -0.326, "fee": null }
    }
]
```

The close order correctly shows `type: "close"` with `delta: 0.002` (real size). Previously it would have shown `type: "partial-close"`.

**Observed state of closed positions (for Phase 4 context):**

| id (short) | symbol | pos size | close orders | close order size |
|---|---|---|---|---|
| abcc1b26 | HYPE | 3.0 | 0 | — |
| 5862c610 | HYPE | 3.2 | 0 | — |
| bf7a8e25 | BTC | 0.002 | 1 | 0 (old synthetic, pre-Phase-2) |
| 575ab6b8 | BTC | 0.02 | 1 | 0 (old synthetic, pre-Phase-2) |
| b9959a57 | BTC | 0.004 | 1 | 0 (old synthetic, pre-Phase-2) |
| 630102d5 | BTC | 0.002 | 1 | 0.002 ✓ |

Phase 4 migration targets the 2 HYPE positions (no close order). The 3 BTC positions with size=0 synthetic orders are a separate backfill concern noted below.

## Phase 4 — Backfill historical closed positions ✓

### Changes

**`db/migrations/034_backfill_close_orders.sql`** — two-step self-verifying DO $$ block:

**Step 1 (INSERT):** for every `closed` position with no order linked via `closes_position_id`, inserts one synthetic close order (real `size`, `closing_price`, `pnl_realized`, `signal_source='reconciler'`) and backfills `strategy_positions.closing_order_id` in a single CTE. Idempotent via `NOT EXISTS` guard.

**Step 2 (UPDATE):** fixes pre-Phase-2 synthetic close orders where `closes_position_id IS NOT NULL AND size = 0 AND signal_source = 'reconciler'`. Sets `size = sp.size`. PnL untouched. Idempotent via `size = 0` predicate.

Both steps RAISE EXCEPTION if the post-operation count is non-zero.

### Migration NOTICE output

```
NOTICE:  Step 1 candidates (closed positions with no linked close order): 7
NOTICE:  Step 1 inserted: 7 close orders (closing_order_id backfilled on positions)
NOTICE:  Step 1 remaining (still no close order): 0
NOTICE:  Step 2 candidates (size=0 reconciler close orders): 23
NOTICE:  Step 2 updated: 23 orders
NOTICE:  Step 2 remaining (size=0 reconciler close orders): 0
DO
```

### SQL result — all 5 affected positions

```sql
SELECT sp.id, sp.symbol, sp.status, sp.size, sp.closing_order_id,
       (SELECT COUNT(*) FROM orders o WHERE o.closes_position_id = sp.id) AS close_orders,
       (SELECT o.size   FROM orders o WHERE o.closes_position_id = sp.id ORDER BY o.received_at DESC LIMIT 1) AS close_order_size
FROM strategy_positions sp WHERE sp.id IN ('abcc1b26…','bf7a8e25…','575ab6b8…','b9959a57…','5862c610…');
```

```
 symbol    | size    | closing_order_id                     | close_orders | close_order_size
-----------+---------+--------------------------------------+--------------+-----------------
 BTC-USDT  | 0.002   | 5127816d-ce3a-4105-8b83-33956dc39585 |            1 | 0.002
 BTC-USDT  | 0.02    | b922b3d0-30ff-46cc-a4d2-b94e663c7775 |            1 | 0.02
 BTC-USDT  | 0.004   | 887e8dbd-3645-40c4-b07f-e3ea3c835840 |            1 | 0.004
 HYPE-USDT | 3.0     | 5ce1e7c7-0628-4828-8749-416d288f29dd |            1 | 3.0
 HYPE-USDT | 3.2     | 2a894386-1793-4aaa-96f9-506c1c637db1 |            1 | 3.2
```

All `close_orders = 1` and `close_order_size = sp.size`.

### Live timeline — all show `close` with real delta

```
bf7a8e25 (BTC 0.002):   entry delta=0.004 fill=60017.4 | close delta=0.002 fill=59600.9
575ab6b8 (BTC 0.02):    entry delta=0.02  fill=59498.5 | close delta=0.02  fill=60184.106
b9959a57 (BTC 0.004):   entry delta=0.004 fill=59776.3 | close delta=0.004 fill=60175
abcc1b26 (HYPE 3.0):    entry delta=3.039 fill=65.802  | close delta=3.0   fill=64.89
```

## Phase 3 follow-up — fix close-label denominator ✓

### Problem

The cumulative-sum branch (`SUM(o2.size) >= sp.size * 0.99`) used `sp.size` as the denominator, but `sp.size` is decremented on every partial close and left at the final remaining leg size at the time of full close. For a multi-leg close the threshold was cleared on the very first leg, mislabeling all legs `close`.

### Fix

`dashboard-api/src/routes/positions.ts` — replaced the cumulative-sum branch with a terminal-leg identity check: the order whose `id` matches the latest `received_at DESC, id DESC` among all close orders for this position is the `close`; every earlier leg falls through to `partial-close`. No dependence on `sp.size`.

### Verification

**Bundle — new branch present:**
```
$ docker compose exec dashboard-api sh -c "grep -n 'ORDER BY o2.received_at DESC' /app/dist/routes/positions.js"
151:              ORDER BY o2.received_at DESC, o2.id DESC
```

**Multi-leg position `fe754dac` (BTC-USDT, 2 close legs):**
```
$ docker compose exec dashboard-api curl -s http://localhost:8003/positions/fe754dac-d632-47c4-b243-0acee387fa71/orders \
    | python3 -m json.tool | grep -E '"type"|"delta"'
        "type": "entry",
        "delta": 0.004,
        "type": "partial-close",
        "delta": 0.002,
        "type": "close",
        "delta": 0.002,
```

First leg → `partial-close`, last leg → `close`. Correct.

**Regression — single-leg `abcc1b26` (HYPE, 1 close leg):**
```
$ docker compose exec dashboard-api curl -s http://localhost:8003/positions/abcc1b26-b404-4e77-8622-c3326deba7aa/orders \
    | python3 -m json.tool | grep -E '"type"|"delta"'
        "type": "entry",
        "delta": 3.03932892,
        "type": "close",
        "delta": 3,
```

Still `close` — no regression.
