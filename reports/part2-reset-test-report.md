# Part 2 Verification Test — Miss Streak Reset on Confirmed-Present Read

**Date:** 2026-06-13
**Test scope:** Verify `reconcile_miss_count` resets to 0 when exchange confirms position present
at a larger size (`will not grow` branch — Part 2 fix) and at exact size (pre-existing path).
**No code changes in this session.**

---

## Step 1 — Position discovery and exchange confirmation

### DB open positions

```
                  id                  |       account_id        |  symbol   | side  | size | status | reconcile_miss_count
--------------------------------------+-------------------------+-----------+-------+------+--------+----------------------
 4d803ada-8eb9-4b67-9ad6-8f9d669f4a82 | Hyperliquidtest         | BTC-USDT  | short | 0.01 | open   | 0
 0a5745d8-b064-4262-b36c-a2297ab51089 | acc_blofin_demo_default | HYPE-USDT | short |    5 | open   | 0
```

### Exchange reads

**Hyperliquidtest:**
```json
[{"symbol":"BTC-USDT","side":"short","size":"0.01","entry_price":"63924.6","leverage":20,"mark_price":"64039.0","unrealized_pnl":"-1.1433"}]
```

**acc_blofin_demo_default:**
```json
[{"symbol":"HYPE-USDT","side":"short","size":"50.0","entry_price":"57.363","leverage":10,"mark_price":"58.615","unrealized_pnl":"-6.26"}]
```

### Size comparison

| Position       | db_size | exchange_size | ratio | Branch hit         | PRESENT? |
|----------------|---------|---------------|-------|--------------------|----------|
| BTC-USDT short | 0.01    | 0.01          | 1×    | exact match reset  | ✅       |
| HYPE-USDT short| 5       | 50.0          | 10×   | will not grow → **Part 2 reset** | ✅ |

Both confirmed PRESENT → safety gate passed, both seeded.

---

## Step 2 — Seeded miss_count = 2

```
UPDATE 2

                  id                  |  symbol   | side  | size | reconcile_miss_count
--------------------------------------+-----------+-------+------+----------------------
 4d803ada-8eb9-4b67-9ad6-8f9d669f4a82 | BTC-USDT  | short | 0.01 | 2
 0a5745d8-b064-4262-b36c-a2297ab51089 | HYPE-USDT | short |    5 | 2
```

---

## Step 3 — Reconcile pass

```
POST /reconcile → {"success":true,"message":"Reconcile pass complete"}
```

---

## Step 4 — Log evidence

```
[WARNING] reconciler: position 0a5745d8 (HYPE-USDT short) exchange_size=50.0 > db_size=5 — ignoring (will not grow)
[INFO]    reconciler: position 0a5745d8 (HYPE-USDT short) confirmed present (exchange_size=50.0) — miss streak reset
```

- `will not grow` immediately followed by `miss streak reset` for HYPE ✅ (Part 2 path)
- BTC `match reset` logs at DEBUG level (not visible at INFO) — DB confirms reset occurred ✅
- No `miss 3/3` line ✅
- No `closed position` line ✅

---

## Post-reconcile DB state

```
                  id                  |  symbol   | side  | status | reconcile_miss_count
--------------------------------------+-----------+-------+--------+----------------------
 4d803ada-8eb9-4b67-9ad6-8f9d669f4a82 | BTC-USDT  | short | open   | 0
 0a5745d8-b064-4262-b36c-a2297ab51089 | HYPE-USDT | short | open   | 0
```

---

## Result: **PASS**

| Check | Result |
|-------|--------|
| HYPE miss_count seeded=2 → post-reconcile=0 | ✅ |
| HYPE status still open | ✅ |
| BTC miss_count seeded=2 → post-reconcile=0 | ✅ |
| BTC status still open | ✅ |
| Logs: `will not grow` → `miss streak reset` for HYPE | ✅ |
| No `miss 3/3` for any position | ✅ |
| No `closed position` for any position | ✅ |
| Both test positions remain open | ✅ |

The Part 2 fix is confirmed working: a confirmed-present-but-larger exchange read resets the
miss streak instead of leaving it to accumulate.
