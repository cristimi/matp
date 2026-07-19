"""Anchored walk-forward validation for the two phase-1 hypotheses.

For each test year in 2023..2026: select the best config using ONLY data before
Jan 1 of that year (expanding fit window starting 2021), then apply the selected
config to the unseen test year. Stitch the test-year returns into one
out-of-sample track record. "cash" (flat, 0%) is always a candidate config, so
the procedure is allowed to conclude "don't trade".

Momentum configs: lookback x {long-only, long/short}, selected by fit-window
Sharpe. Funding configs: (enter, exit) hysteresis grid, selected by fit-window
total net; run at 0.4%/episode costs (Binance-ish) and 0.2% (Hyperliquid-ish).

Loaders and engines are duplicated from backtest_momentum.py /
backtest_funding.py because scripts are piped via stdin into the
strategy-tester container — there is no module to import from.

Run: docker compose exec -T strategy-tester python - < research/walkforward.py
Requires /tmp/edge-data from fetch_data.py.
"""
import glob

import numpy as np
import pandas as pd

DATA = "/tmp/edge-data"
FEE_PCT = 0.0008                 # momentum: taker + slippage per side
ANN = 365
TEST_YEARS = [2023, 2024, 2025, 2026]
INTERVALS_PER_DAY = 3
TRAIL = 9
MAX_CONCURRENT = 3
ANN_FACTOR = INTERVALS_PER_DAY * 365


# ── data ───────────────────────────────────────────────────────────────────────

def load_px_fu() -> tuple[pd.DataFrame, pd.DataFrame]:
    closes, fundings = {}, {}
    for path in sorted(glob.glob(f"{DATA}/*_daily.csv")):
        sym = path.split("/")[-1].replace("_daily.csv", "")
        k = pd.read_csv(path)
        k.index = pd.to_datetime(k["open_time"], unit="ms", utc=True).dt.normalize()
        closes[sym] = k["close"].astype(float)
        f = pd.read_csv(path.replace("_daily", "_funding"))
        ft = pd.to_datetime(f["funding_time"], unit="ms", utc=True).dt.normalize()
        fundings[sym] = f["funding_rate"].astype(float).groupby(ft).sum()
    px = pd.DataFrame(closes)
    fu = pd.DataFrame(fundings).reindex(px.index).fillna(0.0)
    return px, fu


def load_funding_8h() -> pd.DataFrame:
    out = {}
    for path in sorted(glob.glob(f"{DATA}/*_funding.csv")):
        sym = path.split("/")[-1].replace("_funding.csv", "")
        f = pd.read_csv(path)
        idx = pd.to_datetime(f["funding_time"], unit="ms", utc=True).dt.round("h")
        out[sym] = pd.Series(f["funding_rate"].astype(float).values, index=idx)
    df = pd.DataFrame(out)
    return df[~df.index.duplicated()].sort_index()


# ── engines (mirror the phase-1 scripts) ───────────────────────────────────────

def mom_series(px, fu, lookback, long_only) -> pd.Series:
    ret = px.pct_change()
    sig = np.sign(px / px.shift(lookback) - 1)
    if long_only:
        sig = sig.clip(lower=0)
    pos = sig.shift(1)
    n = px.notna().sum(axis=1)
    w = pos.div(n, axis=0)
    gross = (w * ret).sum(axis=1)
    funding_pnl = (-w * fu).sum(axis=1)
    fees = w.diff().abs().sum(axis=1) * FEE_PCT
    return (gross + funding_pnl - fees).dropna()


def fund_episodes(rates, enter, exit_) -> pd.Series:
    trail_ann = rates.rolling(TRAIL, min_periods=TRAIL).mean() * ANN_FACTOR
    state, out = False, []
    for v in trail_ann.to_numpy():
        if not np.isnan(v):
            if not state and v > enter:
                state = True
            elif state and v < exit_:
                state = False
        out.append(state)
    return pd.Series(out, index=rates.index).shift(1, fill_value=False)


