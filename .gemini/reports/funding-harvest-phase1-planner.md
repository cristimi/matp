# Funding harvest phase 1: staged automation — planner (2026-07-19)

Branch: `feat/funding-harvest`. User decision: automate the delta-neutral funding
harvest at the **staged "armed + confirm"** level (full pipeline computes and arms
the trade; execution requires one confirmation; full-auto only after the first real
regime validates it). Design record: `docs/design/FUNDING_HARVEST.md` — key calls:
single-venue Hyperliquid (spot Unit assets + perp), universe limited to coins with
liquid HL spot (BTC/ETH/SOL/DOGE), short perp at 2x + long spot equal notional,
Binance 3d funding stays the signal while HL hourly funding is the income side.

## Built in this phase

- **`db/migrations/057_funding_harvest_plans.sql`** (applied): plan table with
  `armed | executed | expired | cancelled` lifecycle, one armed plan per coin
  (partial unique index), full pricing snapshot per plan.
- **`ai-signal-generator/app/funding_harvest.py`** — the planner: resolves the HL
  Unit spot pair from `spotMeta`, walks the LIVE spot and perp books to the target
  notional (measured slippage, not assumed — Unit books are thin), prices both
  legs, computes entry/round-trip cost, daily funding income at HL's live hourly
  rate, and break-even days. Persists as `armed`, superseding any prior armed plan.
- **Monitor integration**: cool→hot on a supported coin builds+arms a plan and
  enriches the `funding.hot` push with the numbers; hot→cool expires armed plans
  and says how many. Unsupported coins still alert, flagged "manual assessment
  only". Planner failure degrades to the basic alert — never kills the monitor.
- **Endpoints**: `GET /internal/funding-harvest/plans`,
  `POST /internal/funding-harvest/plan/{coin}?persist=` (preview / test hook).
- **notification-service render**: armed body shows legs, $/day, HL vs signal
  rate, breakeven, "Confirm to execute".
- Config: `FUNDING_HARVEST_CAPITAL_USD` (default $150 → $100/leg),
  `FUNDING_HARVEST_PERP_LEVERAGE` (2), HL fee constants.

## Verification (live, branch build deployed)

On-demand plan against real HL books:

```
$ POST /internal/funding-harvest/plan/BTC
{"coin":"BTC","spot_pair":"UBTC/USDC (@142)","notional_usd":100.0,
 "spot_qty":0.00154998,"spot_price":64517.0,"perp_price":64547.0,
 "spot_slippage_bps":0.0,"perp_slippage_bps":0.0,
 "hl_funding_ann":0.1095,"est_roundtrip_usd":0.23,
 "est_daily_funding_usd":0.03,"breakeven_days":7.67}
```

(Today's quiet 11%/yr HL funding → 7.7-day breakeven — exactly why the 40% gate
exists; at regime levels breakeven drops under 2 days.)

Supersede + lifecycle: two `persist=true` arms → first row auto-`expired`, second
`armed`; test rows then `cancelled`. Enriched notification through the real
stream/render/push pipeline (synthetic TEST2 event):

```
🔥 Funding hot: TEST2 47.0%/yr | Signal 47.0%/yr (Binance 3d). ARMED: $100/leg
short TEST2 perp 2x + long UTEST2/USDC, ~$0.11/day at HL 41%/yr, breakeven 2.1d.
Confirm to execute. | sent
```

Monitor cycle clean after deploy: `12 coins checked, hot=none`.

## Deployment note

The live `ai-signal-generator` + `notification-service` containers currently run
the **feat/funding-harvest** build (strictly additive, read-only planner — no
order placement code exists yet). A redeploy from `main` would drop the planner
(but keep the monitor) until the feature merges.

## Next phases (per design doc)

2. HL adapter spot support + paired-position model (order-executor).
3. One-confirmation execute + unwind + margin top-up watcher. Blocked on an
   operator decision: funded mainnet HL account (both accounts are demo; HL
   testnet has no Unit spot).
4. Full auto after the first real regime is handled cleanly.
