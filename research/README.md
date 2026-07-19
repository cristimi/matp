# Edge research

Offline research scripts — **not service code**. Nothing here touches the trading
stack; they run inside the `strategy-tester` container (which has pandas + network)
against public Binance history:

```bash
docker compose exec -T strategy-tester python - < research/fetch_data.py        # once; caches CSVs in /tmp/edge-data
docker compose exec -T strategy-tester python - < research/backtest_momentum.py
docker compose exec -T strategy-tester python - < research/backtest_funding.py
```

Purpose: test two edge hypotheses with real data and real costs *before* any
platform integration, per the rule that no strategy goes live without backtest
evidence. Binance is used for its deep public history (2021+, 12 liquid USDT
perps); live deployment would be Hyperliquid, whose fees are lower and funding
is hourly — Binance answers "does the premium exist", not "what will HL pay".

## Phase-1 results (2021-01-01 → 2026-07-19, full outputs in `.gemini/reports/edge-research-phase1.md`)

**Time-series momentum** (hold long when trailing 30d return > 0, 1-day lag,
fees+slippage+real funding applied): long-only variant made +30.8% CAGR,
Sharpe 0.83, maxDD −56% — vs BTC buy&hold +15.3%/0.53/−77%. But per-year
breakdown shows 2021 dominates: **ex-2021 the CAGR is −0.7%** (vs −14.1%/yr for
holding the basket). Conclusion: momentum is real *crash protection* relative to
holding crypto, not an absolute-return edge. 90d lookback is dead (−7% CAGR L/S).

**Delta-neutral funding harvest** (short perp + long spot when trailing 3d
funding annualizes above an entry threshold): the premium exists — +56–69% of
notional in gross funding over 5.5y — but it is episodic and bull-loaded.
Best in-sample config (enter 60%/exit 10% ann., 0.4% episode cost): +2.0%/yr on
2× capital; with HL-like 0.2% costs: +3.4%/yr. Nearly all of it was earned in
2021 (+25% on notional); 2022–2026 ≈ 0. Conclusion: not viable always-on
(cash-like returns); worth keeping as an **opportunistic bull-regime tool** —
when funding runs >40% annualized for days, the paid premium is real and
delta-neutral. Grid thresholds are in-sample; must be re-validated walk-forward
before sizing.

## Verdict vs. a US index (the question that started this)

Neither hypothesis produced evidence of beating an S&P index fund on a 10y
horizon in absolute terms. What the data does support: **if** crypto exposure is
held at all, momentum-gating it strictly dominates naive holding (same Sharpe,
25pp shallower drawdown, ex-2021 flat vs −50%); and **when** a euphoric funding
regime returns, delta-neutral harvesting monetizes it without price risk.

## Phase-2 results: anchored walk-forward (`walkforward.py`, report in `.gemini/reports/edge-research-phase2-walkforward.md`)

For each test year 2023–2026, the best config was selected on prior years only
(cash always a candidate), then scored on the unseen year. Verdict: **walk-forward
kills both as standing edges.**

- **Momentum** OOS 2023–2026: +15.5% CAGR, Sharpe 0.56, maxDD −53% — but BTC
  buy&hold over the identical window did +46.7% CAGR at Sharpe 1.05 with the same
  drawdown. The selection also drifted (30d → 14d in 2025) and then lost two years
  straight (−30.9%, −12.1%). Phase-1's shine was fit-window hindsight. Residual
  value: drawdown-limited *crypto exposure* (maxDD −53% vs −71% EW), not alpha.
- **Funding harvest** OOS: +0.1% total over 3.6y at Binance-ish costs, +2.1% at
  HL-ish costs. Dormant exactly as the per-year table predicted — it even slept
  through the 2024 bull (+2.2% that year, on notional). Confirmed: an episodic
  premium worth *monitoring* (arm when trailing funding runs >40–60% annualized),
  not an allocation.

**Bottom line vs the original 10y question: neither strategy produced
out-of-sample evidence of beating a US index fund. The index remains the honest
recommendation for investment capital.** These scripts stay as the template for
testing any future hypothesis — the discipline (real costs, cash as a candidate,
anchored OOS) is the durable output of this research.

## Remaining open threads (only if pursued deliberately)

- Hyperliquid funding/fee validation (hourly funding, real HL fee tiers).
- Basis-path risk for the funding trade (currently ignored; converges at exit).
- ~~A funding-regime monitor~~ **Built** (2026-07-19, on `main`):
  `ai-signal-generator/app/funding_monitor.py` alerts via notification-service
  when trailing 3d funding > 40%/yr (exit < 20%/yr). See
  `.gemini/reports/funding-regime-monitor.md`.
