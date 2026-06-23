"""
Entry/exit shadow-diff harness.
Usage:
  python -m app.diff replay <strategy_id> <since_iso>
  python -m app.diff live   <strategy_id>
  python -m app.diff exits  <strategy_id> [window_min]

Ground truth = public.orders for the given strategy_id.
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone

import asyncpg

from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_TIMEFRAME_MS = {"1h": 3_600_000}

VERDICT_MATCHED        = "matched"
VERDICT_MISSING_IN_TV  = "missing_in_tv"   # local fired, no matching TV order
VERDICT_EXTRA_LOCAL    = "extra_local"      # TV fired, no matching local signal
VERDICT_SIDE_MISMATCH  = "side_mismatch"
VERDICT_BAR_OFFSET     = "bar_offset"


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _round_to_bar(dt: datetime, tf_ms: int) -> int:
    ms = _dt_to_ms(dt)
    return (ms // tf_ms) * tf_ms


def _print_table(rows: list[dict]) -> None:
    header = f"{'BAR (UTC)':<22}  {'TV signal':<14}  {'Local signal':<16}  Verdict"
    print(header)
    print("-" * 70)
    for r in rows:
        bar_str = _ms_to_dt(r["bar_ms"]).strftime("%Y-%m-%d %H:%M") if r["bar_ms"] else "?"
        tv_sig  = r.get("tv_signal")  or "-"
        loc_sig = r.get("local_signal") or "-"
        verdict = r["verdict"]
        print(f"{bar_str:<22}  {tv_sig:<14}  {loc_sig:<16}  {verdict}")


async def _fetch_tv_entries(conn, strategy_id: str, since_ms: int) -> dict[int, dict]:
    """Ground truth keyed on strategy_id: the test-harness strategy is dedicated,
    so every one of its orders is a TV entry — independent of whether the listener
    preserves the payload's signal_source."""
    tf_ms = _TIMEFRAME_MS["1h"]
    since_dt = _ms_to_dt(since_ms)
    rows = await conn.fetch(
        """
        SELECT id, received_at, signal, side
        FROM public.orders
        WHERE strategy_id = $1
          AND signal IN ('open_long', 'open_short')
          AND received_at >= $2
        ORDER BY received_at
        """,
        strategy_id,
        since_dt,
    )
    result: dict[int, dict] = {}
    for r in rows:
        bar_ms = _round_to_bar(r["received_at"], tf_ms)
        # If multiple orders land in same bar, keep first
        if bar_ms not in result:
            result[bar_ms] = dict(r)
    return result


async def cmd_replay(strategy_id: str, since_iso: str) -> None:
    from app.strategies.test_harness import TestHarnessStrategy, WARMUP_BARS, TIMEFRAME, SYMBOL

    since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=timezone.utc)
    since_ms = _dt_to_ms(since_dt)
    now_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    tf_ms    = _TIMEFRAME_MS[TIMEFRAME]

    # Fetch extra bars before since_ms for RSI warmup
    fetch_from_ms = since_ms - WARMUP_BARS * tf_ms * 2
    logger.info("replay: fetching %s %s from %s to now",
                SYMBOL, TIMEFRAME, _ms_to_dt(fetch_from_ms).isoformat())

    import ccxt.async_support as ccxt_async
    exchange_obj = ccxt_async.blofin({"enableRateLimit": True})
    candles: list[dict] = []
    seen: set[int] = set()
    since_cur = fetch_from_ms
    try:
        await exchange_obj.load_markets()
        while since_cur < now_ms:
            batch = await exchange_obj.fetch_ohlcv(
                "BTC/USDT:USDT", timeframe=TIMEFRAME, since=since_cur, limit=1000
            )
            if not batch:
                break
            new_count = 0
            for c in batch:
                ts = c[0]
                if ts in seen or ts >= now_ms:
                    continue
                seen.add(ts)
                candles.append({"t": ts, "o": c[1], "h": c[2], "l": c[3], "c": c[4], "v": c[5]})
                new_count += 1
            if new_count == 0:
                break
            since_cur = batch[-1][0] + tf_ms
            if len(batch) < 1000:
                break
            await asyncio.sleep(0.2)
    finally:
        await exchange_obj.close()

    candles.sort(key=lambda x: x["t"])
    logger.info("replay: fetched %d total candles", len(candles))

    # Run deterministic strategy to collect local entry signals after warmup
    strat = TestHarnessStrategy()
    local_entries: dict[int, str] = {}  # bar_open_ms -> signal

    for i in range(len(candles)):
        if i < WARMUP_BARS:
            strat.evaluate(candles[: i + 1])  # advance state without recording
            continue
        subset = candles[: i + 1]
        sigs = strat.evaluate(subset)
        for sig in sigs:
            if sig.signal in ("open_long", "open_short"):
                bar_ms = sig.signal_bar_time
                if bar_ms >= since_ms:
                    local_entries[bar_ms] = sig.signal

    logger.info("replay: %d local entry signals in window", len(local_entries))

    # Fetch TV ground-truth entries
    conn = await asyncpg.connect(settings.database_url)
    try:
        tv_entries = await _fetch_tv_entries(conn, strategy_id, since_ms)
    finally:
        await conn.close()

    logger.info("replay: %d tv_test entry orders in window", len(tv_entries))

    # Build comparison table over union of all bar times
    all_bars = sorted(set(local_entries.keys()) | set(tv_entries.keys()))
    rows_out: list[dict] = []
    matched = 0

    for bar_ms in all_bars:
        tv_sig  = tv_entries.get(bar_ms, {}).get("signal")
        loc_sig = local_entries.get(bar_ms)

        if tv_sig and loc_sig:
            if tv_sig == loc_sig:
                verdict = VERDICT_MATCHED
                matched += 1
            else:
                verdict = VERDICT_SIDE_MISMATCH
        elif loc_sig and not tv_sig:
            # Check for ±1-bar offset in TV entries
            offset_tv = None
            for delta in (-tf_ms, tf_ms):
                if (bar_ms + delta) in tv_entries:
                    offset_tv = tv_entries[bar_ms + delta].get("signal")
                    break
            verdict = VERDICT_BAR_OFFSET if offset_tv else VERDICT_MISSING_IN_TV
        elif tv_sig and not loc_sig:
            # Check for ±1-bar offset in local entries
            offset_loc = None
            for delta in (-tf_ms, tf_ms):
                if (bar_ms + delta) in local_entries:
                    offset_loc = local_entries[bar_ms + delta]
                    break
            verdict = VERDICT_BAR_OFFSET if offset_loc else VERDICT_EXTRA_LOCAL
        else:
            continue

        rows_out.append({"bar_ms": bar_ms, "tv_signal": tv_sig, "local_signal": loc_sig, "verdict": verdict})

    total = len(rows_out)
    _print_table(rows_out)
    print()
    print(f"Summary: {matched} matched / {total} total, {total - matched} mismatches")


