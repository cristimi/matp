# Phase 1 — Idempotent PnL Booking + Flatten-on-Disable

**Date:** 2026-06-21  
**Files changed:** `order-listener/app/webhook_handler.py`, `order-listener/app/reconciler.py`  
**Status:** Complete — container running, invariants verified

---

## Files + ranges read (1.1)

### `webhook_handler.py`

| Range | Content |
|-------|---------|
| 87–125 | `_is_drawdown_breached`, `_disable_if_drawdown_breached` |
| 586–613 | Guard 5 drawdown-on-open block (removed) |
| 785–916 | `close_strategy_position` full body incl. `is_full` var, full-close UPDATE |
| 1023–1045 | Flat handler booking block (removed) |
| 1059–1114 | Close signal handler + booking block (removed) |
| 1200–1295 | Open path: executor call, site-3 booking (removed), top-up/create |

### `reconciler.py`

| Range | Content |
|-------|---------|
| 300–321 | `close_strategy_position` call in `_handle_full_external_close` |
| 324–418 | `_recover_manual_close_pnl` — SELECT, history fetch, stale guard, UPDATE |

---

## Changes made

### 1.2 — `_book_realized_pnl` helper (new, lines 129–152 post-edit)

```python
async def _book_realized_pnl(pool, strategy_id: str, pnl) -> None:
    if pnl is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE strategies
                SET pnl_today          = pnl_today + $1,
                    pnl_total          = pnl_total + $1,
                    capital_allocation = capital_allocation + $1,
                    allocation_peak    = GREATEST(COALESCE(allocation_peak, capital_allocation),
                                                  capital_allocation + $1),
                    updated_at         = NOW()
                WHERE id = $2
                """,
                float(pnl), strategy_id,
            )
        await _disable_if_drawdown_breached(pool, strategy_id)
    except Exception as e:
        logger.warning(f"_book_realized_pnl failed for {strategy_id}: {e}")
```

### 1.3 — `_flatten_strategy_positions` helper (new, lines 155–167 post-edit)

```python
async def _flatten_strategy_positions(pool, strategy: dict) -> list[dict]:
    async with pool.acquire() as conn:
        legs = await conn.fetch(
            "SELECT symbol, side FROM strategy_positions WHERE strategy_id=$1 AND status='open'",
            strategy['id'],
        )
    results = []
    for leg in legs:
        r = await close_strategy_position(
            pool, strategy, symbol=leg['symbol'], side=leg['side'],
            reason="flatten_on_disable",
        )
        results.append({"symbol": leg['symbol'], "side": leg['side'], **(r or {})})
    return results
```

### 1.4 — `_disable_if_drawdown_breached` updated (flatten THEN disable)

Key diff — old code set `enabled=false` directly; new code flattens open legs first:

```python
# NEW: fetch account_id too so _flatten_strategy_positions can close via exchange
row = await conn.fetchrow(
    "SELECT id, enabled, capital_allocation, allocation_peak, max_drawdown_pct, "
    "account_id FROM strategies WHERE id = $1",
    strategy_id,
)
# connection released before flatten
strategy = dict(row)
...
if _is_drawdown_breached(...):
    await _flatten_strategy_positions(pool, strategy)   # NEW: close all legs first
    async with pool.acquire() as conn:
        await conn.execute("UPDATE strategies SET enabled = false ...")
```

### 1.5 — `close_strategy_position`: idempotent booking on full close

Added after the full-close transaction commits, before returning `ret`:

```python
if is_full and realized_pnl_f is not None:
    await _book_realized_pnl(pool, strategy['id'], realized_pnl_f)
```

Idempotency guard: the full-close UPDATE uses `WHERE id = $5 AND status = 'open'` — an open
position can only transition to closed once. If `updated is not None` (the race-condition guard
already in place) AND `is_full AND realized_pnl_f is not None`, booking fires exactly once.

### 1.5 — Three caller booking blocks removed

Deleted from `handle_webhook`:

- **Site 1** (~line 1023): flat handler `UPDATE strategies SET pnl_today ...` + `_disable_if_drawdown_breached`
- **Site 2** (~line 1092): close signal handler `UPDATE strategies SET pnl_today ...` + `_disable_if_drawdown_breached`
- **Site 3** (~line 1213): open path (flip path) `UPDATE strategies SET pnl_today ...` + `_disable_if_drawdown_breached`

### 1.6 — Flip: instant booking + no stale row

Added after top-up-or-create block, inside `if payload.signal in ("open_long", "open_short"):`:

```python
flip_pnl = exec_result.get("pnl") or exec_result.get("realized_pnl")
if flip_pnl is not None and float(flip_pnl) != 0:
    opposite_side = "short" if pos_side == "long" else "long"
    try:
        async with pool.acquire() as conn:
            opp_leg = await conn.fetchrow(
                """SELECT id FROM strategy_positions
                   WHERE strategy_id=$1 AND symbol=$2 AND side=$3 AND status='open'""",
                strategy['id'], pos_symbol, opposite_side,
            )
        if opp_leg:
            await close_strategy_position(
                pool, strategy, symbol=pos_symbol, side=opposite_side,
                skip_exchange=True, realized_pnl=flip_pnl,
                fill_price=result.actual_fill_price, reason="flip_close",
            )
        else:
            logger.warning(f"Flip PnL={flip_pnl} ... — booking directly")
            await _book_realized_pnl(pool, strategy['id'], flip_pnl)
    except Exception as _fe:
        logger.warning(f"Flip PnL handling failed ...")
```

### 1.7 — Guard 5 removed

