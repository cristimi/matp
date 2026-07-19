"""The four gates for the cross-venue funding-spread trade (phase 5 follow-up).

Gate 1 — Blofin validation: rerun the slow grid with the REAL second leg
         (Blofin 8h funding) instead of the Binance proxy, on each pair's
         common span, with the Binance result recomputed on the same span for
         a fair comparison.
Gate 2 — Walk-forward: anchored half-year folds; config picked on fit data
         only (cash always a candidate), scored on the unseen half-year.
Gate 3 — Entry basis: HL-vs-Blofin hourly close basis; per-episode basis PnL
         (enter/exit at different venue marks) — noise or drag?
Gate 4 — Margin policy: per-episode max adverse excursion (Binance 1h closes,
         full history) -> implied safe leverage per leg vs liquidation.

Run: docker compose exec -T strategy-tester python - < research/spread_gates.py
Requires /tmp/edge-data from fetch_hl_funding.py, fetch_blofin_funding.py,
fetch_basis_klines.py and the phase-1/4 Binance funding CSVs.
"""
import glob
import os

import numpy as np
import pandas as pd

DATA = "/tmp/edge-data"
HOURS_ANN = 24 * 365
MAX_CONCURRENT = 3
SLOW_GRID = [(72, 0.30, 0.10), (72, 0.50, 0.10), (168, 0.30, 0.10), (168, 0.50, 0.10)]
COST = 0.003
BASIS_COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "NEAR", "APE", "GMT",
               "SEI", "TIA", "JTO", "BLUR"]


def _hourly(path, col_time="funding_time", col_val="funding_rate", per_hour_div=1.0,
            bfill_limit=0):
    df = pd.read_csv(path)
    if df.empty:
        return None
    s = pd.Series(df[col_val].astype(float).values / per_hour_div,
                  index=pd.to_datetime(df[col_time], unit="ms", utc=True).dt.round("h"))
    s = s[~s.index.duplicated()]
    if bfill_limit:
        s = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="h", tz="UTC")
                      ).bfill(limit=bfill_limit)
    return s


def _cex_hourly(path):
    """CEX settlement rates -> per-hour equivalent series. Interval inferred per
    settlement from timestamp gaps (Blofin mixes 8h and 4h cadences on some
    coins; Binance is uniformly 8h)."""
    df = pd.read_csv(path)
    if df.empty:
        return None
    ts = pd.to_datetime(df["funding_time"], unit="ms", utc=True).dt.round("h")
    s = pd.Series(df["funding_rate"].astype(float).values, index=ts)
    s = s[~s.index.duplicated()].sort_index()
    gap_h = s.index.to_series().diff().dt.total_seconds().div(3600).clip(1, 8).fillna(8)
    hourly_eq = s / gap_h
    full = pd.date_range(s.index.min(), s.index.max(), freq="h", tz="UTC")
    return hourly_eq.reindex(full).bfill(limit=7)


def load_pair(coin: str, cex: str) -> pd.DataFrame | None:
    hl_p = f"{DATA}/HL_{coin}_funding.csv"
    cex_p = (f"{DATA}/BLOFIN_{coin}_funding.csv" if cex == "blofin"
             else f"{DATA}/{coin}USDT_funding.csv")
    if not (os.path.exists(hl_p) and os.path.exists(cex_p)):
        return None
    hl = _hourly(hl_p)
    bn = _cex_hourly(cex_p)
    if hl is None or bn is None:
        return None
    df = pd.DataFrame({"hl": hl, "bn": bn}).dropna()
    return df if len(df) > 24 * 90 else None


def episodes(spread_ann, trail_h, enter, exit_):
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


def run_portfolio(pairs, trail_h, enter, exit_, cost):
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
    costs = entries.mul(w, fill_value=0.0).sum(axis=1) * cost
    return collect - costs, int(entries.sum().sum()), act


def coins_list():
    return sorted(p.split("/")[-1][3:-12] for p in glob.glob(f"{DATA}/HL_*_funding.csv"))