async def _resolve_since(conn, strategy_id: str, since_iso: str | None):
    """Return the cutover datetime: explicit --since if given, else the first TV order for this
    strategy. Returns None if no TV orders exist yet."""
    if since_iso:
        dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    row = await conn.fetchrow(
        "SELECT min(received_at) AS t FROM public.orders WHERE strategy_id=$1", strategy_id)
    return row["t"]  # datetime or None


async def cmd_live(strategy_id: str, since_iso: str | None = None) -> None:
    """Match existing shadow_signals rows against orders (tv_test) and write verdict back."""
    tf_ms = _TIMEFRAME_MS["1h"]
    conn  = await asyncpg.connect(settings.database_url)
    try:
        since = await _resolve_since(conn, strategy_id, since_iso)
        if since is None:
            print("No TV orders for this strategy yet — nothing to compare.")
            return
        print(f"Comparing entries since cutover: {since.isoformat()}")

        shadow_rows = await conn.fetch(
            """
            SELECT id, signal, side, signal_bar_time
            FROM public.shadow_signals
            WHERE strategy_id = $1
              AND signal IN ('open_long', 'open_short')
              AND signal_bar_time >= $2
            ORDER BY signal_bar_time
            """,
            strategy_id,
            since,
        )

        rows_out: list[dict] = []
        matched = 0

        for sr in shadow_rows:
            bar_dt  = sr["signal_bar_time"]
            bar_ms  = _dt_to_ms(bar_dt)
            loc_sig = sr["signal"]

            tv_row = await conn.fetchrow(
                """
                SELECT id, signal FROM public.orders
                WHERE strategy_id = $1
                  AND signal IN ('open_long', 'open_short')
                  AND received_at >= $2
                  AND received_at <  $3
                LIMIT 1
                """,
                strategy_id,
                _ms_to_dt(bar_ms),
                _ms_to_dt(bar_ms + tf_ms),
            )

            if tv_row and tv_row["signal"] == loc_sig:
                verdict   = VERDICT_MATCHED
                order_id  = tv_row["id"]
                diff_note = None
                matched  += 1
            elif tv_row:
                verdict   = VERDICT_SIDE_MISMATCH
                order_id  = tv_row["id"]
                diff_note = f"tv={tv_row['signal']} local={loc_sig}"
            else:
                verdict   = VERDICT_MISSING_IN_TV
                order_id  = None
                diff_note = "no matching tv_test order in this 1h bar"

            await conn.execute(
                """
                UPDATE public.shadow_signals
                SET match_status     = $1,
                    matched_order_id = $2,
                    diff_notes       = $3
                WHERE id = $4
                """,
                verdict, order_id, diff_note, sr["id"],
            )

            rows_out.append({
                "bar_ms":       bar_ms,
                "tv_signal":    tv_row["signal"] if tv_row else None,
                "local_signal": loc_sig,
                "verdict":      verdict,
            })

        total = len(rows_out)
        _print_table(rows_out)
        print()
        print(f"Summary: {matched} matched / {total} total, {total - matched} mismatches")

    finally:
        await conn.close()


