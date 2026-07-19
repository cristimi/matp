"""Robustness controls for the unlock event study (run after event_study_unlocks.py).

Two checks that decide whether the unlock effect is real:
1. Split-sample: run-up and drift 'strategy' returns, 2022-2024 vs 2025-2026.
2. Baseline control: every token in this universe bleeds vs BTC on average
   (low-float/high-FDV secular underperformance). Subtract each token's own
   mean daily abnormal drift x window length from its event windows — what
   survives is the *timing* effect attributable to the unlock calendar itself.

Result 2026-07-19: the D+7..D+30 'drift edge' (raw t=-6.7) collapses to
t=-1.1 after the baseline control — it was ~85% secular bleed. The run-up
week (D-8..D-1) keeps a modest genuine effect: -1.47% excess, t=-2.44.

Run: docker compose exec -T strategy-tester python - < research/unlock_controls.py
"""
import numpy as np
import pandas as pd

DATA = "/tmp/edge-data"
FEE_SIDE = 0.0008
WINDOWS = [("RUN-UP D-8..D-1", -8, -1), ("DRIFT D+7..D+30", 7, 30)]


def load_px(sym):
    k = pd.read_csv(f"{DATA}/{sym}_daily.csv")
    idx = pd.to_datetime(k["open_time"], unit="ms", utc=True).dt.normalize()
    return pd.Series(k["close"].astype(float).values, index=idx)


def close_at(px, day):
    s = px[px.index <= day]
    return float(s.iloc[-1]) if len(s) and (day - s.index[-1]).days <= 3 else None


def wret(px, d0, a, b):
    ca = close_at(px, d0 + pd.Timedelta(days=a))
    cb = close_at(px, d0 + pd.Timedelta(days=b))
    return cb / ca - 1 if ca and cb else None


def main():
    events = pd.read_csv(f"{DATA}/unlock_events.csv")
    btc = load_px("BTCUSDT")
    px = {s: load_px(s) for s in events["symbol"].unique()}

    print("=== 1. split-sample: hedged short per event window, fees 0.32%/trade ===")
    for label, a, b in WINDOWS:
        tr = []
        for _, ev in events.iterrows():
            d0 = pd.Timestamp(ev["date"], tz="UTC")
            r, m = wret(px[ev["symbol"]], d0, a, b), wret(btc, d0, a, b)
            if r is None or m is None:
                continue
            tr.append({"year": d0.year, "net": (-r + m) - 4 * FEE_SIDE})
        t = pd.DataFrame(tr)
        for name, sub in (("ALL", t), ("2022-2024", t[t.year <= 2024]),
                          ("2025-2026", t[t.year >= 2025])):
            ts = sub["net"].mean() / sub["net"].std() * np.sqrt(len(sub))
            print(f"  {label:16s} {name:9s} N={len(sub):3d} mean {sub['net'].mean():+.2%} "
                  f"win {(sub['net'] > 0).mean():.0%} t={ts:.2f}")

    print("\n=== 2. baseline control: excess over each token's own bleed vs BTC ===")
    base = {}
    for s, p in px.items():
        j = pd.concat([p, btc], axis=1, keys=["a", "b"], sort=False).dropna()
        base[s] = float((np.log(j["a"]).diff() - np.log(j["b"]).diff()).mean())
    print("  baseline daily abnormal drift (%/day):",
          {s: f"{v * 100:+.2f}" for s, v in sorted(base.items())})
    for label, a, b in WINDOWS:
        L = b - a
        raw, excess = [], []
        for _, ev in events.iterrows():
            d0 = pd.Timestamp(ev["date"], tz="UTC")
            r, m = wret(px[ev["symbol"]], d0, a, b), wret(btc, d0, a, b)
            if r is None or m is None:
                continue
            abn = np.log(1 + r) - np.log(1 + m)
            raw.append(abn)
            excess.append(abn - base[ev["symbol"]] * L)
        for name, x in (("raw abnormal", raw), ("EXCESS over baseline", excess)):
            x = pd.Series(x)
            ts = x.mean() / x.std() * np.sqrt(len(x))
            print(f"  {label:16s} {name:21s} N={len(x)} mean {x.mean():+.2%} "
                  f"median {x.median():+.2%} neg {(x < 0).mean():.0%} t={ts:.2f}")


if __name__ == "__main__":
    main()
