# Part 4 Fix Report — Position-Scoped PnL Attribution

**Date:** 2026-06-13
**Files changed:** `base.py`, `hyperliquid.py`, `blofin.py` (adapters), `main.py` (executor),
`executor_client.py`, `reconciler.py` (listener)

---

## Edits applied

### Edit 1 — `base.py` abstract signature

```python
# Before
async def get_closed_position_details(self, symbol: str) -> dict | None:
# After
async def get_closed_position_details(self, symbol: str, since_ms: int | None = None) -> dict | None:
```

### Edit 2 — `hyperliquid.py` — time-filter on close_fills

Signature updated; filter added to comprehension:
```python
close_fills = [
    f for f in fills
    if f.get("coin") == coin and (
        "Close" in (f.get("dir") or "") or
        "Liq"   in (f.get("dir") or "")
    )
    and (since_ms is None or int(f.get("time", 0)) >= since_ms)
]
```

### Edit 3 — `blofin.py` — filter entries by updateTime

Signature updated; filter added after empty check:
```python
if since_ms is not None:
    entries = [e for e in entries if int(e.get("updateTime") or 0) >= since_ms]
    if not entries:
        return None
```

### Edit 4 — `main.py` — endpoint accepts `?since=` query param

```python
async def get_position_history(account_id: str, symbol: str, since: int | None = None):
    details = await adapter.get_closed_position_details(symbol, since_ms=since)
```

### Edit 5 — `executor_client.py` — forwards `opened_at` as `&since=`

```python
async def get_position_history(account_id: str, symbol: str, opened_at=None) -> dict:
    ...
    if opened_at is not None:
        since_ms = int(opened_at.timestamp() * 1000)
        path += f"&since={since_ms}"
```

### Edit 6 — `reconciler.py` — both call sites pass `opened_at`

```python
# _handle_full_external_close
history = await get_position_history(acct_id, symbol, opened_at)

# _recover_manual_close_pnl
history = await get_position_history(acct_id, symbol, opened_at)
```

---

## Build verification (both containers)

```
order-executor grep -n "since_ms" app/adapters/hyperliquid.py
  539: async def get_closed_position_details(self, symbol: str, since_ms: int | None = None)
  551: # scoped to fills at or after since_ms so PnL is not summed across the whole
  559: and (since_ms is None or int(f.get("time", 0)) >= since_ms)

order-listener grep -n "&since=" app/executor_client.py
  126: path += f"&since={since_ms}"
```

Both services healthy.

---

## Check A — latest closed BTC position

```
id                                   | opened_at                     | opened_ms     | closed_at                     | pnl_realized
-------------------------------------+-------------------------------+---------------+-------------------------------+-------------
9d38cbcb-8514-4b71-9cc9-c4eec8172e27 | 2026-06-12 16:48:12.560646+00 | 1781282892561 | 2026-06-12 20:58:39.920741+00 | 30.01278
```

`pnl_realized=30.01278` is the DB value — the over-attribution the fix addresses.

---

## Check B — differential: unscoped vs tight-window vs scoped

### Unscoped (old behavior — sums all BTC close history)
```json
{"close_reason":"Closed on exchange","closing_price":62995.203019,"pnl_realized":30.01278,...}
```
38 fills included: Close Long (Jun-10), Close Long (Jun-12 06:15), Close Short (Jun-12 14:46),
Close Short ×2 (Jun-12 20:54–20:57). PnL spans the full account history.

### Tight window (now − 60s) — proves filter is wired
```json
{}
```
No BTC closes in the last 60 seconds → filter correctly returns nothing.

### Scoped to opened_ms=1781282892561 (the correct single position)
```json
{"close_reason":"Closed on exchange","closing_price":64089.549,"pnl_realized":0.23551,
 "closed_at":"2026-06-12T20:56:26.231000+00:00"}
```
Only 6 fills (all "Close Short" ≥ 2026-06-12T16:48), total sz≈0.01 BTC.

### Magnitude comparison
| Call | pnl_realized | fills |
|------|-------------|-------|
| Unscoped | 30.01278 | 38 (entire account history) |
| Tight window | {} | 0 |
| Scoped to position open | **0.23551** | 6 (this position only) |

Over-attribution factor: 30.01 / 0.24 ≈ **125×**. The position was a ~0.01 BTC short at
64kUSD — a $0.24 realistic PnL. ✅

---

## Check C — backward compatibility (no `since`)

Unscoped call above returned a full result (not {}). Default behavior unchanged. ✅

---

## Check D — client wiring: `&since=` carried in reconcile calls

```
GET /accounts/acc_blofin_demo_default/positions/history?symbol=SOL-USDT&since=1781276068730 → 200 OK
GET /accounts/acc_blofin_demo_default/positions/history?symbol=SOL-USDT&since=1781276084373 → 200 OK
GET /accounts/acc_blofin_demo_default/positions/history?symbol=ETH-USDT&since=1781275711912 → 200 OK
```

Every history call from the reconciler carries `&since=<opened_ms>`. ✅

---

## Final state

Both test positions remain open and unaffected:
```
BTC-USDT  | short | open | miss_count=0
HYPE-USDT | short | open | miss_count=0
```

---

## Result: **PASS**

| Check | Result |
|-------|--------|
| Unscoped pnl > scoped pnl (over-attribution confirmed and fixed) | ✅ |
| Tight-window returns {} (filter demonstrably applied) | ✅ |
| Scoped pnl realistic for position size (~$0.24 for 0.01 BTC) | ✅ |
| Default (no since) still returns a result | ✅ |
| Reconcile requests carry &since= | ✅ |
| Both test positions remain open | ✅ |

The `since_ms` time scope eliminates cross-position PnL bleed. A BTC trade history that
previously summed to +30.01 USDT now correctly attributes +0.24 USDT to the one position
that was actually closed.
