# BloFin Position Size & Margin Mismatch — Diagnosis Report

**Date:** 2026-06-19  
**Symbol investigated:** HYPE-USDT (long, demo account `acc_blofin_demo_default`)

---

## Current discrepancy

| | MATP DB | BloFin exchange |
|---|---|---|
| Size | 1.45598556 HYPE | 5.80 HYPE |
| Entry price | 68.666 | 68.537 |
| Margin (×20 lev) | **$5.00** | **$19.88** |

---

## Root cause: Bug 1 — `submit_order` places close orders without `reduceOnly`

**Location:** `order-executor/app/adapters/blofin.py:219-240` (`submit_order`)

When a `close_short` or `close_long` signal arrives, `_process_order` in `webhook_handler.py` calls `call_executor(order_request)` which routes to `BlofinAdapter.submit_order()`. That function builds the BloFin order body:

```python
body_data = {
    "instId": order.symbol,
    "marginMode": margin_mode,
    "side": order.side,       # "buy" for close_short
    "orderType": order.order_type,
    "size": str(order_size),  # converted to contracts
    "lever": str(leverage),
}
# No reduceOnly flag anywhere
```

There is **no `reduceOnly: true`** set. On BloFin (net-mode), a plain BUY order will reduce the short but, if the close size exceeds the actual short position, the excess becomes a **new long position** rather than being rejected.

---

## Exact reconstruction of June 18 events

**19:54:48** — `open_short 0.73032149` (new margin-clamped size):
- `_to_contracts(0.73032149)` = **0.7 contracts**
- BloFin SELL 0.7. Net: **−0.7** short

**19:55:22** — `close_short 5` (TradingView sent `size=5` — the old convention):
- `_to_contracts(5.0)` = **5.0 contracts**
- BloFin BUY 5 — no `reduceOnly`.
- BloFin had only −0.7 short. Excess 4.3 creates a **net long**.
- BloFin net: **+4.3 long** (MATP doesn't know about this)
- DB: `close_strategy_position` clamps `min(5, 0.73) = 0.73` → marks the DB short as fully closed. DB is now flat.

**20:01:32** — `open_long 1.45598556`:
- `_to_contracts(1.45598556)` = **1.5 contracts**
- BloFin BUY 1.5. Net: **+4.3 + 1.5 = +5.8 long** ✓
- DB: creates new position `size=1.45598556`

**Result:** Exchange holds 5.8 HYPE. DB tracks 1.45598556. Margin: $19.88 vs $5.00.

Net position simulation confirms the math exactly:

```
After open_short  SELL 0.7:             net = -0.7
After close_short BUY  5.0 (no reduceOnly): net = +4.3   ← position flipped
After open_long   BUY  1.5:             net = +5.8   ← matches exchange
```

---

## Bug 2 — Reconciler "exchange larger" guard prevents self-correction

**Location:** `order-listener/app/reconciler.py:124-146`

```python
if ex_size is not None and ex_size_dec > db_size + _SIZE_EPSILON:
    # Exchange size LARGER than DB — never grow from reconciliation.
    logger.warning(...)
    if miss_count != 0:
        # reset miss count
    continue
```

This guard was added correctly to prevent the reconciler from growing DB positions. But because exchange (5.80) > DB (1.45), the reconciler **resets the miss count every pass and takes no action**. The mismatch cannot self-heal via the reconciler.

---

## Bug 3 — Dashboard margin always computed from DB size

**Location:** `dashboard-api/src/routes/positions.ts:73-88`

```typescript
size: Number(dbPos.size),  // ← always from DB, never from exchange
margin: 0,                 // computed below:
// ...
enrichedPos.margin = (enrichedPos.entry_price * enrichedPos.size) / (enrichedPos.leverage || 1);
```

Even though `realPos` (the live exchange data) is available and contains the actual position size, the `size` field is always sourced from `dbPos.size`. The margin formula uses the DB-tracked size (1.45598556) rather than the exchange-observed size (5.80), producing $5.00 instead of ~$19.88.

The exchange `realPos` contains `size`, `entry_price`, and `leverage` but the enrichment code never uses `realPos.size` for the margin computation.

---

## Bug 4 — Structural: DB size vs exchange size are never equal even in the happy path

**Location:** `order-executor/app/adapters/blofin.py:60-75` (`_to_contracts`), `order-listener/app/webhook_handler.py:751-770` (`_create_strategy_position`)

The DB stores `payload.size` — the base-coin amount from the webhook (or after margin clamping). The exchange receives the rounded contract count. Due to lot-size rounding:

- DB stores: `1.45598556` HYPE
- Exchange gets: `round(1.45598556 / lotSize) * lotSize` = **1.5 contracts**
- Exchange holds: **1.5 HYPE** (if contractValue=1)

These can never be exactly equal. Even without Bug 1, there will always be a small persistent mismatch in both displayed size and computed margin. The reconciler's `_SIZE_EPSILON = 0.000001` is too tight to absorb this rounding gap (can be 0.04+ HYPE).

---

## Summary table

| Bug | Location | Effect |
|---|---|---|
| **1** (root cause) | `blofin.py` `submit_order` — no `reduceOnly` on close signals | close_short with oversized amount opened +4.3 HYPE long on exchange; DB sees it as flat |
| **2** | `reconciler.py:124-146` — "exchange larger" guard | Resets miss count every pass; divergence never self-corrects |
| **3** | `positions.ts:73` — `size = Number(dbPos.size)` | Margin and size shown in UI always use the DB value, not the live exchange value |
| **4** (structural) | `_create_strategy_position` stores `payload.size`; `_to_contracts` rounds | Even correct closes produce a persistent ~3% size discrepancy per trade |
