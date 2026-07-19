# Edge research phase 5: cross-venue funding-spread capture (2026-07-19)

Branch: `feat/edge-research`. Hypothesis chosen for system fit: the same perp
carries different funding on different venues; a delta-neutral cross-venue pair
(long where longs are paid/pay less, short where shorts are paid more) collects
the spread each settlement. Perp-only — no spot build needed — and MATP already
has adapters for two venues (Hyperliquid + Blofin). Binance is the research
proxy for the CEX leg (deep public history); Blofin validation is a named gate.

## Data

`research/fetch_hl_funding.py`: Hyperliquid hourly funding, 24 coins,
2023-07 → 2026-07 (~21k–27k points each), aligned against the Binance 8h
histories cached in phases 1/4 (8h rate spread across hourly buckets).

## Spread landscape (annualized |HL − Binance|)

```
        mean|s|  p90|s|  >15%  >50%     (share of hours)
BTC      11.5%   26.8%   21%    4%
ETH      11.3%   28.1%   20%    4%
SEI      26.8%   65.7%   48%   14%
BLUR     27.3%   52.3%   37%   10%
TIA      22.6%   54.5%   37%   11%
JTO      22.3%   48.7%   32%   10%
...24 coins, all with mean |spread| 10.8–27.3%/yr
```

## Episode backtest grid (max 3 concurrent, both halves shown)

```
trail enter/exit cost  episodes avg_len_h in_mkt   gross    fees net/yr net/yr(2x)  half1   half2
  24h     15%/5% 0.3%      3956        20   100% +121.7% -398.4% -90.6%     -45.3% -116.3% -160.3%
  72h    30%/10% 0.3%       575        64    63% +104.8%  -70.4% +11.3%      +5.6%   +7.6%  +26.9%
  72h    50%/10% 0.3%       369        66    39%  +86.8%  -42.6% +14.5%      +7.2%  +16.1%  +28.0%
 168h    30%/10% 0.3%       286       109    52%  +87.3%  -33.6% +17.6%      +8.8%  +24.9%  +28.9%
 168h    50%/10% 0.3%       191       129    40%  +73.8%  -21.7% +17.1%      +8.5%  +27.5%  +24.5%
 168h    30%/10% 0.2%       286       109    52%  +87.3%  -22.4% +21.3%     +10.6%  +33.3%  +31.7%
```

The v1 fast config repeats the familiar churn death. Every slow config is
positive in BOTH halves — the first strategy in this program to manage that.

Sign-flip honesty check (the sim collects |spread|, implicitly flipping free if
the spread changes sign mid-episode): 14% of active coin-hours run against the
entry direction; holding direction fixed retains **86% of gross** (+366% vs
+427% uncapped). Realistic call: **+12–18%/yr on notional** after fees,
≈ **+6–9%/yr on 2× unlevered capital**, market-neutral.

## Verdict

First candidate to clear the bar on paper: double-digit yield on notional,
structural payer (directional crowds pushing venue funding apart), slow cadence
(hourly settlements — homelab-compatible), capacity-limited (fattest spreads in
SEI/BLUR/TIA-class books), and robust across halves without parameter luck.

Gates before build (recorded in README): real **Blofin funding-history
validation** (the actual second leg; Binance is only a proxy), walk-forward on
the hysteresis thresholds, entry basis/execution modeling (venue marks differ at
entry), and a leg-liquidation margin policy. If those pass, the build is small:
both legs are perps on already-adapted venues, and the funding-harvest staged
pipeline (monitor → armed plan → confirm) fits this trade shape unchanged.