def gate1():
    print("=" * 70)
    print("GATE 1 — Blofin validation (real second leg vs Binance proxy)")
    print("=" * 70)
    bl_pairs = {c: d for c in coins_list() if (d := load_pair(c, "blofin")) is not None}
    print(f"coins with HL+Blofin history: {len(bl_pairs)}")
    if not bl_pairs:
        print("NO BLOFIN DATA — gate cannot run")
        return None
    start = min(d.index[0] for d in bl_pairs.values())
    bn_pairs = {c: d[d.index >= start]
                for c in bl_pairs if (d := load_pair(c, "binance")) is not None}
    bn_pairs = {c: d for c, d in bn_pairs.items() if len(d) > 24 * 90}
    rows = []
    for trail_h, enter, exit_ in SLOW_GRID:
        for cex, pairs in (("blofin", bl_pairs), ("binance(same span)", bn_pairs)):
            net, n_epi, _ = run_portfolio(pairs, trail_h, enter, exit_, COST)
            years = (net.index[-1] - net.index[0]).days / 365
            half = net.index[0] + (net.index[-1] - net.index[0]) / 2
            rows.append({"config": f"{trail_h}h {enter:.0%}/{exit_:.0%}", "cex_leg": cex,
                         "episodes": n_epi, "net/yr": f"{net.sum() / years:+.1%}",
                         "half1": f"{net[net.index < half].sum():+.1%}",
                         "half2": f"{net[net.index >= half].sum():+.1%}"})
    print(pd.DataFrame(rows).to_string(index=False))
    return bl_pairs


def gate2(bl_pairs):
    print("\n" + "=" * 70)
    print("GATE 2 — walk-forward (half-year folds, config picked on fit only)")
    print("=" * 70)
    for cex in ("blofin", "binance"):
        pairs = bl_pairs if cex == "blofin" else \
            {c: d for c in coins_list() if (d := load_pair(c, "binance")) is not None}
        series = {"cash": None}
        for trail_h, enter, exit_ in SLOW_GRID:
            net, _, _ = run_portfolio(pairs, trail_h, enter, exit_, COST)
            series[f"{trail_h}h {enter:.0%}/{exit_:.0%}"] = net.groupby(net.index.normalize()).sum()
        idx = next(s.index for s in series.values() if s is not None)
        series["cash"] = pd.Series(0.0, index=idx)
        folds = [("2024-07-01", "2025-01-01"), ("2025-01-01", "2025-07-01"),
                 ("2025-07-01", "2026-01-01"), ("2026-01-01", "2027-01-01")]
        oos_total = 0.0
        print(f"-- CEX leg: {cex} --")
        for f0, f1 in folds:
            t0, t1 = pd.Timestamp(f0, tz="UTC"), pd.Timestamp(f1, tz="UTC")
            scores = {k: s[s.index < t0].sum() for k, s in series.items()}
            best = max(scores, key=scores.get)
            oos = series[best][(series[best].index >= t0) & (series[best].index < t1)].sum()
            oos_total += oos
            print(f"  fold {f0[:7]}..{f1[:7]}: picked '{best}' "
                  f"(fit net {scores[best]:+.1%}) -> OOS {oos:+.1%}")
        print(f"  stitched OOS total (2y): {oos_total:+.1%} on notional "
              f"= {oos_total / 2:+.1%}/yr\n")


