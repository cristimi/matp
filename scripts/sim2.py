#!/usr/bin/env python3
"""
Counterfactual v2: each AI limit order placed ONCE, never amended.

Two horizons, because "never filled" is ambiguous otherwise:
  A) real  — the order dies when the real one died (cancelled/filled). Every
             other decision the strategy made is kept; only amends vanish.
  B) open  — the order rests until it fills or until now. What "place it and
             walk away" would really have produced.

Same-bar honesty: when the fill and an exit land in the same bar, the bar is
drilled into finer candles so the exit can only count from the fill onward.
Where no finer candles exist the case is flagged, not silently resolved.
"""
import csv, datetime as dt
from pathlib import Path

D = Path(__file__).parent
NOW_MS = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
BAND = 0.40
TFS = ["1h", "15m", "5m", "1m"]
SEC = {"1h": 3600, "15m": 900, "5m": 300, "1m": 60}


def load(tf):
    out = []
    with open(D / f"eth_{tf}.csv") as f:
        for row in csv.reader(f):
            if len(row) == 5:
                t, o, h, l, c = row
                out.append((int(t), float(o), float(h), float(l), float(c)))
    return sorted(out)


C = {tf: load(tf) for tf in TFS}


def series(t0, t1):
    """Finest candle series that actually covers t0 — coarse for old dates."""
    for tf in reversed(TFS):
        s = C[tf]
        if s and s[0][0] <= t0:
            return tf, [b for b in s if t0 <= b[0] <= t1]
    return "1h", [b for b in C["1h"] if t0 <= b[0] <= t1]


def sub(tf, t_open):
    """Finer candles inside one bar, or None."""
    i = TFS.index(tf)
    for f in TFS[i + 1:]:
        s = [b for b in C[f] if t_open <= b[0] < t_open + SEC[tf] * 1000]
        if s:
            return f, s
    return None, None


def scan_exit(bar, tf, is_long, tp, sl, after_fill_only):
    """('tp'|'sl'|None, flagged). after_fill_only drills so the fill precedes."""
    _, _, h, l, _ = bar
    hit_tp = h >= tp if is_long else l <= tp
    hit_sl = l <= sl if is_long else h >= sl
    if not hit_tp and not hit_sl:
        return None, False
    if hit_tp != hit_sl and not after_fill_only:
        return ("tp" if hit_tp else "sl"), False
    f, s = sub(tf, bar[0])
    if s:
        for sb in s:
            r, fl = scan_exit(sb, f, is_long, tp, sl, False)
            if r:
                return r, fl
        return None, False
    return ("sl" if hit_sl else "tp"), True


def simulate(entry, sl, tp, is_long, t0, t1):
    tf, win = series(t0, t1)
    if not win:
        return dict(kind="no_candles", fill=None, exit_t=None, exit_px=None, flag=False)
    fi = None
    for i, b in enumerate(win):
        if (b[3] <= entry) if is_long else (b[2] >= entry):
            fi = i
            break
    if fi is None:
        return dict(kind="never_filled", fill=None, exit_t=None, exit_px=None, flag=False)

    # Fill bar: the exit may only count from the fill moment onward.
    ftf, fsub = sub(tf, win[fi][0])
    if fsub:
        started = False
        for sb in fsub:
            if not started:
                if (sb[3] <= entry) if is_long else (sb[2] >= entry):
                    started = True
                else:
                    continue
            r, fl = scan_exit(sb, ftf, is_long, tp, sl, False)
            if r:
                return dict(kind=r, fill=win[fi][0], exit_t=sb[0],
                            exit_px=(tp if r == "tp" else sl), flag=fl)
        same_bar_flag = False
    else:
        # No finer candles under the fill bar. The ordering is still knowable
        # for a stop: a resting entry always sits BETWEEN the market and its
        # stop, so price must cross the entry before it can reach the stop.
        # A target in the same bar is genuinely ambiguous — price could have
        # spiked to it before ever coming back to fill.
        b = win[fi]
        hit_tp = b[2] >= tp if is_long else b[3] <= tp
        hit_sl = b[3] <= sl if is_long else b[2] >= sl
        if hit_sl:
            return dict(kind="sl", fill=b[0], exit_t=b[0], exit_px=sl,
                        flag=bool(hit_tp))
        if hit_tp:
            return dict(kind="tp", fill=b[0], exit_t=b[0], exit_px=tp, flag=True)
        same_bar_flag = False

    for b in win[fi + 1:]:
        r, fl = scan_exit(b, tf, is_long, tp, sl, False)
        if r:
            return dict(kind=r, fill=win[fi][0], exit_t=b[0],
                        exit_px=(tp if r == "tp" else sl), flag=fl or same_bar_flag)
    return dict(kind="still_open", fill=win[fi][0], exit_t=None,
                exit_px=win[-1][4], flag=same_bar_flag)


