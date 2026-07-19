# Edge research phase 4: funding-adjusted study (2026-07-19)

Branch: `feat/edge-research`. Applies real Binance 8h funding history (fetched
for all 12 unlock-universe alts + BTC, ~4–6k settlement points each) to the two
survivors of phase 3: the secular short-VC-coins bleed and the unlock run-up
effect. Script: `research/funding_adjusted_bleed.py`.

## Actual output (abridged)

```
=== per-alt mean funding, annualized (positive = shorts RECEIVE) ===
APE -6.0%  APT -1.4%  ARB +9.1%  BLUR -9.2%  GMT -7.3%  JTO -0.9%
JUP -0.7%  PYTH +5.8%  SEI -5.1%  STRK +3.5%  SUI +5.5%  TIA -2.6%
basket mean: -0.8%/yr

=== A. secular-bleed basket (short 12 alts / long BTC, daily rebalance) ===
  span: 2022-03-15 .. 2026-07-19
  price legs only:     CAGR -27.6%  vol 79.6%  sharpe 0.05  maxDD -96.8%
  WITH funding:        CAGR -36.7%  vol 80.3%  sharpe -0.10  maxDD -97.6%
  funding drag alone:  mean -12.1%/yr on notional
  net by year: 2022 -94%, 2023 -15%, 2024 +29%, 2025 +144%, 2026 -15%

=== B. unlock run-up hedged short D-8..D-1, per event ===
  price legs + fees  N=311 mean +2.26% median +2.43% win 63% t=3.81
  WITH funding       N=311 mean +2.13% median +2.34% win 64% t=3.58
  net by year: 2022 +73%(20) 2023 +168%(50) 2024 +271%(90) 2025 +37%(105) 2026 +113%(46)
```

## Findings

1. **The secular-bleed short is dead — and the mechanism is a surprise.** The
   hypothesis was that negative funding (shorts paying) explains why the 12/12
   bleed persists un-arbed. Wrong: alt funding is mild (−0.8%/yr basket mean).
   The real killers: **volatility drag** — continuously shorting ~80%-vol
   assets loses ≈σ²/2 geometrically even while the assets fall in log terms
   (price legs alone: −27.6% CAGR; 2022: −94%, when the early basket was just
   GMT+APE during their pumps) — plus the long-BTC hedge leg paying BTC's
   persistently positive funding (−12.1%/yr total drag). Longs genuinely lose
   on these tokens; shorts still can't collect. That asymmetry *is* the answer
   to why the pattern persists: nobody can harvest it directly.

2. **The unlock run-up trade survives its funding audit.** Seven-day hedged
   shorts cost only ~0.13%/event in net funding: +2.26% → **+2.13% mean
   per event, t=3.58, 64% win, positive-sum in every year 2022–2026**. Unlike
   the continuous short, event-limited exposure (7 days at a time) sidesteps
   the volatility-drag compounding. This is now the single surviving edge
   candidate of the entire research program.

3. Remaining gates before any capital discussion (recorded in README):
   an **overlap-free portfolio sim** — 2025 saw 105 events, so clustered
   windows overlap and per-event sums overstate a realizable equity curve;
   **walk-forward**; and the phase-3 selection caveat (the run-up window was
   one of five examined).

Data: 13 funding histories fetched and cached to /tmp/edge-data (same CSV
format as phase 1). No service code touched.