def fund_series(fu, enter, exit_, round_trip) -> pd.Series:
    active = pd.DataFrame({s: fund_episodes(fu[s].dropna(), enter, exit_)
                          .reindex(fu.index, fill_value=False) for s in fu.columns})
    trail = fu.rolling(TRAIL, min_periods=TRAIL).mean() * ANN_FACTOR
    keep = trail.where(active).rank(axis=1, ascending=False) <= MAX_CONCURRENT
    active = active & keep
    n_active = active.sum(axis=1)
    w = active.div(n_active.replace(0, np.nan), axis=0).fillna(0.0)
    funding_pnl = (w * fu.fillna(0.0)).sum(axis=1)
    entries = (active & ~active.shift(1, fill_value=False))
    cost = entries.mul(w, fill_value=0.0).sum(axis=1) * round_trip
    net = funding_pnl - cost
    return net.groupby(net.index.normalize()).sum()


# ── walk-forward machinery ─────────────────────────────────────────────────────

def sharpe(s: pd.Series) -> float:
    s = s.dropna()
    if len(s) < 30 or s.std() == 0:
        return 0.0
    return float(s.mean() / s.std() * np.sqrt(ANN))


def total(s: pd.Series) -> float:
    return float((1 + s.dropna()).prod() - 1)


def metrics(daily: pd.Series) -> str:
    daily = daily.dropna()
    if len(daily) == 0 or daily.abs().sum() == 0:
        return "flat (cash)"
    eq = (1 + daily).cumprod()
    years = len(daily) / ANN
    cagr = eq.iloc[-1] ** (1 / years) - 1
    maxdd = (eq / eq.cummax() - 1).min()
    return (f"CAGR {cagr:+.1%}  vol {daily.std() * np.sqrt(ANN):.1%}  "
            f"sharpe {sharpe(daily):.2f}  maxDD {maxdd:.1%}  "
            f"total {eq.iloc[-1] - 1:+.1%} over {years:.1f}y")


def walkforward(configs: dict[str, pd.Series], select) -> pd.Series:
    parts = []
    for y in TEST_YEARS:
        fit_end = pd.Timestamp(f"{y}-01-01", tz="UTC")
        test_end = pd.Timestamp(f"{y + 1}-01-01", tz="UTC")
        scores = {name: select(s[s.index < fit_end]) for name, s in configs.items()}
        best = max(scores, key=scores.get)
        test = configs[best]
        test = test[(test.index >= fit_end) & (test.index < test_end)]
        print(f"  fold {y}: fit<{y} picked '{best}' "
              f"(fit score {scores[best]:.2f}) -> OOS {y}: {total(test):+.1%}")
        parts.append(test)
    return pd.concat(parts)


def main():
    px, fu_daily = load_px_fu()
    fu8 = load_funding_8h()
    cash = pd.Series(0.0, index=px.index)

    print("=== MOMENTUM walk-forward (select by fit Sharpe; cash is a candidate) ===")
    mom_cfgs = {"cash": cash}
    for lb in (7, 14, 21, 30, 60, 90):
        mom_cfgs[f"{lb}d long-only"] = mom_series(px, fu_daily, lb, True)
        mom_cfgs[f"{lb}d long/short"] = mom_series(px, fu_daily, lb, False)
    oos = walkforward(mom_cfgs, sharpe)
    print(f"  stitched OOS 2023-2026: {metrics(oos)}")
    start = oos.index[0]
    print(f"  benchmarks same window:")
    print(f"    BTC buy&hold:  {metrics(px['BTCUSDT'].pct_change()[start:])}")
    print(f"    EW buy&hold:   {metrics(px.pct_change().mean(axis=1)[start:])}")

    for cost, label in ((0.004, "0.4%/episode (Binance-ish)"),
                        (0.002, "0.2%/episode (Hyperliquid-ish)")):
        print(f"\n=== FUNDING walk-forward, cost {label} "
              f"(select by fit total net; cash is a candidate) ===")
        f_cfgs = {"cash": pd.Series(0.0, index=fu8.index.normalize().unique())}
        for enter in (0.20, 0.40, 0.60):
            for exit_ in (0.05, 0.10, 0.20):
                f_cfgs[f"enter {enter:.0%}/exit {exit_:.0%}"] = \
                    fund_series(fu8, enter, exit_, cost)
        oos_f = walkforward(f_cfgs, total)
        print(f"  stitched OOS 2023-2026 (on notional): {metrics(oos_f)}")


if __name__ == "__main__":
    main()