Deleted the 26-line block (`if payload.signal in ("open_long", "open_short"):` drawdown check
that raised 429 `drawdown_stop`). The `_is_drawdown_breached` docstring updated to remove the
stale "Guard 5" reference.

### reconciler.py: `_recover_manual_close_pnl` updated

Two changes:

1. Added `sp.strategy_id` to the SELECT query (needed to call `_book_realized_pnl`).

2. Changed UPDATE from `execute` to `fetchrow` with `RETURNING id, strategy_id, pnl_realized`
   and tightened guard from `AND (pnl_realized IS NULL OR pnl_realized = 0)` to
   `AND pnl_realized IS NULL`. For each actually-updated row, calls `_book_realized_pnl`.

```python
updated_row = await conn.fetchrow(
    """
    UPDATE strategy_positions
    SET pnl_realized = $1, updated_at = NOW()
    WHERE id = $2 AND status = 'closed'
      AND pnl_realized IS NULL
    RETURNING id, strategy_id, pnl_realized
    """,
    pnl_float, pos_id,
)
if updated_row:
    from app.webhook_handler import _book_realized_pnl
    await _book_realized_pnl(pool, str(updated_row['strategy_id']), updated_row['pnl_realized'])
```

---

## Build + verify output (1.8)

### Build

```
docker compose build --no-cache order-listener
# → Successfully built matp-order-listener (no errors)

docker compose up -d --force-recreate order-listener
# → Container matp-order-listener-1 Recreated → Started
```

### Invariant checks inside running container

```
$ docker compose exec order-listener grep -c "capital_allocation = capital_allocation +" /app/app/webhook_handler.py
1

$ docker compose exec order-listener grep -c "Drawdown stop hit\|drawdown_stop" /app/app/webhook_handler.py
0
```

Exactly **one** `capital_allocation = capital_allocation +` site (inside `_book_realized_pnl`). Guard 5 is fully absent.

### Health check

```
$ curl -sf http://localhost:8001/health
{"status":"ok","service":"order-listener"}
```

### Reconciler ran cleanly (from logs)

```
2026-06-21 20:26:02,624 [INFO] httpx: HTTP Request: GET http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/positions "HTTP/1.1 200 OK"
2026-06-21 20:26:02,727 [INFO] app.main: Reconciler: automatic pass complete
```

### Invariant spot check: disabled strategy with open position

```sql
SELECT s.id FROM strategies s JOIN strategy_positions p ON p.strategy_id=s.id
WHERE s.enabled=false AND p.status='open';
```
```
 id
----
(0 rows)
```

### DB state snapshot

```
           id           | enabled | capital_allocation | pnl_total
------------------------+---------+--------------------+------------
 tv_test_harness        | t       | 300                | 0
 matp-test-harness-fe19 | f       | 500                | 0
 tv-btc-test-hl-94e1   | t       | 102.298249...      | 2.298249...
 hype-test-7db4         | t       | 200                | 0
 ai-btc-6f8c            | t       | 100.578167...      | 0.578167...
```

**Booked positions verified against strategy totals:**

- `ai-btc-6f8c` closed positions: 0.09672 + 0.040248 + 0.12877 + 0.31243 = **0.578168** ✓ matches `pnl_total`
- `tv-btc-test-hl-94e1` closed position: 2.29825 ✓ matches `pnl_total`
- All booked positions have closing orders with `pnl` set (went through old signal-close path). Under new code these will be booked by `close_strategy_position` directly.

**Pre-existing HYPE data gap noted:**  
`hype-test-7db4` has a closed position with `pnl_realized=0.8062` but `capital_allocation=200, pnl_total=0`. This is a historical gap from before this fix — the position was closed (likely by reconciler when `webhook_enabled=false` blocked signal entry), the old `close_strategy_position(skip_exchange=True)` set `pnl_realized` in the position table but never booked into the strategy. Since `pnl_realized != NULL`, `_recover_manual_close_pnl` does not target it. This is documented but not fixed here — it predates the new booking path.

**No-double-count check:** The `_recover_manual_close_pnl` target query (`pnl_realized IS NULL OR pnl_realized = 0`) returns **0 rows** currently — no already-booked positions will be re-processed.

---

## Idempotency guarantee (exact guard)

| Path | Guard |
|------|-------|
| Signal close / flat close / manual close | `close_strategy_position` UPDATE `WHERE status='open'` transitions exactly once → `is_full AND realized_pnl_f IS NOT NULL` → one `_book_realized_pnl` call |
| Reconciler direct close (PnL known) | Same — `skip_exchange=True` + non-null `realized_pnl` → `close_strategy_position` books at close time |
| Reconciler direct close (PnL unconfirmed) | `skip_exchange=True` + `realized_pnl=None` → `close_strategy_position` sets `pnl_realized=NULL` (CASE guard) → does NOT book; `_recover_manual_close_pnl` handles later |
| `_recover_manual_close_pnl` backfill | UPDATE `WHERE pnl_realized IS NULL` + RETURNING — only rows actually updated (NULL→value transition) trigger `_book_realized_pnl` |
| Partial reduce | `is_full=False` → booking skipped |
| Flip | Opposite-side leg closed via `close_strategy_position(skip_exchange=True, realized_pnl=flip_pnl)` → books once; fallback `_book_realized_pnl` if no opposite row found |

---

## Out of scope (per spec)

- No DB migration. `webhook_enabled` column left in place (unused). `order-generator` untouched.
- PUT-site drawdown re-check on `max_drawdown_pct` tightening remains open.
- Phase 2 (lifecycle endpoints, `webhook_enabled` gate removal, UI) — awaiting human approval.
