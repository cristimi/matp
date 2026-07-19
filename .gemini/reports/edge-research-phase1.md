# Edge research phase 1: momentum + funding harvest backtests (2026-07-19)

Branch: `feat/edge-research`. Follow-up to the honest portfolio analysis: instead of
another LLM-reads-indicators strategy, test two hypotheses with a *structural* reason
to exist, on real data with real costs, before any platform work. User chose to run
both in parallel and let the numbers decide.

Method: offline scripts in `research/` executed inside the `strategy-tester`
container (has pandas + network; repo not mounted, so scripts are piped via stdin).
Data: Binance USDT-perps, 12 liquid coins (BTC, ETH, SOL, BNB, XRP, DOGE, ADA,
AVAX, LINK, LTC, DOT, NEAR), 2021-01-01 → 2026-07-19: daily klines + full 8h
funding history (~6,100 funding points per coin).

```
$ docker compose exec -T strategy-tester python - < research/fetch_data.py
BTCUSDT: 2026 days (2021-01-01..2026-07-19), 6077 funding points
... (all 12 symbols identical coverage)
```

## Backtest 1 — time-series momentum (30d/90d, daily, 1-day signal lag, fees 0.08%/side + real funding applied to positions)

```
                         CAGR    vol sharpe   maxDD final_$1 years
TSMOM 30d long/short   +18.4%  60.3%   0.59  -63.3%     2.56   5.6
TSMOM 30d long-only    +30.8%  44.2%   0.83  -56.2%     4.43   5.6
TSMOM 90d long/short    -7.0%  58.2%   0.17  -81.1%     0.67   5.6
TSMOM 90d long-only     +6.5%  44.4%   0.37  -63.3%     1.42   5.6
BTC buy&hold           +15.3%  57.7%   0.53  -76.7%     2.20   5.5
Equal-weight buy&hold  +41.3%  78.7%   0.83  -80.9%     6.81   5.5

TSMOM 30d long-only by year: 2021 +357.0%, 2022 -36.6%, 2023 +83.9%,
                             2024 +49.3%, 2025 -22.9%, 2026 -27.8%
Equal-weight B&H by year:    2021 +1261.6%, 2022 -74.2%, 2023 +151.8%,
                             2024 +89.3%, 2025 -38.0%, 2026 -34.4%
```

Honesty check — excluding the 2021 bull:

```
TSMOM 30d long-only:   ex-2021 total  -3.1%, CAGR  -0.7%
TSMOM 30d long/short:  ex-2021 total -30.3%, CAGR  -7.6%
Equal-weight buy&hold: ex-2021 total -50.0%, CAGR -14.1%
```

**Read:** 30d long-only momentum is real *relative* edge — it matches the basket's
Sharpe (0.83) with a 25pp shallower drawdown and holds ~flat (−0.7%/yr) through a
period where holding crypto lost 14%/yr. It is NOT an absolute-return edge: all
excess over cash came from 2021. 90d momentum is dead on this universe.

## Backtest 2 — delta-neutral funding harvest (short perp + long spot while trailing 3d funding annualized > enter; hysteresis exit; cost per episode on all 4 legs)

v1 (enter 20%/exit 5%, 0.4%/episode) produced the key diagnosis: **gross funding
+69.3% of notional over 5.5y — the premium exists — but 547 churny episodes ate
−73.8% in costs.** Refined grid (in-sample selection, flagged as such):

```
enter/exit  cost  episodes in_mkt  gross   fees net/yr(notional) net/yr(2x cap)  maxDD
    20%/5% 0.40%       547    29% +69.3% -73.8%            -0.8%          -0.4% -24.0%
   20%/10% 0.40%       501    25% +67.6% -67.8%            -0.0%          -0.0% -20.4%
   40%/10% 0.40%       329    20% +62.3% -44.2%            +3.0%          +1.6%  -5.7%
   60%/10% 0.40%       239    17% +56.2% -32.7%            +3.9%          +2.0%  -3.1%
   40%/10% 0.20%       329    20% +62.3% -22.1%            +6.3%          +3.4%  -0.7%
   60%/10% 0.20%       239    17% +56.2% -16.4%            +6.2%          +3.3%  -0.7%

By year (40%/10%, 0.4%): 2021 +25.2%, 2022 +0.0%, 2023 +0.1%,
                         2024 -4.4%, 2025 +0.0%, 2026 +0.0%
```

**Read:** as an always-on strategy this returns cash-like money (≤3.4%/yr on
capital) — not competitive. But the premium is real and *regime-gated*: in the 2021
euphoria it paid +25% on notional in a year, delta-neutral. Worth keeping as an
opportunistic tool armed only when funding runs hot for days, not as a standing
allocation. Basis-path risk between the legs is not yet modeled.

## Verdict vs. the 10y US-index benchmark

Neither hypothesis beats an S&P index fund on the evidence so far — that remains
the honest baseline for investment capital. What survives phase 1: (a) if crypto
exposure is held at all, 30d momentum-gating strictly dominates naive holding;
(b) delta-neutral funding harvest is a real but episodic premium worth arming in
euphoric regimes. Both are mechanical and therefore properly backtestable —
unlike the LLM-discretionary strategies.

## Next steps (recorded in research/README.md)

Walk-forward validation (fit 2021–2023, test 2024–2026); Hyperliquid
funding/fee validation (hourly funding, lower fees); weekly + vol-targeted
momentum variants; basis-path modeling for the funding trade.
