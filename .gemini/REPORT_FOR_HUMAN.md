# MATP Phase 3 Report — Manual-Close PnL Fallback + Hyperliquid TP/SL

**Commit:** `f58c3f4`  
**Date:** 2026-06-12  
**Branch:** `feat/strategy-tester`

---

## Part A — Manual-close PnL fallback ✅

**Problem:** Manual/UI closes via `POST /positions/{id}/close` call the executor `/close-position` which creates no `orders` row. So `sync_position_pnl` has nothing to sum → `pnl_realized` stays 0.

**Fix:** Added `_recover_manual_close_pnl(pool)` to `order-listener/app/reconciler.py`. After each `sync_position_pnl` call (including the early-return path when there are no open positions), it:
1. Queries `status='closed'` positions with `pnl_realized=0` and no attributed close orders, closed within the last 7 days
2. Calls `GET /positions/history` for each
3. Applies the existing stale-history guard (`hist_closed_at > opened_at`)
4. Writes `pnl_realized` from history when valid; logs `pnl_unconfirmed` when not

**Verification:**

Created a BTC-USDT position with `opened_at=2026-06-12T11:00:00` (before the BloFin history `closed_at=2026-06-12T12:43:05`), status=closed, `pnl_realized=0`:

Before reconcile:
```
 id                                   | symbol   | status | pnl_realized | opened_at              | closed_at
 a9cc0b83-3b7c-44f1-b8d2-a9c287a8e886 | BTC-USDT | closed | 0            | 2026-06-12 11:00:00+00 | 2026-06-12 12:43:06+00
```

After `POST /reconcile`:
```
 id                                   | symbol   | status | pnl_realized
 a9cc0b83-3b7c-44f1-b8d2-a9c287a8e886 | BTC-USDT | closed | 0.604253364
```

Listener log confirming history fallback:
```
2026-06-12 15:34:52 [INFO] reconciler: history fallback set pnl_realized=0.604253364
  for a9cc0b83-3b7c-44f1-b8d2-a9c287a8e886 (BTC-USDT long)
```

Stale-history guard fires correctly for positions where `hist_closed_at <= opened_at`:
```
2026-06-12 15:32:48 [WARNING] reconciler: stale history for 95b152aa (BTC-USDT)
  hist_closed_at=2026-06-12 12:43:05 <= opened_at=2026-06-12 15:32:34 — skipping
```

**Regression check — signal close attribution still correct:**
```
 id                                   | pnl_realized | orders_sum
 53783ec7-b340-44cb-ba78-91d0eac2242d |         6.00 |       6.00  ✅
 c3a5a9f3-1e69-41fa-9f24-ed9297f16f01 |         5.50 |       5.50  ✅
```

---

## Part B — Hyperliquid TP/SL placement ✅ (verified on HL demo exchange)

**Problem:** `HyperliquidAdapter._place_order` ignored `tp_price`/`sl_price` on `OrderRequest`.

**Fix in `order-executor/app/adapters/hyperliquid.py`:**
- When `tp_price` or `sl_price` is set on an **opening** order (`reduce_only=False`), append TP and/or SL trigger legs to the `orders` list and switch `grouping` from `"na"` to `"normalTpsl"`.
- Each trigger leg: `{"a": asset_idx, "b": trigger_side, "p": trigger_wire, "s": size_wire, "r": True, "t": {"trigger": {"isMarket": True, "triggerPx": trigger_wire, "tpsl": "tp"/"sl"}}}` — wire field order (a,b,p,s,r,t) and trigger dict order (isMarket,triggerPx,tpsl) preserved exactly as signature-critical.
- Signing path unchanged — richer `action` dict feeds the same msgpack/keccak path.
- Response parser fixed: HL returns trigger-leg statuses as plain strings (`"waitingForTrigger"`) not dicts; `isinstance` guard prevents `AttributeError`.

**Verification on Hyperliquid testnet (account: Hyperliquidtest):**

Execute call:
```json
{
  "account_id": "Hyperliquidtest", "symbol": "ETH-USDT", "side": "buy",
  "signal": "open_long", "order_type": "market", "size": "0.01",
  "tp_price": "1800", "sl_price": "1600"
}
```

Executor response (success=true, entry filled):
```json
{
  "success": true,
  "exchange_order_id": "54893222197",
  "status": "filled",
  "actual_fill_price": "1703.9",
  "raw_response": {
    "status": "ok",
    "response": {"type": "order", "data": {
      "statuses": [
        {"filled": {"totalSz": "0.01", "avgPx": "1703.9", "oid": 54893222197}},
        "waitingForTrigger",
        "waitingForTrigger"
      ]
    }}
  }
}
```