def gate3(bl_pairs):
    print("=" * 70)
    print("GATE 3 — HL-vs-Blofin entry basis (hourly closes, ~7mo window)")
    print("=" * 70)
    rows, per_episode = [], []
    for c in BASIS_COINS:
        hp, bp = f"{DATA}/HL1H_{c}.csv", f"{DATA}/BLOFIN1H_{c}.csv"
        if not (os.path.exists(hp) and os.path.exists(bp)):
            continue
        hl = _hourly(hp, "open_time", "close")
        bf = _hourly(bp, "open_time", "close")
        j = pd.DataFrame({"hl": hl, "bf": bf}).dropna()
        if len(j) < 24 * 30:
            continue
        basis = j["hl"] / j["bf"] - 1
        rows.append({"coin": c, "hours": len(j), "mean_bps": f"{basis.mean() * 1e4:+.1f}",
                     "std_bps": f"{basis.std() * 1e4:.1f}",
                     "p95|b|_bps": f"{basis.abs().quantile(0.95) * 1e4:.1f}"})
        if c in bl_pairs:
            d = bl_pairs[c]
            spread_ann = (d["hl"] - d["bn"]) * HOURS_ANN
            act = episodes(spread_ann, 168, 0.30, 0.10)
            ent = act & ~act.shift(1, fill_value=False)
            exi = ~act & act.shift(1, fill_value=False)
            e_times, x_times = list(act.index[ent]), list(act.index[exi])
            for et in e_times:
                xt = next((x for x in x_times if x > et), None)
                if xt is None or et not in basis.index or xt not in basis.index:
                    continue
                sign = 1 if spread_ann.rolling(168).mean().get(et, 0) > 0 else -1
                # short HL when HL funding higher: pnl = basis_entry - basis_exit
                per_episode.append(sign * (basis[et] - basis[xt]))
    print(pd.DataFrame(rows).to_string(index=False))
    if per_episode:
        x = pd.Series(per_episode)
        print(f"\nper-episode basis PnL (N={len(x)}): mean {x.mean() * 1e4:+.1f}bps  "
              f"std {x.std() * 1e4:.1f}bps  min {x.min() * 1e4:+.1f}bps  "
              f"(vs mean episode funding collect ~50bps)")


def gate4():
    print("\n" + "=" * 70)
    print("GATE 4 — margin: max adverse excursion per episode (Binance 1h, full 3y)")
    print("=" * 70)
    ups, downs, lens = [], [], []
    for c in BASIS_COINS:
        kp = f"{DATA}/BN1H_{c}.csv"
        d = load_pair(c, "binance")
        if d is None or not os.path.exists(kp):
            continue
        px = _hourly(kp, "open_time", "close")
        spread_ann = (d["hl"] - d["bn"]) * HOURS_ANN
        act = episodes(spread_ann, 168, 0.30, 0.10)
        ent = act & ~act.shift(1, fill_value=False)
        in_ep, entry_px = False, None
        cur_max = cur_min = None
        for t, a in act.items():
            p = px.get(t)
            if a and not in_ep:
                in_ep, entry_px = True, p
                cur_max = cur_min = p
                n = 0
            elif a and in_ep and p is not None and entry_px is not None:
                cur_max, cur_min = max(cur_max, p), min(cur_min, p)
                n += 1
            elif not a and in_ep:
                if entry_px and cur_max and cur_min:
                    ups.append(cur_max / entry_px - 1)
                    downs.append(1 - cur_min / entry_px)
                    lens.append(n)
                in_ep = False
    up, dn = pd.Series(ups), pd.Series(downs)
    print(f"episodes measured: {len(up)}  (config 168h 30%/10%, mean length {np.mean(lens):.0f}h)")
    for name, s in (("adverse for SHORT leg (max rise)", up),
                    ("adverse for LONG leg (max fall)", dn)):
        print(f"  {name}: p50 {s.quantile(0.5):.1%}  p90 {s.quantile(0.9):.1%}  "
              f"p99 {s.quantile(0.99):.1%}  max {s.max():.1%}")
    p99 = max(up.quantile(0.99), dn.quantile(0.99))
    mx = max(up.max(), dn.max())
    print(f"  implied max leverage to survive p99 move (maint 1%): "
          f"{1 / (p99 + 0.01):.1f}x; to survive worst-ever: {1 / (mx + 0.01):.1f}x")
    print("  (isolated margin per leg, no top-up. An auto top-up watcher relaxes this.)")


def main():
    bl_pairs = gate1()
    if bl_pairs:
        gate2(bl_pairs)
        gate3(bl_pairs)
    gate4()


if __name__ == "__main__":
    main()
