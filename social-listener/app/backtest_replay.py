"""Replay social signals through the CURRENT gates and price the resulting positions.

Read-only: reads social_signal_log (v1 extractions) or a JSON dump from
backtest_extract (v2), and a 1m OHLCV file. Writes nothing to the live tables.

    python -m app.backtest_replay <ohlcv.json> <days> [--v2 x.json] [--funding f.json] [--v2-only]

Execution model (all assumptions explicit, all overridable below):
  * decision time  = the row's real ingested_at (measured p50 18.8s after posting)
  * fill price     = close of the 1m bar containing the decision time
  * cost per fill  = taker fee + adverse slippage, charged on notional
  * a flip is two fills (close + open)
  * fixed notional, 1x, no compounding
  * perp funding applied per 8h period held, when a funding file is supplied
  * NO stop loss — the position runs until the next signal, so MAE (worst
    unrealised drawdown inside a leg) is reported alongside the result
"""
import argparse
import asyncio
import bisect
import json
import sys
from datetime import datetime, timedelta, timezone

from app import db
from app.statemachine import evaluate

NOTIONAL = 10_000.0
TAKER_FEE = 0.0006      # 0.06% per fill
SLIPPAGE = 0.0002       # 0.02% adverse per fill
COST_PER_FILL = NOTIONAL * (TAKER_FEE + SLIPPAGE)
FUNDING: list[dict] = []   # [{"t": ms, "r": rate}] loaded from --funding


class Prices:
    def __init__(self, bars: list[dict]):
        self.ts = [b["timestamp"] for b in bars]
        self.close = [b["close"] for b in bars]
        self.low = [b["low"] for b in bars]
        self.high = [b["high"] for b in bars]

    def at(self, ts_ms: int) -> float | None:
        """Close of the 1m bar covering ts_ms (None outside coverage)."""
        i = bisect.bisect_right(self.ts, ts_ms) - 1
        if i < 0 or ts_ms - self.ts[i] > 300_000:
            return None
        return self.close[i]

    def extremes(self, a_ms: int, b_ms: int) -> tuple[float, float]:
        """(min low, max high) between two timestamps — for MAE/MFE."""
        i = bisect.bisect_left(self.ts, a_ms)
        j = bisect.bisect_right(self.ts, b_ms)
        lo = min(self.low[i:j], default=0.0)
        hi = max(self.high[i:j], default=0.0)
        return lo, hi


async def load_v1(days: int) -> list[dict]:
    async with db.pool().acquire() as c:
        rows = await c.fetch(
            """SELECT channel_msg_id, posted_at, ingested_at, is_actionable, action_type,
                      asset, direction, reference_price, confidence, has_image
               FROM public.social_signal_log
               WHERE posted_at >= now() - ($1 || ' days')::interval
               ORDER BY posted_at""",
            str(days),
        )
    out = []
    for r in rows:
        d = dict(r)
        # asyncpg returns NUMERIC as Decimal; the gates do float arithmetic
        for k in ("reference_price", "confidence"):
            if d[k] is not None:
                d[k] = float(d[k])
        out.append(d)
    return out


async def load_v2(path: str, days: int) -> list[dict]:
    with open(path) as f:
        recs = json.load(f)
    # real ingest latency per message, so both runs decide at the same moment
    async with db.pool().acquire() as c:
        rows = await c.fetch(
            "SELECT channel_msg_id, ingested_at FROM public.social_signal_log"
            " WHERE posted_at >= now() - ($1 || ' days')::interval",
            str(days),
        )
    lat = {r["channel_msg_id"]: r["ingested_at"] for r in rows}
    out, assumed = [], 0
    for r in recs:
        posted = datetime.fromisoformat(r["posted_at"])
        real = lat.get(r["channel_msg_id"])
        if real is None:
            # Predates social_signal_log — assume the measured live p50 (18.8s).
            real = posted + timedelta(seconds=19)
            assumed += 1
        out.append({**r, "posted_at": posted, "ingested_at": real})
    out.sort(key=lambda r: r["posted_at"])
    print(f"decision times: {len(out)-assumed} measured, {assumed} assumed (+19s, the live p50)")
    return out


