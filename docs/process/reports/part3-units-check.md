# Part 3 Pre-Check — BloFin Size Gap: Contracts vs Leverage

**Date:** 2026-06-13
**Scope:** Read-only. No code, DB, or position changes.

---

## Step 1 — BloFin instrument specs (public API)

```
https://demo-trading-api.blofin.com/api/v1/market/instruments?instType=SWAP

HYPE-USDT  contractValue=0.1    lotSize=1    minSize=1
BTC-USDT   contractValue=0.001  lotSize=0.1  minSize=0.1
```

`contractValue` = coins (base asset) per 1 contract.

---

## Step 2 — DB size vs exchange size

### DB open positions

```
account_id               | symbol    | side  | db_size | leverage
acc_blofin_demo_default  | HYPE-USDT | short | 5       | 10
Hyperliquidtest          | BTC-USDT  | short | 0.01    | 20
```

### Exchange reads

**BloFin (acc_blofin_demo_default):**
```json
[{"symbol":"HYPE-USDT","side":"short","size":"50.0","leverage":10}]
```

**Hyperliquid (Hyperliquidtest):**
```json
[{"symbol":"BTC-USDT","side":"short","size":"0.01","leverage":20}]
```

---

## Step 3 — Cross-checks

### Contracts test (HYPE-USDT)

```
exchange_size × contractValue = 50.0 × 0.1 = 5.0
db_size                                     = 5
Match: ✅
```

BloFin reports position size in **contracts**; the DB stores it in **base coins** (as sent by
the TradingView signal). The adapter's `_to_contracts` converts correctly on the write path
(`5 coins / 0.1 = 50 contracts`), but `get_open_positions` returns the raw contract count
without converting back.

### Leverage refutation (BTC-USDT on Hyperliquid)

```
leverage = 20×
exchange_size = 0.01
db_size       = 0.01
Gap = 0   (exact match despite 20× leverage)
```

If leverage drove the gap, BTC at 20× would show exchange = 20 × 0.01 = 0.2. It does not.
**Leverage hypothesis: refuted.** ✅

---

## Verdict

**Mechanism: contracts.** BloFin's `get_open_positions` returns size in exchange contracts;
MATP's DB stores size in base-asset coins. The conversion factor is each instrument's own
`contractValue` field from `/api/v1/market/instruments`.

**Part 3 fix:** in `BlofinAdapter.get_open_positions`, multiply each returned size by
`contractValue` before yielding the position object, so the reconciler compares like-for-like
(coins vs coins). The fix must look up `contractValue` per symbol via `_get_instrument`
(already cached) — not hardcoded.

Confirmed values:
| Symbol    | contractValue | exchange contracts | converted coins | db_size | Match |
|-----------|---------------|--------------------|-----------------|---------|-------|
| HYPE-USDT | 0.1           | 50.0               | 5.0             | 5       | ✅    |
| BTC-USDT  | 0.001         | (BloFin not used)  | —               | —       | n/a   |

Both test positions left open and unchanged.
