# Edge research phase 3: token-unlock event study (2026-07-19)

Branch: `feat/edge-research`. Hypothesis: scheduled cliff unlocks are
price-insensitive supply — prices should show abnormal weakness around them.

## Data

DefiLlama's paid API blocks the emissions index, but the per-protocol CDN payload
is free: `defillama-datasets.llama.fi/emissions/{slug}` includes pre-classified
cliff events + per-allocation daily unlock series (used to derive a circulating
proxy that sizes each event). One classifier quirk: some payloads have
`unlockType: None` with the type only in the description text — handled.
Universe: 17 protocols probed, 12 usable (DYDX/IMX/WLD/ENA/AXS encode pure linear
streams — no cliffs). **311 events ≥0.5% of circulating, 2022–2026**, matched to
Binance USDT-perp daily klines.

```
aptos 45, apecoin 47, stepn 52, arbitrum 42, sui 38, sei 35, starknet 28,
jupiter 18, pyth 3, + singles (TIA/JTO/BLUR)   -> 311 events
```

## Raw event study (abnormal returns vs BTC, N=304 with full price coverage)

```
                   mean  median %neg      t
run-up D-8..D-1  -2.67%  -2.77%  65%  -4.41
D-1..D+1         -0.53%  -0.83%  56%  -1.57
D-1..D+3         -1.35%  -1.74%  62%  -2.79
D-1..D+7         -1.24%  -2.86%  62%  -1.68
drift D+7..D+30  -4.89%  -8.09%  72%  -4.25
```

Naive strategy (hedged short D-2→D+1, events ≥2%, fees 0.32%): mean +0.05%/trade,
**t=0.11** — the unlock *day* is not tradeable; the market isn't that lazy.
Split-sample on the two significant windows looked spectacular (drift OOS
2025–26: +5.61%/trade, t=4.61)…

## …and the control that killed it

Every token in this universe bleeds vs BTC on average, unlock or no unlock:

```
baseline daily abnormal drift (%/day): APE -0.29, APT -0.27, ARB -0.29,
BLUR -0.39, GMT -0.27, JTO -0.23, JUP -0.18, PYTH -0.28, SEI -0.21,
STRK -0.50, SUI -0.12, TIA -0.25      (12 of 12 negative)

RUN-UP D-8..D-1   raw t=-5.52  →  EXCESS over token baseline: -1.47%, t=-2.44
DRIFT D+7..D+30   raw t=-6.67  →  EXCESS over token baseline: -1.22%, t=-1.11
```

The celebrated "unlock drift" is ~85% *secular low-float/VC-coin underperformance*
— a always-on short would have captured it without ever reading an unlock
calendar. What survives attribution to the calendar itself is a **modest run-up
effect**: −1.47% excess in the week before a cliff, t=−2.44, 56% of events
negative, present in both sample halves. Real but small — and with five windows
examined, that t carries selection risk.

## Honest verdict

1. No tradeable edge is confirmed yet. The run-up effect (−1.5%/event beyond
   baseline, ~65 events/yr) is the only candidate left standing and needs:
   funding-cost modeling (short alt perp legs frequently *pay* funding),
   an overlap-free portfolio sim (monthly unlockers' windows overlap, inflating
   t-stats), and walk-forward — before any capital discussion.
2. The strongest pattern found is accidental: the 12/12 secular bleed of
   unlock-heavy tokens vs BTC (≈−0.26%/day median). Whether it's harvestable
   depends entirely on the funding cost of holding those shorts for months —
   which is plausibly the mechanism that lets it persist. Worth one dedicated
   funding-adjusted study.
3. The research pipeline did its job twice today: a t=6.7 "edge" entered, a
   t=1.1 non-result left. That discipline is the asset.

Scripts: `research/fetch_unlocks.py`, `research/event_study_unlocks.py`,
`research/unlock_controls.py` (all reproducible in the strategy-tester
container). README updated with the phase-3 section.