async def initial_states(days: int) -> dict[str, str]:
    """Position per asset at the window's open, from the last acted decision before it.

    Replaying from FLAT would invent an entry the live system never had — the
    channel's stance carries in from before the window.
    """
    async with db.pool().acquire() as c:
        rows = await c.fetch(
            """SELECT DISTINCT ON (asset) asset, to_state
               FROM public.social_shadow_orders
               WHERE decision='acted' AND asset IS NOT NULL
                 AND posted_at < now() - ($1 || ' days')::interval
               ORDER BY asset, posted_at DESC""",
            str(days),
        )
    return {r["asset"]: r["to_state"] for r in rows}


def replay(recs: list[dict], px: Prices, label: str, seed: dict[str, str] | None = None) -> dict:
    """Price the channel as a SINGLE position per asset — net mode.

    Deliberately not multi-position. The P&L walk below assumes one position at a
    time per asset: it pairs each transition with the next one to find the exit, and
    a second concurrent leg has no place in that timeline. `evaluate` is therefore
    called with multi=False, which reproduces exactly what a net account does — an
    OPEN against the opposite side is a flip. A hedge-mode backtest needs the walk
    rewritten to track two legs, and until it is, this tool answers the net question
    only. Any 'LONG+SHORT' seed from a live hedge run is refused rather than
    silently read as one side.
    """
    for asset, stance in (seed or {}).items():
        if "+" in str(stance):
            raise ValueError(
                f"{asset} was holding two legs ({stance}) at the window's open. This "
                f"replay prices one position per asset and cannot seed from a hedge "
                f"stance — pick a window that opens with at most one leg per asset."
            )
    state: dict[str, str] = dict(seed or {})
    transitions: list[dict] = []
    reasons: dict[str, int] = {}

    for r in recs:
        if not r.get("is_actionable"):
            continue
        asset = (r.get("asset") or "").upper() or None
        cur = state.get(asset, "FLAT")
        decided_at = r["ingested_at"]
        d_ms = int(decided_at.timestamp() * 1000)
        p_ms = int(r["posted_at"].timestamp() * 1000)

        mark = px.at(d_ms) if asset == "BTC" else None
        implied = px.at(p_ms) if (asset == "BTC" and r.get("reference_price") is None) else None

        d = evaluate(r, "live", cur, mark, implied, now=decided_at, multi=False)
        reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1

        if d["advance"] and asset:
            fill = px.at(d_ms)
            if fill is None:
                reasons["_unpriceable"] = reasons.get("_unpriceable", 0) + 1
                continue
            transitions.append({
                "msg": r["channel_msg_id"], "at": decided_at, "asset": asset,
                "from": cur, "to": d["to_state"], "price": fill,
                "reason": d["reason"], "has_image": r.get("has_image", False),
            })
            state[asset] = d["to_state"]

    # price the position timeline (BTC only — the sole asset with price data)
    legs, equity, fills = [], 0.0, 0
    btc = [t for t in transitions if t["asset"] == "BTC"]
    for i, t in enumerate(btc):
        if t["from"] != "FLAT":
            fills += 1
        if t["to"] != "FLAT":
            fills += 1
        if t["to"] == "FLAT":
            continue
        nxt = btc[i + 1] if i + 1 < len(btc) else None
        exit_px = nxt["price"] if nxt else px.close[-1]
        exit_at = nxt["at"] if nxt else datetime.fromtimestamp(px.ts[-1] / 1000, timezone.utc)
        sign = 1 if t["to"] == "LONG" else -1
        gross = NOTIONAL * sign * (exit_px - t["price"]) / t["price"]
        # perp funding: longs pay a positive rate, shorts receive it
        fund = 0.0
        for f in FUNDING:
            if t["at"].timestamp() * 1000 <= f["t"] < exit_at.timestamp() * 1000:
                fund -= sign * f["r"] * NOTIONAL

        lo, hi = px.extremes(int(t["at"].timestamp() * 1000), int(exit_at.timestamp() * 1000))
        worst = lo if sign == 1 else hi
        best = hi if sign == 1 else lo
        mae = NOTIONAL * sign * (worst - t["price"]) / t["price"]
        mfe = NOTIONAL * sign * (best - t["price"]) / t["price"]

        legs.append({
            "msg": t["msg"], "side": t["to"], "entry_at": t["at"], "exit_at": exit_at,
            "entry": t["price"], "exit": exit_px, "gross": gross, "funding": fund,
            "mae": mae, "mfe": mfe,
            "open": nxt is None, "reason": t["reason"], "has_image": t["has_image"],
        })
        equity += gross

    funding = sum(l["funding"] for l in legs)
    costs = fills * COST_PER_FILL
    return {
        "label": label, "signals": len(recs),
        "actionable": sum(1 for r in recs if r.get("is_actionable")),
        "reasons": reasons, "transitions": transitions, "legs": legs,
        "gross": equity, "fills": fills, "costs": costs, "funding": funding,
        "net": equity - costs + funding,
    }


