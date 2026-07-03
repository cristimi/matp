# Fix: guaranteed safety-SL could land beyond the real liquidation price

Date: 2026-07-03
Fixes: docs/process/reports/safety-sl-vs-liq-investigation.md
Scope: Phase 1 (source real MMR) + Phase 2 (use it in the SL formula). Phase 3
(after-fill exchange-side guard) is deferred pending separate confirmation —
not started.

## Root cause (recap)

`compute_guaranteed_sl()` used a hardcoded flat `MMR = 0.01` for every symbol/leverage.
Real maintenance-margin rate rises toward a symbol's exchange max leverage; at 40x on
BTC (Hyperliquid, which caps BTC at 40x) real MMR was ~1.30%, not 1%, so the computed
"safety" SL landed 62618.395 — past the real liquidation price 62435.7063. The
`MIN_SAFETY_SL_DIST=0.005` floor was also latently unsafe: it could widen the SL
*beyond* whatever the formula itself said was safe at high leverage, once real MMR was
sourced correctly.

## Phase 1 — real MMR source

### Hyperliquid

`meta` gives each coin's `marginTableId`; `marginTable` (queried by that id) gives a
tiered ladder of `{lowerBound, maxLeverage}`. MMR for the tier a position's notional
falls into is Hyperliquid's standard formula `1 / (2 * tier_maxLeverage)`.

Verified against the live BTC 40x position (Hyperliquid **testnet**, since that's the
account actually in use — `hyperliquid-hyperliquid-hqdy`):

```
docker compose exec nginx wget -qO- --post-data='{"type":"meta"}' ... https://api.hyperliquid-testnet.xyz/info
  -> BTC: {'szDecimals': 5, 'name': 'BTC', 'maxLeverage': 40, 'marginTableId': 54}
docker compose exec nginx wget -qO- --post-data='{"type":"marginTable","id":54}' ... https://api.hyperliquid-testnet.xyz/info
  -> {"description":"tiered 40x","marginTiers":[{"lowerBound":"0.0","maxLeverage":40},
      {"lowerBound":"10000.0","maxLeverage":25},{"lowerBound":"50000.0","maxLeverage":10}]}
```

Base tier `maxLeverage=40` → theoretical `MMR = 1/(2*40) = 1.25%`. Compared against the
live position's real liquidation price at verification time:

```
docker compose exec nginx wget -qO- http://order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/positions
  -> [{"symbol":"BTC-USDT","side":"short","size":"0.02","entry_price":"61693.0","leverage":40,
      "mark_price":"61699.0","unrealized_pnl":"-0.11992","liquidation_price":"62438.7521481482"}]
```

Real liq distance = (62438.7521 − 61693.0) / 61693.0 = **1.2087%**. Implied real MMR
(back-solved from `1/L − dist`) = 2.5% − 1.2087% = **1.2913%** — a ~0.041% gap over the
theoretical 1.25%, consistent with the investigation's ~0.046% finding for the earlier
snapshot of the same position (fee/funding folded into HL's live `liquidationPx`, which
the static formula can't model). This confirms the formula needs an explicit
conservatism buffer, not just the raw `1/(2*maxLeverage)` value — see `MMR_CONSERVATISM_BUFFER`
below.

### Blofin

Contrary to the investigation's "needs a lookup to confirm" note: Blofin **does** expose
real tiered MMR, via an undocumented-in-`blofin.py` but live public endpoint:

```
GET /api/v1/market/position-tiers?instId={symbol}&marginMode={cross|isolated}
```

No auth required. Tier boundaries (`minSize`/`maxSize`) are in **quote-currency
notional**, not contracts — confirmed by cross-checking against `maxLeverage` per tier,
which matches the instrument's overall max leverage at the lowest tier. Example (prod
`openapi.blofin.com`, BTC-USDT, cross):

```json
{"symbol":"BTC-USDT","marginMode":"cross","minSize":"0","maxSize":"2500","maintenanceMarginRate":"0.003","maxLeverage":"150"}
```

The account actually in use (`blofin-blofin-demo-v5vr`) is a **demo** account with its
own, different tier ladder (`demo-trading-api.blofin.com`):

```json
{"symbol":"HYPE-USDT","marginMode":"isolated","minSize":"0","maxSize":"1000","maintenanceMarginRate":"0.0067","maxLeverage":"75"}
```

The adapter queries each account's own `base_url`, so demo vs. prod tier differences are
handled automatically, not hardcoded.

### New route + adapter methods

- `ExchangeAdapter.get_maintenance_margin_rate(symbol, notional, margin_mode="isolated") -> Optional[float]`
  added to `order-executor/app/adapters/base.py` (default `None`), implemented in both
  `hyperliquid.py` and `blofin.py`.
- `MMR_CONSERVATISM_BUFFER = 0.0015` (0.15%) added once in `base.py`, applied to every
  derived/tiered MMR in both adapters. Sized to comfortably cover the ~0.041–0.046% gap
  observed between theoretical/tiered MMR and Hyperliquid's live liquidation price.
- New route: `GET /accounts/{account_id}/maintenance-margin/{symbol}?notional=X&margin_mode=Y`
  in `order-executor/app/main.py`, following the existing GET-route pattern.

Live verification (pasted raw JSON, current containers):