def pnl(res, entry, is_long, sz):
    if res["kind"] not in ("tp", "sl", "still_open"):
        return 0.0
    ex = res["exit_px"]
    return (ex - entry) * sz if is_long else (entry - ex) * sz


def fmt(ms):
    return "-" if ms is None else dt.datetime.fromtimestamp(
        ms / 1000, dt.timezone.utc).strftime("%m-%d %H:%M")


rows = [l.rstrip("\n").split("|") for l in open(D / "orders.psv")]

for horizon in ("real", "open"):
    print(f"\n{'='*118}")
    print(f"HORIZON = {horizon}   " + (
        "order dies when the real one did (cancelled/filled)" if horizon == "real"
        else "order rests until it fills, or until now"))
    print("=" * 118)
    print(f"{'order':10} {'side':5} {'real end':10} {'orig px':>9} {'fill':>12} "
          f"{'result':>11} {'exit':>12} {'$ no-amend':>11} {'$ real':>8} {'flag':>5}")
    print("-" * 118)

    t_real = t_no = t_band = 0.0
    nf = nb = tp_no = tp_b = flags = 0
    n = 0
    for p in rows:
        (oid, side, status, t0s, t1s, opx, osl, otp,
         fpx, fsl, ftp, size, afp, err, rpnl, creason, cpx) = p[:17]
        if status == "rejected":
            continue
        n += 1
        t0 = int(float(t0s))
        t1 = NOW_MS if (horizon == "open" or status == "pending") else int(float(t1s))
        entry, sl, tp = float(opx), float(osl), float(otp)
        sz = float(size)
        is_long = side == "buy"
        real_pnl = float(rpnl) if rpnl else 0.0

        r = simulate(entry, sl, tp, is_long, t0, t1)
        g = pnl(r, entry, is_long, sz)
        if r["kind"] in ("tp", "sl", "still_open"):
            nf += 1
        if r["kind"] == "tp":
            tp_no += 1
        if r["flag"]:
            flags += 1

        rr = abs(entry - sl)
        be = entry + BAND * rr if is_long else entry - BAND * rr
        rb = simulate(be, sl, tp, is_long, t0, t1)
        gb = pnl(rb, be, is_long, sz)
        if rb["kind"] in ("tp", "sl", "still_open"):
            nb += 1
        if rb["kind"] == "tp":
            tp_b += 1

        t_real += real_pnl
        t_no += g
        t_band += gb

        print(f"{oid[:8]:10} {side:5} {status:10} {entry:9.2f} {fmt(r['fill']):>12} "
              f"{r['kind']:>11} {fmt(r['exit_t']):>12} {g:11.2f} {real_pnl:8.2f} "
              f"{'!' if r['flag'] else '':>5}")

    print("-" * 118)
    print(f"orders on the exchange: {n}      filled without amends: {nf}"
          f"   (targets reached: {tp_no})")
    print(f"                                 filled with 40% band : {nb}"
          f"   (targets reached: {tp_b})")
    print(f"same-bar cases with no finer candles to settle the order: {flags}")
    print(f"  REAL, as traded (with amends) : {t_real:8.2f} USD")
    print(f"  NO AMENDS                     : {t_no:8.2f} USD   diff {t_no - t_real:+.2f}")
    print(f"  NO AMENDS + 40% entry band    : {t_band:8.2f} USD   diff {t_band - t_real:+.2f}")
print("\nAll figures gross, before fees. Fees on this size are roughly $0.10-0.50 per round trip.")
