"""Cross-venue funding-spread study: Hyperliquid vs Binance (proxy for a CEX leg).

Hypothesis: the same perp carries different funding on different venues; when
the spread is wide, a delta-neutral pair (long the venue where longs are paid /
pay less, short the venue where shorts are paid more) collects the spread with
no directional exposure. Slow (hourly settlements), capacity-limited, and MATP
already has adapters for two venues — the CEX leg would be Blofin in practice
(Binance history is the research proxy; CEX funding clusters, but this must be
validated on Blofin before build).

Accounting: HL settles hourly; Binance every 8h (rate spread over 8 hourly
buckets). spread(t) = annualized(HL) - annualized(Binance). Entry when the
24h-trailing |spread| exceeds ENTER, exit below EXIT; the position collects
|spread| per hour while on. Costs: 4 perp legs (open+close on both venues) =
ROUND_TRIP per episode. Price-basis PnL between the two marks is ignored
(both track the same index; adds noise, not drift — first-pass caveat).

Run: docker compose exec -T strategy-tester python - < research/funding_spread_study.py
Requires /tmp/edge-data: HL_{COIN}_funding.csv (fetch_hl_funding.py) and
{COIN}USDT_funding.csv (phases 1/4).
"""
import glob
import os

import numpy as np
import pandas as pd

DATA = "/tmp/edge-data"
HOURS_ANN = 24 * 365
MAX_CONCURRENT = 3
# v1 (trail 24h, enter 15%, exit 5%, 0.3%/episode) churned: 3956 episodes of
# ~20h each — 122% gross collected, 398% in costs. The grid below hunts for
# configs where episodes are long enough to amortize the round trip.
GRID = [  # (trail_h, enter, exit, round_trip)
    (24, 0.15, 0.05, 0.003),   # v1 baseline
    (72, 0.30, 0.10, 0.003),
    (72, 0.50, 0.10, 0.003),
    (168, 0.30, 0.10, 0.003),
    (168, 0.50, 0.10, 0.003),
    (168, 0.30, 0.10, 0.002),
]


def load_pair(coin: str) -> pd.DataFrame | None:
    hl_path = f"{DATA}/HL_{coin}_funding.csv"
    bn_path = f"{DATA}/{coin}USDT_funding.csv"
    if not (os.path.exists(hl_path) and os.path.exists(bn_path)):
        return None
    hl = pd.read_csv(hl_path)
    if hl.empty:
        return None
    hl_s = pd.Series(hl["funding_rate"].astype(float).values,
                     index=pd.to_datetime(hl["funding_time"], unit="ms", utc=True).dt.round("h"))
    hl_s = hl_s[~hl_s.index.duplicated()]
    bn = pd.read_csv(bn_path)
    bn_s = pd.Series(bn["funding_rate"].astype(float).values / 8.0,   # per-hour equivalent
                     index=pd.to_datetime(bn["funding_time"], unit="ms", utc=True).dt.round("h"))
    bn_s = bn_s[~bn_s.index.duplicated()].reindex(
        pd.date_range(bn_s.index.min(), bn_s.index.max(), freq="h", tz="UTC")
    ).bfill(limit=7)   # each 8h settlement covers the 7 hours before it + itself
    df = pd.DataFrame({"hl": hl_s, "bn": bn_s}).dropna()
    return df if len(df) > 24 * 90 else None


def episodes(spread_ann: pd.Series, trail_h: int, enter: float, exit_: float) -> pd.Series:
    trail = spread_ann.rolling(trail_h, min_periods=trail_h).mean()
    state, out = False, []
    for v in trail.abs().to_numpy():
        if not np.isnan(v):
            if not state and v > enter:
                state = True
            elif state and v < exit_:
                state = False
        out.append(state)
    return pd.Series(out, index=spread_ann.index).shift(1, fill_value=False)


def main():
    coins = sorted(p.split("/")[-1][3:-12] for p in glob.glob(f"{DATA}/HL_*_funding.csv"))
    pairs = {c: d for c in coins if (d := load_pair(c)) is not None}
    print(f"coins with both venues' history: {sorted(pairs)}\n")

    print("=== spread landscape (annualized |HL - Binance|) ===")
    stats = {}
    for c, d in pairs.items():
        spread = (d["hl"] - d["bn"]) * HOURS_ANN
        stats[c] = {
            "hours": len(d),
            "mean|s|": f"{spread.abs().mean():.1%}",
            "p90|s|": f"{spread.abs().quantile(0.9):.1%}",
            ">15%": f"{(spread.abs() > 0.15).mean():.0%}",
            ">50%": f"{(spread.abs() > 0.50).mean():.0%}",
        }
    print(pd.DataFrame(stats).T.to_string(), "\n")

    results = []
    for trail_h, enter, exit_, round_trip in GRID:
        hourly, active = {}, {}
        for c, d in pairs.items():
            spread_ann = (d["hl"] - d["bn"]) * HOURS_ANN
            act = episodes(spread_ann, trail_h, enter, exit_)
            hourly[c] = (d["hl"] - d["bn"]).where(act, 0.0).abs()
            active[c] = act
        col = pd.DataFrame(hourly).fillna(0.0)
        act = pd.DataFrame(active).fillna(False)
        trail_abs = pd.DataFrame({c: ((pairs[c]["hl"] - pairs[c]["bn"]) * HOURS_ANN)
                                 .rolling(trail_h).mean().abs() for c in pairs}).fillna(0.0)
        keep = trail_abs.where(act).rank(axis=1, ascending=False) <= MAX_CONCURRENT
        act = act & keep.fillna(False)
        n_act = act.sum(axis=1)
        w = act.div(n_act.replace(0, np.nan), axis=0).fillna(0.0)
        collect = (w * col).sum(axis=1)
        entries = act & ~act.shift(1, fill_value=False)
        costs = entries.mul(w, fill_value=0.0).sum(axis=1) * round_trip
        net = collect - costs
        years = (net.index[-1] - net.index[0]).days / 365
        n_epi = int(entries.sum().sum())
        half = net.index[0] + (net.index[-1] - net.index[0]) / 2
        results.append({
            "trail": f"{trail_h}h", "enter/exit": f"{enter:.0%}/{exit_:.0%}",
            "cost": f"{round_trip:.1%}",
            "episodes": n_epi,
            "avg_len_h": round(act.sum().sum() / max(n_epi, 1)),
            "in_mkt": f"{(n_act > 0).mean():.0%}",
            "gross": f"{collect.sum():+.1%}",
            "fees": f"{-costs.sum():+.1%}",
            "net/yr": f"{net.sum() / years:+.1%}",
            "net/yr(2x cap)": f"{net.sum() / 2 / years:+.1%}",
            "half1": f"{net[net.index < half].sum():+.1%}",
            "half2": f"{net[net.index >= half].sum():+.1%}",
        })
    print("=== episode backtest grid (max 3 concurrent; halves = net on notional) ===")
    print(pd.DataFrame(results).to_string(index=False))


if __name__ == "__main__":
    main()
