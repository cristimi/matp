# Edge research phase 6: all four gates for the funding-spread trade (2026-07-19)

Branch: `feat/edge-research`. Phase 5 named four gates before the cross-venue
funding-spread capture could be considered real. All four were run
(`research/spread_gates.py` + fetchers `fetch_blofin_funding.py`,
`fetch_basis_klines.py`; new data: full 3y Blofin 8h funding for 24 coins,
HL+Blofin hourly candles ~7mo for 12 coins, Binance 1h klines 3y for 12 coins).

## Gate 1 — Blofin validation (the real second leg): PASS

Blofin mixes 4h/8h settlement cadences on some coins — the loader infers each
settlement's interval from timestamp gaps before hourly-equivalencing.

```
      config            cex_leg  episodes net/yr  half1  half2
 72h 30%/10%             blofin      1150  +0.4%  -4.2%  +5.4%
 72h 50%/10%             blofin       341 +17.9% +19.6% +35.1%
168h 30%/10%             blofin       566 +16.6% +23.2% +27.5%
168h 50%/10%             blofin       118 +18.8% +29.0% +28.4%
(binance same-span comparators: +6.6% / +10.7% / +15.2% / +13.8%)
```

The Binance proxy was not flattering — the real HL-vs-Blofin spread is as good
or better at slow configs. Fast/low-threshold capture churns out on Blofin
(1150 episodes): only slow, high-threshold capture is robust on both legs.

## Gate 2 — walk-forward: PASS

```
-- CEX leg: blofin --  (config picked on fit data only; cash a candidate)
  fold 2024-07..2025-01: picked '168h 50%/10%' -> OOS +0.0%
  fold 2025-01..2025-07: picked '168h 50%/10%' -> OOS +7.1%
  fold 2025-07..2026-01: picked '168h 50%/10%' -> OOS +14.2%
  fold 2026-01..2027-01: picked '168h 50%/10%' -> OOS +7.0%
  stitched OOS total (2y): +28.4% on notional = +14.2%/yr
```

Same config selected in every fold (no parameter drift), every fold ≥ 0.
Binance-leg variant: +7.8%/yr OOS.

## Gate 3 — entry basis: PASS (noise, not drag)

HL-vs-Blofin hourly close basis over ~5,000 common hours per coin: majors
2–4bps std; small coins 10–26bps (BLUR carries a persistent +16bps HL premium).
Per-episode basis PnL for the 168h/30% episodes inside the candle window:
mean **+26.9bps**, std 59.7bps, min −22.7bps (N=21) vs ~40–50bps/episode
funding collect. Material variance, zero systematic drag.

## Gate 4 — margin: CONDITIONAL PASS — it defines the build

```
episodes measured: 106 (mean length 493h ≈ 20 days)
adverse for SHORT leg (max rise): p50 10.7%  p90 84.8%  p99 296.7%  max 631.7%
adverse for LONG leg (max fall):  p50  9.1%  p90 23.4%  p99  33.4%  max  35.0%
naive isolated-margin safe leverage: 0.3x  → unviable
```

The hot-spread coins are precisely the pump-prone ones. Fix: a **±25% abort
rule** — close both legs whenever price moves 25% from episode entry:

```
no abort:      42 episodes, sum net +113.1% over 3.1y (uncapped per-coin sim)
abort at ±25%: 92 starts, 50 aborted, sum net +96.8%  → retains 86% of P&L
```

With the abort rule, worst-case leg loss ≈25% + gap risk → 2–3x per-leg
leverage is safe with margin headroom. **The abort watcher and auto margin
top-up are hard requirements of any build.**

## Verdict

All gates pass; gate 4 conditions the design. Realistic expectation:
**+12–16%/yr on notional** (walk-forward OOS +14.2%/yr, minus the ~14% abort
haircut and basis variance), market-neutral, at ~1x capital-to-notional with 2x
legs. First strategy in the program to clear every honesty gate. Build shape:
the existing funding-harvest staged pipeline (monitor → armed plan → one-tap
confirm) fits unchanged; both legs are perps on already-adapted venues
(Hyperliquid + Blofin). Prerequisite before phase-3-style execution: funded
non-demo accounts on both venues and a capital decision.