def _print_exit_table(rows_out: list[dict], extra_tv: list) -> None:
    header = f"{'BAR (UTC)':<22}  {'side':<14}  {'reason':<10}  {'verdict':<20}  note"
    print(header)
    print("-" * 95)
    for r in rows_out:
        bar_str = _ms_to_dt(r["bar_ms"]).strftime("%Y-%m-%d %H:%M") if r["bar_ms"] else "?"
        print(f"{bar_str:<22}  {r['side']:<14}  {r['reason']:<10}  {r['verdict']:<20}  {r.get('note', '')}")
    if extra_tv:
        print()
        print("TV-ONLY CLOSES (exits TradingView made that the engine did not):")
        print(f"  {'received_at (UTC)':<24}  signal")
        print(f"  {'-' * 40}")
        for tv in extra_tv:
            ts_str = tv["received_at"].strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {ts_str:<24}  {tv['signal']}")


async def cmd_exits(strategy_id: str, window_min: int = 15, since_iso: str | None = None) -> None:
    """Match shadow close signals against TV close orders within a +/- window. Writes verdict back."""
    window_ms = window_min * 60_000
    conn = await asyncpg.connect(settings.database_url)
    try:
        since = await _resolve_since(conn, strategy_id, since_iso)
        if since is None:
            print("No TV orders for this strategy yet — nothing to compare.")
            return
        print(f"Comparing exits since cutover: {since.isoformat()}")

        shadow_rows = await conn.fetch(
            """SELECT id, signal, signal_bar_time, exit_reason, size_pct
               FROM public.shadow_signals
               WHERE strategy_id=$1 AND signal IN ('close_long','close_short')
                 AND signal_bar_time >= $2
               ORDER BY signal_bar_time""",
            strategy_id,
            since,
        )
        tv_rows = await conn.fetch(
            """SELECT id, signal, received_at
               FROM public.orders
               WHERE strategy_id=$1 AND signal IN ('close_long','close_short')
                 AND received_at >= $2
               ORDER BY received_at""",
            strategy_id,
            since,
        )

        tv_used: set = set()
        rows_out: list[dict] = []
        matched = 0

        for sr in shadow_rows:
            s_ms   = _dt_to_ms(sr["signal_bar_time"])
            s_sig  = sr["signal"]
            reason = sr["exit_reason"] or "flip"

            # nearest unused TV close of the same side within the window
            best, best_delta = None, None
            for tv in tv_rows:
                if tv["id"] in tv_used or tv["signal"] != s_sig:
                    continue
                d = abs(_dt_to_ms(tv["received_at"]) - s_ms)
                if d <= window_ms and (best_delta is None or d < best_delta):
                    best, best_delta = tv, d

            if best:
                verdict  = VERDICT_MATCHED
                order_id = best["id"]
                note     = f"reason={reason} dt={best_delta // 1000}s"
                tv_used.add(best["id"])
                matched += 1
            else:
                opp = next(
                    (tv for tv in tv_rows
                     if tv["id"] not in tv_used and tv["signal"] != s_sig
                     and abs(_dt_to_ms(tv["received_at"]) - s_ms) <= window_ms),
                    None,
                )
                verdict  = VERDICT_SIDE_MISMATCH if opp else VERDICT_MISSING_IN_TV
                order_id = opp["id"] if opp else None
                note     = f"reason={reason} " + (
                    "opposite-side tv close" if opp
                    else f"no tv close within {window_min}m"
                )

            await conn.execute(
                """UPDATE public.shadow_signals
                   SET match_status=$1, matched_order_id=$2, diff_notes=$3 WHERE id=$4""",
                verdict, order_id, note, sr["id"],
            )
            rows_out.append({
                "bar_ms":  s_ms,
                "reason":  reason,
                "side":    s_sig,
                "verdict": verdict,
                "note":    note,
            })

        extra_tv = [tv for tv in tv_rows if tv["id"] not in tv_used]

        _print_exit_table(rows_out, extra_tv)
        print()
        print(
            f"Summary: {matched} matched / {len(shadow_rows)} shadow closes, "
            f"{len(shadow_rows) - matched} unmatched; {len(extra_tv)} tv-only closes"
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m app.diff replay <strategy_id> <since_iso>")
        print("       python -m app.diff live   <strategy_id> [--since <iso>]")
        print("       python -m app.diff exits  <strategy_id> [window_min] [--since <iso>]")
        sys.exit(1)

    cmd = sys.argv[1]
    sid = sys.argv[2]

    def _opt_since(argv: list[str]) -> str | None:
        if "--since" in argv:
            i = argv.index("--since")
            return argv[i + 1] if i + 1 < len(argv) else None
        return None

    if cmd == "replay":
        if len(sys.argv) < 4:
            print("replay requires <since_iso>")
            sys.exit(1)
        asyncio.run(cmd_replay(sid, sys.argv[3]))
    elif cmd == "live":
        asyncio.run(cmd_live(sid, _opt_since(sys.argv)))
    elif cmd == "exits":
        win = next((int(a) for a in sys.argv[3:] if a.isdigit()), 15)
        asyncio.run(cmd_exits(sid, win, _opt_since(sys.argv)))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
