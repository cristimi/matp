# Part 3 Fix Report — BloFin Read Path Returns Base Units, Not Contracts

**Date:** 2026-06-13
**File changed:** `order-executor/app/adapters/blofin.py`

---

## Edits applied

### Edit 1 — `_to_base` helper (after `_to_contracts`, line 77)

```python
async def _to_base(self, inst_id: str, contracts: Decimal) -> Decimal:
    """Convert a Blofin contract count to base-asset volume (inverse of _to_contracts).
    base_coins = contracts * contractValue. Uses the cached instrument spec."""
    inst = await self._get_instrument(inst_id)
    if not inst:
        logger.warning(
            f"BlofinAdapter: no instrument spec for {inst_id}, using default contractValue 0.001"
        )
    contract_val = (inst or {}).get("contractValue") or "0.001"
    return contracts * Decimal(str(contract_val))
```

### Edit 2 — `get_open_positions` loop (line 280)

Before:
```python
        mapped_positions = []
        for p in raw_positions:
            size_val = float(p.get("positions", 0))
            if size_val == 0:
                continue

            mark_raw = p.get("markPrice") or p.get("last") or p.get("averagePrice", "0")
            mapped_positions.append(Position(
                symbol=p.get("instId"),
                side="long" if size_val > 0 else "short",
                size=Decimal(str(abs(size_val))),
                ...
```

After:
```python
        mapped_positions = []
        for p in raw_positions:
            size_val = float(p.get("positions", 0))
            if size_val == 0:
                continue

            inst_id   = p.get("instId")
            # BloFin reports quantity in contracts; convert to base coins so every consumer
            # (reconciler, dashboard, modify-stops) gets the same unit the DB stores.
            base_size = await self._to_base(inst_id, Decimal(str(abs(size_val))))

            mark_raw = p.get("markPrice") or p.get("last") or p.get("averagePrice", "0")
            mapped_positions.append(Position(
                symbol=inst_id,
                side="long" if size_val > 0 else "short",
                size=base_size,
                ...
```

---

## Build verification

```
docker compose exec -T order-executor grep -n "_to_base" app/adapters/blofin.py
77:    async def _to_base(self, inst_id: str, contracts: Decimal) -> Decimal:
286:            base_size = await self._to_base(inst_id, Decimal(str(abs(size_val))))
```

Service: `matp-order-executor-1  Up (healthy)  8004/tcp`

---

## Check A — Read path returns base coins

```
DB:       symbol=HYPE-USDT  side=short  db_size=5   leverage=10
Exchange: {"symbol":"HYPE-USDT","side":"short","size":"5.00","leverage":10,...}
```

exchange_size = **5.00** == db_size = **5** ✅ (was 50.0 before the fix; contractValue=0.1 applied correctly: 50 × 0.1 = 5)

---

## Check B — Reconciler sees a match; no `will not grow`

Seeded: `reconcile_miss_count = 2` on HYPE-USDT.

POST /reconcile → `{"success":true,"message":"Reconcile pass complete"}`

Logs after the manual pass (no `will not grow`, no `closed position`, no `miss N/3` for HYPE):
```
[no HYPE-USDT entries at WARNING or above]
```

(`match reset` logs at DEBUG — not visible at INFO. DB state is authoritative.)

Post-reconcile DB:
```
symbol    | side  | status | reconcile_miss_count
----------+-------+--------+---------------------
HYPE-USDT | short | open   | 0
```

miss_count 2→0, status open. **No `will not grow`. No close.** ✅

---

## Check C — Hyperliquid unaffected

```
DB:       symbol=BTC-USDT  side=short  db_size=0.01
Exchange: {"symbol":"BTC-USDT","side":"short","size":"0.01","leverage":20,...}
```

db_size == exchange_size = 0.01, unchanged. ✅

---

## Check D — skipped (optional per spec)

---

## Result: **PASS**

| Check | Result |
|-------|--------|
| HYPE executor size = db_size (5.00, not 50) | ✅ |
| HYPE miss_count 2→0 after reconcile | ✅ |
| HYPE status still open | ✅ |
| No `will not grow` for HYPE in reconcile pass | ✅ |
| No `closed position` | ✅ |
| BTC-USDT (Hyperliquid) unchanged at 0.01 | ✅ |
| Both test positions remain open | ✅ |

The contracts→coins conversion in `get_open_positions` cures all three downstream bugs
(reconciler gap, dashboard inconsistency, modify-stops 10× oversizing) with no regression on
Hyperliquid.
