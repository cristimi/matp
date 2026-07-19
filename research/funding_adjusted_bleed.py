"""Funding-adjusted study of the two survivors from phase 3 (unlock study):

A. The secular bleed: short an equal-weight basket of the 12 unlock-heavy alts,
   long BTC for the same notional, held continuously. Phase 3 measured the
   price leg at ~-0.26%/day median in the alts' favor — but a perp short
   RECEIVES funding when it's positive and PAYS when negative. This study adds
   the real 8h funding on both legs and answers whether the bleed is
   harvestable or whether funding is the mechanism that lets it persist.

B. The unlock run-up effect (D-8..D-1, -1.47% excess, t=-2.44): same funding
   adjustment per event window, plus entry/exit fees, to get the actual
   per-event net of a hedged short.

Positions: short alt (+funding when positive), long BTC (-funding when
positive). Basket rebalances daily to equal weight across listed alts; fee on
weight changes at 0.08%/side. Fetches missing {SYM}_funding.csv into
/tmp/edge-data (same format as fetch_data.py).

Run: docker compose exec -T strategy-tester python - < research/funding_adjusted_bleed.py
Requires /tmp/edge-data klines from fetch_unlocks.py / fetch_data.py.
"""
import csv
import json
import os
import time
import urllib.request

import numpy as np
import pandas as pd

DATA = "/tmp/edge-data"
ALTS = ["APTUSDT", "ARBUSDT", "SUIUSDT", "TIAUSDT", "GMTUSDT", "SEIUSDT",
        "STRKUSDT", "JTOUSDT", "PYTHUSDT", "JUPUSDT", "BLURUSDT", "APEUSDT"]
FEE_SIDE = 0.0008
ANN = 365


def get(url):
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"gave up on {url}: {last}")


def ensure_funding(sym: str) -> None:
    path = f"{DATA}/{sym}_funding.csv"
    if os.path.exists(path):
        return
    rows, start = [], 1609459200000
    while True:
        data = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}"
                   f"&limit=1000&startTime={start}")
        if not data:
            break
        rows += [[r["fundingTime"], r["fundingRate"]] for r in data]
        if len(data) < 1000:
            break
        start = data[-1]["fundingTime"] + 1
        time.sleep(0.3)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["funding_time", "funding_rate"])
        w.writerows(rows)
    print(f"fetched funding {sym}: {len(rows)} points")


def load_px(sym):
    k = pd.read_csv(f"{DATA}/{sym}_daily.csv")
    idx = pd.to_datetime(k["open_time"], unit="ms", utc=True).dt.normalize()
    return pd.Series(k["close"].astype(float).values, index=idx)


def load_funding_daily(sym):
    f = pd.read_csv(f"{DATA}/{sym}_funding.csv")
    ft = pd.to_datetime(f["funding_time"], unit="ms", utc=True).dt.normalize()
    return f["funding_rate"].astype(float).groupby(ft).sum()


def metrics(daily):
    daily = daily.dropna()
    eq = (1 + daily).cumprod()
    years = len(daily) / ANN
    return (f"CAGR {eq.iloc[-1] ** (1 / years) - 1:+.1%}  vol {daily.std() * np.sqrt(ANN):.1%}  "
            f"sharpe {daily.mean() / daily.std() * np.sqrt(ANN):.2f}  "
            f"maxDD {(eq / eq.cummax() - 1).min():.1%}  total {eq.iloc[-1] - 1:+.1%} over {years:.1f}y")


