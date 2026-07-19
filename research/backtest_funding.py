"""Delta-neutral funding-harvest backtest on Binance USDT-perp funding history.

Trade: when a coin's trailing 3-day mean funding annualizes above `enter`,
short the perp and hold spot for the same notional (delta-neutral); collect
funding every 8h interval while in. Exit when the trailing mean annualizes
below `exit`. Only positive-funding harvesting is modeled — the negative side
would require shorting spot, which retail can't do cleanly.

Costs: `round_trip` of notional applied once per episode (spot buy/sell + perp
open/close, fees + slippage on all four legs). Basis wiggle between the legs is
NOT modeled (it converges at exit but adds path noise) — first-pass caveat.

A parameter grid is reported because the v1 run (enter 20%/exit 5%, 0.4% costs)
showed the core diagnosis: +69% of notional in gross funding existed, but 547
churny episodes ate 74% in costs. The grid separates "premium doesn't exist"
from "entry rule wastes it". Grid selection is IN-SAMPLE — any config chosen
from it must be re-validated walk-forward before real sizing.

Portfolio: each 8h interval, capital splits equally across active episodes,
capped at MAX_CONCURRENT (highest trailing funding wins). Returns are reported
on NOTIONAL and on CAPITAL at 2x notional (spot leg fully funded + perp margin,
no leverage — conservative).

Run: docker compose exec -T strategy-tester python - < research/backtest_funding.py
Requires /tmp/edge-data from fetch_data.py.
"""
import glob

import numpy as np
import pandas as pd

DATA = "/tmp/edge-data"
INTERVALS_PER_DAY = 3            # Binance funds every 8h
TRAIL = 9                        # trailing window: 3 days of 8h intervals
MAX_CONCURRENT = 3
ANN_FACTOR = INTERVALS_PER_DAY * 365


def load() -> pd.DataFrame:
    out = {}
    for path in sorted(glob.glob(f"{DATA}/*_funding.csv")):
        sym = path.split("/")[-1].replace("_funding.csv", "")
        f = pd.read_csv(path)
        idx = pd.to_datetime(f["funding_time"], unit="ms", utc=True).dt.round("h")
        out[sym] = pd.Series(f["funding_rate"].astype(float).values, index=idx)
    df = pd.DataFrame(out)
    return df[~df.index.duplicated()].sort_index()


def episodes(rates: pd.Series, enter: float, exit_: float) -> pd.Series:
    """Boolean in-position series for one symbol, hysteresis enter/exit."""
    trail_ann = rates.rolling(TRAIL, min_periods=TRAIL).mean() * ANN_FACTOR
    state, out = False, []
    for v in trail_ann.to_numpy():
        if not np.isnan(v):
            if not state and v > enter:
                state = True
            elif state and v < exit_:
                state = False
        out.append(state)
    # decision uses data through t; position earns from t+1
    return pd.Series(out, index=rates.index).shift(1, fill_value=False)


def run(fu: pd.DataFrame, enter: float, exit_: float, round_trip: float) -> dict:
    active = pd.DataFrame({s: episodes(fu[s].dropna(), enter, exit_)
                          .reindex(fu.index, fill_value=False) for s in fu.columns})
    trail = fu.rolling(TRAIL, min_periods=TRAIL).mean() * ANN_FACTOR

    ranked = trail.where(active)
    keep = ranked.rank(axis=1, ascending=False) <= MAX_CONCURRENT
    active = active & keep

    n_active = active.sum(axis=1)
    w = active.div(n_active.replace(0, np.nan), axis=0).fillna(0.0)

    funding_pnl = (w * fu.fillna(0.0)).sum(axis=1)          # short perp receives
    entries = (active & ~active.shift(1, fill_value=False))
    cost = entries.mul(w, fill_value=0.0).sum(axis=1) * round_trip
    net = funding_pnl - cost                                 # per unit notional

    years = (fu.index[-1] - fu.index[0]).days / 365
    daily = net.groupby(net.index.normalize()).sum()
    eq = (1 + daily).cumprod()
    return {
        "enter/exit": f"{enter:.0%}/{exit_:.0%}",
        "cost": f"{round_trip:.2%}",
        "episodes": int(entries.sum().sum()),
        "in_mkt": f"{(n_active > 0).mean():.0%}",
        "gross": f"{funding_pnl.sum():+.1%}",
        "fees": f"{-cost.sum():+.1%}",
        "net/yr(notional)": f"{(1 + net.sum()) ** (1 / years) - 1:+.1%}",
        "net/yr(2x cap)": f"{(1 + net.sum() / 2) ** (1 / years) - 1:+.1%}",
        "maxDD": f"{(eq / eq.cummax() - 1).min():.1%}",
        "_daily": daily,
    }


def main():
    fu = load()
    print(f"universe: {list(fu.columns)}")
    print(f"span: {fu.index[0].date()} .. {fu.index[-1].date()}  "
          f"({len(fu)} funding intervals)\n")

    grid = [
        (0.20, 0.05, 0.004),   # v1 baseline
        (0.20, 0.10, 0.004),
        (0.40, 0.10, 0.004),
        (0.60, 0.10, 0.004),
        (0.40, 0.10, 0.002),   # Hyperliquid-like cheaper legs
        (0.60, 0.10, 0.002),
    ]
    results = [run(fu, e, x, c) for e, x, c in grid]
    print(pd.DataFrame([{k: v for k, v in r.items() if k != "_daily"}
                        for r in results]).to_string(index=False))

    print("\nnet return on notional by calendar year (enter 40%/exit 10%, 0.4% cost):")
    daily = next(r for r in results if r["enter/exit"] == "40%/10%"
                 and r["cost"] == "0.40%")["_daily"]
    for y, r in ((1 + daily).groupby(daily.index.year).prod() - 1).items():
        print(f"  {y}: {r:+.1%}")


if __name__ == "__main__":
    main()
