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

## Phase 2 — Reconciler: always emit a correct, linked closing order

_Pending confirmation._

## Phase 3 — Timeline label: full close vs partial close

_Pending confirmation._

## Phase 4 — Backfill historical closed positions

_Pending confirmation._
