# Live PnL: Auto-show new/closed positions + tighten tick to ~1s

**Date:** 2026-06-29  
**Services redeployed:** `dashboard-api`, `dashboard-ui`  
**New UI asset:** `index-DeZibwMm.js`

---

## Changes

### 1. Server — `dashboard-api/src/livePnl.ts`

- `PNL_TICK_MS` default changed `2500 → 1000` (still env-overridable via `PNL_TICK_MS`).
- `STALE_MS` stays `PNL_TICK_MS * 4` → now 4 s.
- `PnlSnapshot.strategies` extended: `{ open_pnl: number; position_ids: string[] }`.
- Added a **pre-pass** in `tick()` before the executor fanout to collect all open position IDs per strategy from DB rows. This means:
  - A strategy's `position_ids` is populated from DB truth (all open rows), not gated on executor data.
  - `open_pnl` still deduplicates by `account:symbol:side` — unchanged logic.
  - One executor fanout per unique account per tick — unchanged.

### 2. UI type — `dashboard-ui/src/hooks/useLivePnl.ts`

- `PnlSnapshot.strategies` mirrored: `{ open_pnl: number; position_ids: string[] }`.

### 3. UI logic — `dashboard-ui/src/pages/StrategyTree.tsx` (`StrategyCard`)

- **`hasOpen`** now derived from live snapshot when present:
  ```ts
  const livePids = livePnl?.strategies[s.id]?.position_ids;
  const hasOpen = livePids ? livePids.length > 0 : s.open_positions_count > 0;
  ```
  So a strategy with 0→1 open positions lights the green dot and shows the Open PnL cell without a page reload.

- **`pidKey`**: stable string signature of the live position-id set:
  ```ts
  const pidKey = livePids ? [...livePids].sort().join(',') : null;
  ```

- **Membership-change `useEffect`**: keyed on `pidKey`. When the set changes and the card
  is expanded, refetches the positions at the current scope (`open` or `all`). Guards:
  - `lastRefetchedKey` ref prevents repeated fetches for the same `pidKey`.
  - `loadingPos` guard prevents concurrent in-flight fetches.
  - Collapsed cards are skipped (`doFetchOpen/doFetchAll` handles the initial fetch on expand).

---

## Verification

### Redis snapshot — `position_ids` per strategy

```
$ docker compose exec -T redis redis-cli GET pnl:live:snapshot | python3 -m json.tool
{
    "ts": 1782758966660,
    "strategies": {
        "tv-btc-test-hl-94e1": {
            "open_pnl": 4.89088,
            "position_ids": [
                "575ab6b8-1a47-4f47-a8a9-26ff323955ea"
            ]
        },
        "hype-test-7db4": {
            "open_pnl": -0.31210000000002,
            "position_ids": [
                "abcc1b26-b404-4e77-8622-c3326deba7aa"
            ]
        }
    },
    "positions": {
        "575ab6b8-1a47-4f47-a8a9-26ff323955ea": {
            "mark_price": 59254,
            "unrealized_pnl": 4.89088
        },
        "abcc1b26-b404-4e77-8622-c3326deba7aa": {
            "mark_price": 65.90603333333334,
            "unrealized_pnl": -0.31210000000002
        }
    }
}
```

### Tick cadence — ~1 s

```
dashboard-api-1  | 2026-06-29T18:49:46.642Z [livePnl] tick: 2 open position(s), 2 account(s) fanned out
dashboard-api-1  | 2026-06-29T18:49:47.677Z [livePnl] tick: 2 open position(s), 2 account(s) fanned out
dashboard-api-1  | 2026-06-29T18:49:48.927Z [livePnl] tick: 2 open position(s), 2 account(s) fanned out
dashboard-api-1  | 2026-06-29T18:49:49.668Z [livePnl] tick: 2 open position(s), 2 account(s) fanned out
dashboard-api-1  | 2026-06-29T18:49:50.560Z [livePnl] tick: 2 open position(s), 2 account(s) fanned out
dashboard-api-1  | 2026-06-29T18:49:51.693Z [livePnl] tick: 2 open position(s), 2 account(s) fanned out
dashboard-api-1  | 2026-06-29T18:49:52.745Z [livePnl] tick: 2 open position(s), 2 account(s) fanned out
dashboard-api-1  | 2026-06-29T18:49:53.638Z [livePnl] tick: 2 open position(s), 2 account(s) fanned out
dashboard-api-1  | 2026-06-29T18:49:54.613Z [livePnl] tick: 2 open position(s), 2 account(s) fanned out
dashboard-api-1  | 2026-06-29T18:49:55.694Z [livePnl] tick: 2 open position(s), 2 account(s) fanned out
```

Average inter-tick: ~1.0 s. The extra latency vs. 1000 ms is executor HTTP call overhead (~0–250 ms per account).

### Live asset hash

```
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-DeZibwMm.js
```

### dashboard-api health

```
$ docker compose exec -T dashboard-api curl -sf http://localhost:8003/health
{"status":"ok","service":"dashboard-api"}
```

### Container states

```
$ docker compose ps dashboard-api dashboard-ui
matp-dashboard-api-1   matp-dashboard-api   Up (healthy)
matp-dashboard-ui-1    matp-dashboard-ui    Up
```

---

## Rate-limit note

At 1 s cadence with 2 accounts, the executor receives 2 calls/s. Both accounts are Hyperliquid and the public REST `/info` positions endpoint has no stated per-second limit (Hyperliquid docs: "no hard limit for info endpoints"). This is well within safe range. To back off: set `PNL_TICK_MS=2500` (or higher) in the compose env — `STALE_MS` tracks at `PNL_TICK_MS * 4` automatically.

---

## Behaviour after deploy

- **Green dot / Open PnL cell** appear within ~1 s of a position opening (live snapshot `position_ids.length > 0`), no L1 refetch required.
- **Expanded card** (open or all scope): when `pidKey` changes (new position in snapshot), the card refetches its position list. The `useEffect` is keyed on the sorted-join string — fires once per membership change, not once per tick. `lastRefetchedKey` ref prevents duplicate fetches if other deps re-trigger the effect.
- **Closed position row drops** automatically when the snapshot removes its ID from `position_ids` and the card refetches.
- Header `open_pnl` continues to update live on every tick as before.
- Per-strategy L2 refetch only fires on actual membership change; all connected WS clients share the single server-side ticker fanout.
