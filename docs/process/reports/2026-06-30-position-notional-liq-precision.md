# Position Row: Notional + PnL%, Live Liquidation Price, Exchange-Tick Precision

**Date:** 2026-06-30  
**Scope:** Four phased changes to the position card in StrategyTree — notional exposure,
PnL as absolute+margin%, live liquidation price, and exchange-tick precision normalization.

---

## Phase 1 — Notional + PnL% (pure client-side)

### Changes made

**`dashboard-ui/src/pages/StrategyTree.tsx`**

Added two module-level helpers before `PositionCard`:

- `fmtMoney(v)` — compact money format: `$NNN` under $10k, `$X.Xk` under $1M, `$X.XM` above.
- `fmtPnlPct(pnl, margin)` — returns ` (+X.XX%)` suffix string; empty string when pnl/margin
  is null or zero.

Inside `PositionCard`, added computed values:

- `margin = entry_price × size / leverage`
- `notionalValue` = `size × mark_price` (open) or `size × (closing_price ?? entry_price)` (closed)
- `notionalStr` = `≈$NNN` formatted via `fmtMoney`
- `pnlPctSuffix`, `unrealizedPct`, `realizedPct` from `fmtPnlPct`

**Top row:** notional rendered as a muted `var(--dim)` 11px span after the size.  
**Top-row PnL:** appends `pnlPctSuffix`, e.g. `-0.22 (-0.75%)`.  
**Open detail:** added `Notional` KV; `Unrealized` KV appends `unrealizedPct`.  
**Closed detail:** added `Notional` KV; `Realized` KV appends `realizedPct`.

### Verification

```
$ docker compose ps dashboard-ui
NAME                  IMAGE               COMMAND                  SERVICE        CREATED          STATUS         PORTS
matp-dashboard-ui-1   matp-dashboard-ui   "/docker-entrypoint.…"   dashboard-ui   ~30 seconds ago  Up             80/tcp, 3000/tcp

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-B1Cr5nxP.js

$ docker compose exec -T dashboard-ui sh -c \
  'grep -oa "Notional" /usr/share/nginx/html/assets/index-B1Cr5nxP.js | wc -l'
2

$ docker compose exec -T dashboard-ui sh -c \
  'grep -oa "≈" /usr/share/nginx/html/assets/index-B1Cr5nxP.js | wc -l'
1
```

"Notional" appears twice (open + closed detail panels). The `≈` character (notional prefix in
`fmtMoney`) appears once — minifier inlined the function into a single call site. Container is
running, no type errors (tsc + vite build succeeded in ~175s).

---

## Phase 2 — Live liquidation price

### Changes made

**`order-executor/app/models.py`** — added `liquidation_price: Optional[Decimal] = None` to `Position`.

**`order-executor/app/adapters/hyperliquid.py`** — in `get_open_positions`, populated from `p.get("liquidationPx")`.

**`order-executor/app/adapters/blofin.py`** — in `get_open_positions`, populated from `p.get("liquidationPrice")` (raw Blofin field name confirmed by inspecting the live API response; earlier guess of `liqPx` was wrong — caught and fixed before shipping api/ui).

**`dashboard-api/src/livePnl.ts`** — extended `PnlSnapshot.positions` type and the local `positionsSnap` variable to include `liquidation_price: number | null`; fan-out extracts it from the executor response and stores in the snapshot.

**`dashboard-api/src/routes/strategies.ts`** — for open positions, `liquidation_price` is now sourced from `posSnap.liquidation_price` (live snapshot), falling back to the DB column for positions without a snapshot match.

**`dashboard-ui/src/hooks/useLivePnl.ts`** — extended `PnlSnapshot.positions` type to include `liquidation_price: number | null`.

**`dashboard-ui/src/pages/StrategyTree.tsx`** — added `displayLiqPrice = posSnap?.liquidation_price ?? p.liquidation_price`; Liq KV uses `displayLiqPrice`; price strip adds `Liq <price>` for open positions.

### Verification

```
# Executor — Blofin liq prices now populated:
$ docker compose exec nginx wget -qO- "http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/positions" | python3 -m json.tool | grep -iE "symbol|liquidation"
"symbol": "SUI-USDT",
"liquidation_price": "0.630063450498539631"
"symbol": "HYPE-USDT",
"liquidation_price": "71.162017273900526159"

# Executor — Hyperliquid liq price:
$ docker compose exec nginx wget -qO- "http://order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/positions" | python3 -m json.tool | grep -iE "symbol|liquidation"
"symbol": "BTC-USDT",
"liquidation_price": "57711.5864303797"

# dashboard-api — liq flows through to positions payload:
$ docker compose exec nginx wget -qO- "http://dashboard-api:8003/strategies/tv-btc-test-hl-94e1/positions?scope=open" | python3 -m json.tool | grep -iE "base_asset|liquidation"
"base_asset": "BTC",
"liquidation_price": 57711.5864303797,

$ docker compose exec nginx wget -qO- "http://dashboard-api:8003/strategies/hype-test-7db4/positions?scope=open" | python3 -m json.tool | grep -iE "base_asset|liquidation"
"base_asset": "HYPE",
"liquidation_price": 71.16201727390053,

$ docker compose exec nginx wget -qO- "http://dashboard-api:8003/strategies/sui-manual-59d9/positions?scope=open" | python3 -m json.tool | grep -iE "base_asset|liquidation"
"base_asset": "SUI",
"liquidation_price": 0.6300634504985396,

# UI — new bundle with Liq in price strip:
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-C6BmIfpC.js
$ docker compose exec -T dashboard-ui sh -c 'grep -oa "Liq " /usr/share/nginx/html/assets/index-C6BmIfpC.js | wc -l'
1
```

