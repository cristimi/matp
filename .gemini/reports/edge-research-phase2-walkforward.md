# Edge research phase 2: anchored walk-forward validation (2026-07-19)

Branch: `feat/edge-research`. Phase 1 left one mandatory question: are the momentum
and funding-harvest results discovery or curve-fitting? `research/walkforward.py`
answers it with anchored walk-forward: for each test year 2023–2026, the best config
is selected using ONLY prior-year data (momentum: 6 lookbacks × long-only/long-short,
selected by fit Sharpe; funding: 3×3 enter/exit grid, selected by fit net; "cash" is
always a candidate so the procedure may conclude "don't trade"), then scored on the
unseen year. Stitched OOS = an honest 3.6-year track record no parameter ever saw.

## Actual output

```
$ docker compose exec -T strategy-tester python - < research/walkforward.py
=== MOMENTUM walk-forward (select by fit Sharpe; cash is a candidate) ===
  fold 2023: fit<2023 picked '30d long-only' (fit score 1.29) -> OOS 2023: +83.9%
  fold 2024: fit<2024 picked '30d long-only' (fit score 1.40) -> OOS 2024: +49.3%
  fold 2025: fit<2025 picked '14d long-only' (fit score 1.40) -> OOS 2025: -30.9%
  fold 2026: fit<2026 picked '14d long-only' (fit score 1.03) -> OOS 2026: -12.1%
  stitched OOS 2023-2026: CAGR +15.5%  vol 39.7%  sharpe 0.56  maxDD -52.8%  total +66.6% over 3.6y
  benchmarks same window:
    BTC buy&hold:  CAGR +46.7%  vol 46.8%  sharpe 1.05  maxDD -53.0%  total +290.2% over 3.6y
    EW buy&hold:   CAGR +20.5%  vol 63.9%  sharpe 0.61  maxDD -71.4%  total +93.7% over 3.6y

=== FUNDING walk-forward, cost 0.4%/episode (Binance-ish) ===
  fold 2023: picked 'enter 60%/exit 20%' -> OOS 2023: -0.2%
  fold 2024: picked 'enter 60%/exit 20%' -> OOS 2024: +0.3%
  fold 2025: picked 'enter 60%/exit 20%' -> OOS 2025: +0.0%
  fold 2026: picked 'enter 60%/exit 20%' -> OOS 2026: +0.0%
  stitched OOS 2023-2026 (on notional): CAGR +0.0%  total +0.1% over 3.6y

=== FUNDING walk-forward, cost 0.2%/episode (Hyperliquid-ish) ===
  fold 2023: picked 'enter 60%/exit 5%'  -> OOS 2023: -0.0%
  fold 2024: picked 'enter 40%/exit 10%' -> OOS 2024: +2.2%
  fold 2025: picked 'enter 60%/exit 5%'  -> OOS 2025: +0.0%
  fold 2026: picked 'enter 60%/exit 5%'  -> OOS 2026: +0.0%
  stitched OOS 2023-2026 (on notional): CAGR +0.6%  sharpe 1.25  total +2.1% over 3.6y
```

## Findings

1. **Momentum's phase-1 shine was fit-window hindsight.** Out-of-sample it returned
   +15.5%/yr at Sharpe 0.56 — while BTC buy&hold over the *identical* window did
   +46.7%/yr at Sharpe 1.05 with the same max drawdown. The walk-forward also shows
   the classic failure mode: the selected lookback drifted (30d → 14d after 2024)
   and the newly selected config lost money two years running (−30.9%, −12.1%).
   The residual that survives: OOS drawdown was −53% vs −71% for the equal-weight
   basket, so momentum-gating still limits the pain of crypto exposure. That is
   risk management, not alpha.

2. **Funding harvest is confirmed dormant, not mispriced.** The procedure honestly
   selected trade-worthy configs each year and earned ≈ nothing out-of-sample —
   +0.1% total at Binance costs, +2.1% at Hyperliquid-like costs — sleeping even
   through the 2024 bull. The 2021-style euphoric funding regime has not recurred.
   The premium is real but episodic; the correct posture is a cheap monitor that
   raises a flag when trailing funding exceeds ~40–60% annualized for days, not an
   allocated strategy.

3. **Verdict on the original question (10y vs US index): final.** Two structurally
   motivated hypotheses, 5.5 years of data, real costs, and honest out-of-sample
   testing produced no evidence of beating an index fund. The S&P remains the
   recommendation for investment capital; the durable output of this research is
   the validation discipline itself, now reusable for any future hypothesis.

No service code touched; scripts and README updated in `research/` on
`feat/edge-research`.
