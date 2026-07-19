"""Time-series momentum backtest on daily Binance USDT-perp closes.

Rule: for each coin, hold +1 (long) if the trailing LOOKBACK-day return is
positive, -1 (short) if negative; position taken with a 1-day lag (signal from
data through yesterday's close earns today's close-to-close return). Equal
notional weight 1/N across coins with data. Variants: long/short and long-only.

Costs modeled per unit of traded notional:
  - taker fee + slippage per side: FEE_PCT (applied to |Δposition|)
  - funding: longs pay positive funding, shorts receive it (real 8h history)

Run: docker compose exec -T strategy-tester python - < research/backtest_momentum.py
Requires /tmp/edge-data from fetch_data.py.
"""
import glob

import numpy as np
import pandas as pd

DATA = "/tmp/edge-data"
FEE_PCT = 0.0008          # 0.05% taker + 0.03% slippage, per side
LOOKBACKS = [30, 90]
ANN = 365


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    closes, fundings = {}, {}
    for path in sorted(glob.glob(f"{DATA}/*_daily.csv")):
        sym = path.split("/")[-1].replace("_daily.csv", "")
        k = pd.read_csv(path)
        k.index = pd.to_datetime(k["open_time"], unit="ms", utc=True).dt.normalize()
        closes[sym] = k["close"].astype(float)
        f = pd.read_csv(path.replace("_daily", "_funding"))
        ft = pd.to_datetime(f["funding_time"], unit="ms", utc=True).dt.normalize()
        # daily funding = sum of that day's 8h rates
        fundings[sym] = f["funding_rate"].astype(float).groupby(ft).sum()
    px = pd.DataFrame(closes)
    fu = pd.DataFrame(fundings).reindex(px.index).fillna(0.0)
    return px, fu


def metrics(daily: pd.Series) -> dict:
    daily = daily.dropna()
    eq = (1 + daily).cumprod()
    years = len(daily) / ANN
    cagr = eq.iloc[-1] ** (1 / years) - 1
    vol = daily.std() * np.sqrt(ANN)
    sharpe = daily.mean() / daily.std() * np.sqrt(ANN) if daily.std() > 0 else 0.0
    maxdd = (eq / eq.cummax() - 1).min()
    return {"CAGR": f"{cagr:+.1%}", "vol": f"{vol:.1%}",
            "sharpe": f"{sharpe:.2f}", "maxDD": f"{maxdd:.1%}",
            "final_$1": f"{eq.iloc[-1]:.2f}", "years": f"{years:.1f}"}


def run(px: pd.DataFrame, fu: pd.DataFrame, lookback: int, long_only: bool) -> pd.Series:
    ret = px.pct_change()
    sig = np.sign(px / px.shift(lookback) - 1)
    if long_only:
        sig = sig.clip(lower=0)
    pos = sig.shift(1)                       # trade with 1-day lag
    n = px.notna().sum(axis=1)               # equal weight over listed coins
    w = pos.div(n, axis=0)
    gross = (w * ret).sum(axis=1)
    funding_pnl = (-w * fu).sum(axis=1)      # long pays positive funding
    fees = w.diff().abs().sum(axis=1) * FEE_PCT
    return gross + funding_pnl - fees


def main():
    px, fu = load()
    print(f"universe: {list(px.columns)}")
    print(f"span: {px.index[0].date()} .. {px.index[-1].date()}  ({len(px)} days)\n")

    rows = {}
    for lb in LOOKBACKS:
        rows[f"TSMOM {lb}d long/short"] = metrics(run(px, fu, lb, long_only=False))
        rows[f"TSMOM {lb}d long-only"] = metrics(run(px, fu, lb, long_only=True))

    btc = px["BTCUSDT"].pct_change()
    rows["BTC buy&hold"] = metrics(btc)
    ew = px.pct_change().mean(axis=1)
    rows["Equal-weight buy&hold"] = metrics(ew)

    print(pd.DataFrame(rows).T.to_string())

    # per-year net returns of the headline variants, for regime honesty
    for label, long_only in (("long/short", False), ("long-only", True)):
        net = run(px, fu, 30, long_only=long_only)
        by_year = (1 + net).groupby(net.index.year).prod() - 1
        print(f"\nTSMOM 30d {label}, net return by calendar year:")
        for y, r in by_year.items():
            print(f"  {y}: {r:+.1%}")
    ew_by_year = (1 + ew).groupby(ew.index.year).prod() - 1
    print("\nEqual-weight buy&hold, return by calendar year:")
    for y, r in ew_by_year.items():
        print(f"  {y}: {r:+.1%}")


if __name__ == "__main__":
    main()