**TP and SL trigger orders confirmed on Hyperliquid exchange:**
```json
{
  "oid": 54893222199,
  "orderType": "Stop Market",
  "triggerPx": "1600.0",
  "sz": "0.01",
  "reduceOnly": true,
  "side": "A",
  "triggerCondition": "Price below 1600"
}
{
  "oid": 54893222198,
  "orderType": "Take Profit Market",
  "triggerPx": "1800.0",
  "sz": "0.01",
  "reduceOnly": true,
  "side": "A",
  "triggerCondition": "Price above 1800"
}
```

Executor log showing both legs placed:
```
[INFO] HL order with TP/SL: tp=1800 sl=1600 symbol=ETH-USDT side=buy
[INFO] HL TP/SL leg 1 placed (status: waitingForTrigger)
[INFO] HL TP/SL leg 2 placed (status: waitingForTrigger)
```

All requirements met:
- TP at 1800 ("Price above 1800") ✅
- SL at 1600 ("Price below 1600") ✅
- Both reduce-only ✅
- Opposite side to entry (A = Ask = Sell, closing the long) ✅
- Same size as entry (0.01) ✅
- Signature accepted by Hyperliquid ✅

---

## Part C — `adjust_stops` wiring

**Deferred.** `adjust_stops` remains a no-op; recorded as a follow-up task.

---

## Phase 2 Test D — External Partial-Reduction Reconciler ✅

**Test:** Open a demo position via MATP webhook, then reduce ~50% directly on the exchange (bypassing MATP), verify the reconciler detects the discrepancy after exactly N=3 consecutive misses and shrinks the DB row to match the exchange — with status staying `open`.

**Position used:** BTC-USDT short `9d38cbcb` on Hyperliquidtest, entry size=0.01.

**Step 1 — Create size mismatch:**  
Reduced 0.005 BTC short directly on exchange via executor `/close-position` (bypasses listener; no webhook fires).  
Result: exchange=0.005, DB=0.01, miss_count=0.

**Step 2 — Three reconcile passes:**

| Pass | Event | DB size | status | miss_count | Action |
|------|-------|---------|--------|------------|--------|
| 1    | `POST /reconcile` (manual) | 0.01 | open | 1 | None — miss 1/3 |
| 2    | background loop (~3s later) | 0.01 | open | 2 | None — miss 2/3 |
| 3    | `POST /reconcile` (manual) | **0.005** | **open** | **0** | **Partial reduction fires** |

Reconciler logs:
```
2026-06-12 19:28:16 [INFO] reconciler: position 9d38cbcb (BTC-USDT short) miss 1/3 db=0.01 exchange=0.005
2026-06-12 19:28:19 [INFO] reconciler: position 9d38cbcb (BTC-USDT short) miss 2/3 db=0.01 exchange=0.005
2026-06-12 19:28:25 [INFO] reconciler: position 9d38cbcb (BTC-USDT short) miss 3/3 db=0.01 exchange=0.005
2026-06-12 19:28:25 [INFO] reconciler: partial reduction position 9d38cbcb (BTC-USDT short) by 0.005 to match exchange 0.005
2026-06-12 19:28:25 [INFO] webhook_handler: Partially closed position 9d38cbcb (BTC-USDT short), close_size=0.005, fill=None, pnl=None
```

**Final state:**
```
 id         | size  | status | reconcile_miss_count
 9d38cbcb   | 0.005 | open   | 0
Exchange: BTC-USDT short size=0.005
```

All requirements met:
- 3 consecutive misses required before any action ✅
- DB size shrinks from 0.01 to 0.005 to match exchange ✅
- status stays `open` (not closed) ✅
- miss_count resets to 0 after action ✅
- Size never grows (LARGER check guard fires correctly for exchange > DB) ✅

---

## `pnl_unconfirmed` cases (Part A)

Logged at WARNING level when:
- `hist_closed_at <= opened_at` (stale-history guard) → "stale history … — skipping"
- History returns no `pnl_realized` → "pnl_unconfirmed … — no history PnL"

In both cases: `pnl_realized` is left unchanged (not fabricated).

---

## Final commit hash

`f58c3f4` — feat(phase3): manual-close PnL fallback + Hyperliquid TP/SL placement