def report(res: dict, px: Prices):
    print(f"\n{'='*74}\n{res['label']}\n{'='*74}")
    print(f"messages={res['signals']}  actionable={res['actionable']}")
    print("gate outcomes:", ", ".join(f"{k}={v}" for k, v in sorted(res["reasons"].items())))
    if not res["legs"]:
        print("no positions taken")
        return
    print(f"{'msg':>7} {'side':<6} {'entry':>9} {'exit':>9} {'held':>8} {'gross $':>9} {'fund $':>7} {'MAE $':>8} {'MFE $':>8}  img reason")
    for l in res["legs"]:
        held = l["exit_at"] - l["entry_at"]
        h = f"{held.total_seconds()/3600:.1f}h"
        print(f"{l['msg']:>7} {l['side']:<6} {l['entry']:>9.1f} {l['exit']:>9.1f} {h:>8} "
              f"{l['gross']:>9.2f} {l['funding']:>7.2f} {l['mae']:>8.2f} {l['mfe']:>8.2f}  "
              f"{'y' if l['has_image'] else 'n'}  {l['reason']}"
              f"{' (OPEN)' if l['open'] else ''}")
    wins = [l for l in res["legs"] if l["gross"] > 0]
    print(f"\nlegs={len(res['legs'])}  wins={len(wins)}  fills={res['fills']}")
    print(f"gross ${res['gross']:.2f}   fees ${res['costs']:.2f}   funding ${res['funding']:.2f}   NET ${res['net']:.2f} "
          f"({res['net']/NOTIONAL*100:+.2f}% on ${NOTIONAL:,.0f} notional)")


async def main(args):
    await db.init_db()
    if args.funding:
        with open(args.funding) as f:
            FUNDING.extend(json.load(f))
    with open(args.ohlcv) as f:
        bars = json.load(f)
    px = Prices(bars)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    bars_in = [b for b in bars if b["timestamp"] >= cutoff.timestamp() * 1000]
    bh = (bars[-1]["close"] - bars_in[0]["close"]) / bars_in[0]["close"] * 100
    print(f"window: {args.days}d   BTC {bars_in[0]['close']:.1f} -> {bars[-1]['close']:.1f} "
          f"({bh:+.2f}% buy & hold)")
    print(f"costs: taker {TAKER_FEE*1e4:.0f}bps + slippage {SLIPPAGE*1e4:.0f}bps per fill, "
          f"${NOTIONAL:,.0f} notional, funding "
          f"{'modelled (' + str(len(FUNDING)) + ' pts)' if FUNDING else 'NOT modelled'}")

    seed = await initial_states(args.days)
    print(f"seeded state at window open: {seed or '{} (FLAT — no recorded stance before window)'}")

    if not args.v2_only:
        report(replay(await load_v1(args.days), px, "v1 — stored text-only extractions", seed), px)
    if args.v2:
        report(replay(await load_v2(args.v2, args.days), px,
                      "v2 — re-extracted WITH chart images", seed), px)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ohlcv", help="1m OHLCV json from historical_ohlcv")
    ap.add_argument("days", type=int)
    ap.add_argument("--v2", help="extractions json from backtest_extract")
    ap.add_argument("--funding", help="funding rate history json")
    ap.add_argument("--v2-only", action="store_true",
                    help="skip the v1 (stored extraction) run")
    asyncio.run(main(ap.parse_args()))
