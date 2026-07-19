# Spread harvest phase 1: monitor + armed planner (2026-07-19)

Branch: `feat/spread-harvest`. Implements the staged pipeline for the strategy
that cleared all four research gates (edge-research phases 5–6: HL-vs-Blofin
funding-spread capture, walk-forward OOS +14.2%/yr on notional). Design record:
`docs/design/SPREAD_HARVEST.md`. This phase is read-only — nothing places
orders; it watches, arms, and notifies.

## Built

- **`db/migrations/058_spread_plans.sql`** (applied): two-leg plan table,
  `armed|executed|expired|cancelled` lifecycle, one armed plan per coin.
- **`ai-signal-generator/app/spread_monitor.py`** — hourly, per 24-coin
  universe: fetches last ~168h of HL hourly funding + Blofin settlements
  (mixed 4h/8h cadence handled by gap inference, same math as
  `research/spread_gates.py`), computes the trailing annualized spread,
  hysteresis per coin (enter |trailing| > 50%/yr, exit < 10%/yr — the config
  the walk-forward picked in every fold), state in Redis. On cool→hot with a
  free slot (max 3 armed): builds the delta-neutral plan — short the
  higher-funding venue, live book walk on BOTH venues to $100/leg, est. daily
  collect, breakeven, ±25% abort prices per gate 4 — persists and emits
  `spread.hot`. On hot→cool: expires armed plans, emits `spread.cooled`.
- **Endpoints**: `GET /internal/spread-monitor/status`,
  `GET /internal/spread-harvest/plans`,
  `POST /internal/spread-harvest/plan/{coin}?persist=` (preview/test hook).
- **notification-service**: renders + dedup for both events; armed body carries
  legs, $/day, breakeven, abort band, "Confirm to execute".
- Config: `SPREAD_*` env knobs (capital default $200 → $100/leg at 2x,
  half the capital held back as top-up buffer per the design).

## Verification (live, branch build deployed)

First cycle: `Spread monitor cycle: 24 coins checked, hot=none` — all coins
computed; widest current spreads: TIA −44.4%/yr (correctly still cool under the
50% gate), DOGE −20.4%, ADA −19.0%.

On-demand TIA plan against live books:

```
{"coin":"TIA","trailing_spread_ann":-0.4435,
 "short_venue":"blofin","long_venue":"hyperliquid",   <- direction correctly
 "notional_usd":100.0,"leg_leverage":2,                  inverted for negative spread
 "hl_price":0.35987,"blofin_price":0.3597,
 "hl_slippage_bps":0.0,"blofin_slippage_bps":0.0,
 "est_daily_usd":0.1215,"est_roundtrip_usd":0.3,"breakeven_days":2.47,
 "abort_up_price":0.44973,"abort_down_price":0.26984,
 "details":{"basis_bps":4.73,"abort_pct":0.25}}
```

(basis 4.7bps consistent with gate 3's TIA measurement; both books absorb
$100/leg at 0 bps.)

Lifecycle: two `persist=true` arms → first auto-`expired`, second `armed`
(partial unique index enforced); test rows `cancelled` after. Notification
pipeline end-to-end (synthetic TEST3 event):

```
⚡ Spread hot: TEST3 +62.0%/yr | HL-vs-Blofin funding spread +62.0%/yr (7d
trail). ARMED: $100/leg short hyperliquid / long blofin, ~$0.17/day, breakeven
1.8d, abort ±25%. Confirm to execute. | sent
```

## Deployment note

Live `ai-signal-generator` + `notification-service` run the
**feat/spread-harvest** build (additive, read-only). A redeploy from `main`
drops the spread monitor until the feature merges.

## Next phases (design doc)

2. Paired two-venue position model in order-executor (leg-failure rollback).
3. One-tap execute + the two gate-4 hard requirements: ±25% abort watcher and
   auto margin top-up. **Blocked on operator decisions: funded non-demo
   accounts on BOTH venues + capital.**
4. Full auto after one real episode handled cleanly.