```
$ docker compose exec nginx wget -qO- "http://order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/maintenance-margin/BTC-USDT?notional=1233.86"
{"symbol":"BTC-USDT","notional":1233.86,"maintenance_margin_rate":0.014}
   # = 1/(2*40) + 0.0015 = 0.0125 + 0.0015 = 0.014 ✓

$ docker compose exec nginx wget -qO- "http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/maintenance-margin/HYPE-USDT?notional=202.01&margin_mode=isolated"
{"symbol":"HYPE-USDT","notional":202.01,"maintenance_margin_rate":0.0082}
   # = 0.0067 (demo tier) + 0.0015 = 0.0082 ✓
```

## Phase 2 — wired into the SL formula

### `compute_guaranteed_sl()` (`order-listener/app/webhook_handler.py`)

- New required `mmr` parameter — the live-or-fallback rate, resolved by the call site.
- `sl_distance` is now the natural `(1/L − mmr)` value used **as-is** whenever positive.
  The old `max(natural, MIN_SAFETY_SL_DIST)` floor is removed for the positive case:
  mathematically, any floor that must never exceed `natural` (the safety requirement)
  is a no-op for positive `natural` — `max(natural, min(floor, natural)) ≡ natural`. So
  there is no leverage-aware variant of that floor that both (a) does something and
  (b) is safe; removing the override *is* the fix. Only the degenerate case
  (`natural <= 0`, mmr consumed the whole `1/L` headroom — shouldn't happen given
  exchange max-leverage bounds) falls back to a fixed tiny distance
  (`DEGENERATE_SL_DIST = 0.05%`), logged as an error.

### Call site (`webhook_handler.py`, guaranteed-SL injection block)

- Computes `notional = payload.size * entry_ref` (size is already finalized by the
  margin-per-trade clamp at this point in the flow).
- Calls new `executor_client.get_maintenance_margin(account_id, symbol, notional, margin_mode)`
  (mirrors the existing GET-helper pattern; never raises, returns `None` on any failure).
- On `None`, falls back to `config.fallback_mmr(effective_leverage)` — assumes
  `effective_leverage` sits at the symbol's real max leverage (`MMR ≈ 1/(2*L) + buffer`),
  which is always at least as conservative as any live value for that leverage, since a
  symbol's real max leverage can only be ≥ the leverage actually granted.
- `sl_source` semantics (`'strategy'` / `'liquidation_safe'`) are unchanged.
- `signal_metadata` now also carries `mmr` (rounded) and `mmr_source` (`"live"` /
  `"fallback"`) for auditability.

### Verification — reproduced calc, BTC 40x short

```
$ docker compose exec -T order-listener python3 -c "
from app.webhook_handler import compute_guaranteed_sl
sl, src = compute_guaranteed_sl(61693.0, 40, 'short', None, 0.014)
print(sl, src)
"
62371.6 liquidation_safe
```

`62371.6 < 62438.7521` (real live liquidation price) → **SAFE**, vs. the old buggy
`62618.395` which was past it. Margin to real liquidation: 67.15 (0.11%).

### Verification — HYPE-USDT 10x short (previously-safe position, unchanged/safer)

```
$ docker compose exec -T order-listener python3 -c "
from app.webhook_handler import compute_guaranteed_sl
sl, src = compute_guaranteed_sl(65.165, 10, 'short', None, 0.0082)
print(sl, src)
"
71.1471 liquidation_safe
```

`71.1471 < 71.16202` (real live liquidation price) → **SAFE**. (Old formula gave
71.02985, also safe but with more margin — the new value is tighter/more accurate
since it uses the real per-account tier MMR (0.67%) instead of the flat 1% guess, but
still lands inside real liquidation.)

### Verification — full listener → executor integration path

```
$ docker compose exec -T order-listener python3 -c "
import asyncio
from app.executor_client import get_maintenance_margin
async def main():
    print(await get_maintenance_margin('hyperliquid-hyperliquid-hqdy', 'BTC-USDT', 0.02*61693.0, 'isolated'))
    print(await get_maintenance_margin('blofin-blofin-demo-v5vr', 'HYPE-USDT', 3.10*65.165, 'isolated'))
asyncio.run(main())
"
0.014
0.0082
```

Matches the direct executor-route values exactly — confirms the listener→executor HTTP
path (not just the executor route in isolation) is wired correctly.

### Verification — fallback path (unreachable/unknown account)

```
$ docker compose exec -T order-listener python3 -c "
import asyncio
from app.executor_client import get_maintenance_margin
from app.config import fallback_mmr
async def main():
    print(await get_maintenance_margin('nonexistent-account-id', 'BTC-USDT', 1000.0, 'isolated'))
    print(fallback_mmr(40))
asyncio.run(main())
"
None
0.014
```

`get_maintenance_margin` never raises and returns `None` cleanly for an unreachable
account; `fallback_mmr(40)` happens to equal the live value in this specific case
(because 40x *is* BTC's real max leverage on Hyperliquid) — for lower-leverage positions
the fallback is deliberately more conservative than the live value (e.g.
`fallback_mmr(10) = 5.15%` vs. HYPE's live `0.82%`), trading tighter/more premature
stops for guaranteed safety when the live lookup is unavailable.

## Deploys

```
./scripts/redeploy.sh order-executor   # Phase 1
./scripts/redeploy.sh order-listener   # Phase 2
```

Both containers confirmed `Up`/healthy after redeploy; `order-listener` `/health`
returned `{"status":"ok","service":"order-listener"}` post-deploy.

## Not done (deferred, per instructions)

Phase 3 (after-fill cross-check against the exchange's own reported
`liquidation_price`, and backfilling `strategy_positions.liquidation_price`) was
explicitly gated on confirmation before starting and has not been started.