def main():
    for sym in ALTS + ["BTCUSDT"]:
        ensure_funding(sym)

    px = pd.DataFrame({s: load_px(s) for s in ALTS})
    fu = pd.DataFrame({s: load_funding_daily(s) for s in ALTS}).reindex(px.index).fillna(0.0)
    btc = load_px("BTCUSDT")
    btc_fu = load_funding_daily("BTCUSDT").reindex(px.index).fillna(0.0)

    print("=== per-alt mean funding, annualized (positive = shorts RECEIVE) ===")
    ann_f = {}
    for s in ALTS:
        listed = px[s].notna()
        ann_f[s] = float(fu[s][listed].mean() * ANN)
    print({s: f"{v:+.1%}" for s, v in sorted(ann_f.items())})
    print(f"basket mean: {np.mean(list(ann_f.values())):+.1%}/yr\n")

    # A. continuous basket: short alts equal-weight, long BTC same notional
    ret = px.pct_change()
    w = px.notna().div(px.notna().sum(axis=1), axis=0)      # equal weight, listed only
    price_leg = (w * (-ret)).sum(axis=1) + btc.pct_change().reindex(px.index)
    funding_leg = (w * fu).sum(axis=1) - btc_fu             # short alt receives, long BTC pays
    fees = w.diff().abs().sum(axis=1) * FEE_SIDE
    start = px.index[px.notna().any(axis=1)][0]
    print("=== A. secular-bleed basket (short 12 alts / long BTC, daily rebalance) ===")
    print(f"  span: {start.date()} .. {px.index[-1].date()}")
    print(f"  price legs only:     {metrics((price_leg - fees)[start:])}")
    print(f"  WITH funding:        {metrics((price_leg + funding_leg - fees)[start:])}")
    print(f"  funding drag alone:  mean {funding_leg[start:].mean() * ANN:+.1%}/yr on notional")
    net = (price_leg + funding_leg - fees)[start:]
    by_year = (1 + net).groupby(net.index.year).prod() - 1
    print("  net by year:", {y: f"{r:+.0%}" for y, r in by_year.items()}, "\n")

    # B. unlock run-up (D-8..D-1) hedged short, funding-adjusted per event
    events = pd.read_csv(f"{DATA}/unlock_events.csv")
    fu8 = {s: pd.read_csv(f"{DATA}/{s}_funding.csv") for s in ALTS + ["BTCUSDT"]}
    for s, f in fu8.items():
        f.index = pd.to_datetime(f["funding_time"], unit="ms", utc=True)
    trades = []
    for _, ev in events.iterrows():
        s = ev["symbol"]
        if s not in ALTS:
            continue
        d0 = pd.Timestamp(ev["date"], tz="UTC")
        t_in, t_out = d0 - pd.Timedelta(days=8), d0 - pd.Timedelta(days=1)
        p = px[s].dropna()
        ca = p[p.index <= t_in]
        cb = p[p.index <= t_out]
        m_a = btc[btc.index <= t_in]
        m_b = btc[btc.index <= t_out]
        if ca.empty or cb.empty or (t_in - ca.index[-1]).days > 3:
            continue
        r = cb.iloc[-1] / ca.iloc[-1] - 1
        m = m_b.iloc[-1] / m_a.iloc[-1] - 1
        f_alt = fu8[s].loc[(fu8[s].index > t_in) & (fu8[s].index <= t_out), "funding_rate"].astype(float).sum()
        f_btc = fu8["BTCUSDT"].loc[(fu8["BTCUSDT"].index > t_in) & (fu8["BTCUSDT"].index <= t_out), "funding_rate"].astype(float).sum()
        trades.append({"year": d0.year,
                       "price": (-r + m) - 4 * FEE_SIDE,
                       "net": (-r + m) + (f_alt - f_btc) - 4 * FEE_SIDE})
    tr = pd.DataFrame(trades)
    print("=== B. unlock run-up hedged short D-8..D-1, per event ===")
    for col, label in (("price", "price legs + fees"), ("net", "WITH funding")):
        x = tr[col]
        t = x.mean() / x.std() * np.sqrt(len(x))
        print(f"  {label:18s} N={len(x)} mean {x.mean():+.2%} median {x.median():+.2%} "
              f"win {(x > 0).mean():.0%} t={t:.2f}")
    print("  net by year:", {y: f"{g['net'].sum():+.0%}({len(g)})"
                             for y, g in tr.groupby("year")})


if __name__ == "__main__":
    main()
