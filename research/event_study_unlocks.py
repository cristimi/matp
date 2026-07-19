"""Event study: token cliff unlocks vs perp price (unlock supply-pressure edge).

Hypothesis: large scheduled cliff unlocks create price-insensitive selling;
prices should show abnormal (market-adjusted) weakness into/after the unlock.

Method: for each unlock event (fetch_unlocks.py), take daily closes around the
unlock date D0. c(k) = close on D0+k calendar days (nearest prior trading day).
Abnormal return = coin return - BTC return over the same window. Windows:
run-up (D-8..D-1), D-1..D+1, D-1..D+3, D-1..D+7, drift (D+7..D+30).

Also a naive strategy sim: short at close D-2, cover at close D+1, market-hedged
with an equal long BTC leg, fees 0.08%/side on both legs (4 sides total),
for events >= SIZE_MIN_PCT of circulating supply.

Run: docker compose exec -T strategy-tester python - < research/event_study_unlocks.py
Requires /tmp/edge-data from fetch_unlocks.py.
"""
import numpy as np
import pandas as pd

DATA = "/tmp/edge-data"
FEE_SIDE = 0.0008          # taker + slippage per side, per leg
SIZE_BUCKETS = [(0.5, 2), (2, 5), (5, 100)]
STRAT_MIN_PCT = 2.0
WINDOWS = [("run-up D-8..D-1", -8, -1), ("D-1..D+1", -1, 1), ("D-1..D+3", -1, 3),
           ("D-1..D+7", -1, 7), ("drift D+7..D+30", 7, 30)]


def load_px(sym: str) -> pd.Series:
    k = pd.read_csv(f"{DATA}/{sym}_daily.csv")
    idx = pd.to_datetime(k["open_time"], unit="ms", utc=True).dt.normalize()
    return pd.Series(k["close"].astype(float).values, index=idx)


def close_at(px: pd.Series, day: pd.Timestamp) -> float | None:
    s = px[px.index <= day]
    if s.empty or (day - s.index[-1]).days > 3:
        return None
    return float(s.iloc[-1])


def window_ret(px: pd.Series, d0: pd.Timestamp, a: int, b: int) -> float | None:
    ca = close_at(px, d0 + pd.Timedelta(days=a))
    cb = close_at(px, d0 + pd.Timedelta(days=b))
    if ca is None or cb is None or ca <= 0:
        return None
    return cb / ca - 1


def main():
    events = pd.read_csv(f"{DATA}/unlock_events.csv")
    btc = load_px("BTCUSDT")
    px = {s: load_px(s) for s in events["symbol"].unique()}

    rows = []
    for _, ev in events.iterrows():
        d0 = pd.Timestamp(ev["date"], tz="UTC")
        p = px[ev["symbol"]]
        if close_at(p, d0 - pd.Timedelta(days=10)) is None:
            continue  # perp not listed long enough before the event
        rec = {"symbol": ev["symbol"], "date": ev["date"], "size_pct": ev["size_pct"]}
        ok = True
        for name, a, b in WINDOWS:
            r, m = window_ret(p, d0, a, b), window_ret(btc, d0, a, b)
            if r is None or m is None:
                ok = False
                break
            rec[name] = r - m
        if ok:
            rows.append(rec)
    df = pd.DataFrame(rows)
    print(f"events with full price coverage: {len(df)} "
          f"(of {len(events)} extracted) across {df['symbol'].nunique()} tokens\n")

    def stats(sub: pd.DataFrame, label: str):
        print(f"--- {label} (N={len(sub)}) — abnormal returns vs BTC ---")
        out = {}
        for name, _, _ in WINDOWS:
            x = sub[name].dropna()
            t = x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else np.nan
            out[name] = {"mean": f"{x.mean():+.2%}", "median": f"{x.median():+.2%}",
                         "%neg": f"{(x < 0).mean():.0%}", "t": f"{t:.2f}"}
        print(pd.DataFrame(out).T.to_string(), "\n")

    stats(df, "ALL events")
    for lo, hi in SIZE_BUCKETS:
        sub = df[(df["size_pct"] >= lo) & (df["size_pct"] < hi)]
        if len(sub) >= 5:
            stats(sub, f"size {lo}-{hi}% of circulating")

    # naive hedged short sim: short alt c(-2) -> c(+1), long BTC same window
    trades = []
    for _, ev in events.iterrows():
        if ev["size_pct"] < STRAT_MIN_PCT:
            continue
        d0 = pd.Timestamp(ev["date"], tz="UTC")
        r = window_ret(px[ev["symbol"]], d0, -2, 1)
        m = window_ret(btc, d0, -2, 1)
        if r is None or m is None:
            continue
        net = (-r + m) - 4 * FEE_SIDE      # short alt + long BTC, 4 fee sides
        trades.append({"date": ev["date"], "symbol": ev["symbol"],
                       "size_pct": ev["size_pct"], "net": net})
    tr = pd.DataFrame(trades)
    if len(tr):
        tr["year"] = pd.to_datetime(tr["date"]).dt.year
        print(f"--- STRATEGY SIM: hedged short D-2..D+1, events >={STRAT_MIN_PCT}% circ, "
              f"fees {4 * FEE_SIDE:.2%}/trade ---")
        print(f"trades: {len(tr)}  win rate: {(tr['net'] > 0).mean():.0%}  "
              f"mean net: {tr['net'].mean():+.2%}  median: {tr['net'].median():+.2%}  "
              f"total (sum): {tr['net'].sum():+.1%}")
        t = tr["net"].mean() / tr["net"].std() * np.sqrt(len(tr))
        print(f"t-stat: {t:.2f}")
        print("\nby year (sum of per-trade net, equal $ per trade):")
        for y, g in tr.groupby("year"):
            print(f"  {y}: {g['net'].sum():+.1%}  ({len(g)} trades, {(g['net'] > 0).mean():.0%} win)")
        worst = tr.nsmallest(3, "net")[["date", "symbol", "size_pct", "net"]]
        print("\nworst trades:\n", worst.to_string(index=False))


if __name__ == "__main__":
    main()