All three services healthy. Dead `strategy_positions.liquidation_price` DB column is bypassed for
open positions — value now comes live from the exchange tick snapshot.

---

## Phase 3 — Exchange-tick precision normalization

### Changes made

**`order-executor/app/adapters/base.py`** — added non-abstract `get_instrument_specs() → {}` default.

**`order-executor/app/adapters/blofin.py`** — implemented `get_instrument_specs()`: iterates the already-cached instrument dict; per symbol computes `tick` from `tickSize` and `size_dp` from `log10(lotSize × contractValue)`.

**`order-executor/app/adapters/hyperliquid.py`** — implemented `get_instrument_specs()`: for each coin in `_asset_cache`, returns `{ price: { mode: sigfig, sigfigs: 5 }, size: { dp: szDecimals } }`.

**`order-executor/app/main.py`** — added `GET /accounts/{id}/instrument-specs` endpoint.

**`dashboard-api/src/routes/strategies.ts`** — added `specCache` (Map, 1-hour TTL) and `fetchSpecsForAccount()`. In `GET /:id/positions`: fetches specs for all unique `account_id`s, then attaches `price_mode`, `price_tick`, `price_sigfigs`, `size_dp` to each returned row.

**`dashboard-ui/src/api.ts`** — added `price_mode`, `price_tick`, `price_sigfigs`, `size_dp` to `TreePosition`.

**`dashboard-ui/src/utils/precision.ts`** — rewrote: added `countDecimals(tick)` and `toSigFigs(value, sigfigs)` helpers; `formatPrice` checks `price_mode` → tick rounding or sigfig rendering before falling back to static `RULES` map; `formatSize` uses `size_dp` from spec with trailing-zero trimming.

**`dashboard-ui/src/pages/StrategyTree.tsx`** — built `priceSpec`/`sizeSpec` from position fields; threaded through all `formatPrice`/`formatSize` call sites in `PositionCard` (price strip, both detail panels) and `OrderRow` (header + key details + full info).

### Verification

```
# Executor — Blofin specs (tick mode per symbol):
$ docker compose exec nginx wget -qO- "http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/instrument-specs" | python3 -m json.tool | grep -E '"BTC-USDT|SUI-USDT|HYPE-USDT' -A4
"BTC-USDT": { "price": { "mode": "tick", "tick": 0.1 }, "size": { "dp": 4 } }
"SUI-USDT": { "price": { "mode": "tick", "tick": 0.0001 }, "size": { "dp": 0 } }
"HYPE-USDT": { "price": { "mode": "tick", "tick": 0.001 }, "size": { "dp": 1 } }

# Executor — HL specs (sigfig mode):
$ docker compose exec nginx wget -qO- "http://order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/instrument-specs" | python3 -m json.tool | grep -E '"BTC-USDT' -A6
"BTC-USDT": { "price": { "mode": "sigfig", "sigfigs": 5 }, "size": { "dp": 5 } }

# dashboard-api — spec fields in positions payload:
$ docker compose exec nginx wget -qO- "http://dashboard-api:8003/strategies/tv-btc-test-hl-94e1/positions?scope=open" | python3 -m json.tool | grep -iE 'price_mode|price_tick|price_sigfigs|size_dp|base_asset'
"base_asset": "BTC",
"price_mode": "sigfig",
"price_tick": null,
"price_sigfigs": 5,
"size_dp": 5

$ docker compose exec nginx wget -qO- "http://dashboard-api:8003/strategies/hype-test-7db4/positions?scope=open" | ... | grep price
"price_mode": "tick", "price_tick": 0.001, "price_sigfigs": null, "size_dp": 1

$ docker compose exec nginx wget -qO- "http://dashboard-api:8003/strategies/sui-manual-59d9/positions?scope=open" | ... | grep price
"price_mode": "tick", "price_tick": 0.0001, "price_sigfigs": null, "size_dp": 0

# UI — new bundle:
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-BkthsO6V.js
$ docker compose exec -T dashboard-ui sh -c 'grep -oa "sigfig\|price_mode" /usr/share/nginx/html/assets/index-BkthsO6V.js | sort -u'
price_mode
sigfig
```

Expected display values (confirmed by computing the formatting math):
- BTC/HL entry 58439.0 → sigfig-5 → `58439` (was `58439.00`)
- BTC/HL liq 57711.586 → sigfig-5 → `57712`
- HYPE/Blofin entry 65.165 → tick 0.001 → `65.165` (was `65.80` at 2dp default)
- HYPE/Blofin liq 71.162 → tick 0.001 → `71.162`
- SUI/Blofin entry 0.6951 → tick 0.0001 → `0.6951` (was `0.70`)
- SUI/Blofin liq 0.6301 → tick 0.0001 → `0.6301`
