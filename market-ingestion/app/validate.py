"""
Validate CLI for market-ingestion.

Usage:
  python -m app.validate xrest <symbol> <timeframe> [N]
      Cross-check last N closed bars (WS stream vs REST).

  python -m app.validate align <symbol> <timeframe> [N]
      Print open-times of last N bars and verify UTC alignment.

  python -m app.validate simulate-gap [seconds]
      Set a Redis stall flag to pause ingestion writes for <seconds> (default 180).
      The ingestor will skip writing during this window; gap-stitch fires on resume.
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.config import settings
from app.exchange import make_rest_exchange, resolve_symbol


async def cmd_xrest(symbol: str, timeframe: str, n: int) -> bool:
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    rest = make_rest_exchange(settings.ingestion_exchange)
    try:
        stream_key = f"stream:candles:{settings.ingestion_exchange}:{symbol}:{timeframe}"
        entries = await r.xrevrange(stream_key, count=n)
        if not entries:
            print(f"ERROR: No bars found in stream {stream_key}", file=sys.stderr)
            return False

        entries = list(reversed(entries))
        stream_bars = [
            {
                "t": int(f["t"]),
                "o": float(f["o"]),
                "h": float(f["h"]),
                "l": float(f["l"]),
                "c": float(f["c"]),
                "v": float(f["v"]),
            }
            for _, f in entries
        ]

        await rest.load_markets()
        ccxt_sym = await resolve_symbol(rest, symbol)

        since = stream_bars[0]["t"]
        raw = await rest.fetch_ohlcv(ccxt_sym, timeframe=timeframe, since=since, limit=n + 5)
        rest_by_t = {int(c[0]): c for c in raw}

        print(f"\nWS-vs-REST cross-check: {symbol} {timeframe}, {len(stream_bars)} bars")
        print(
            f"{'Time (UTC)':>18}  {'o-ws':>12}  {'o-rest':>12}  {'c-ws':>12}  {'c-rest':>12}  status"
        )
        print("-" * 85)

        all_match = True
        tol = 1e-6

        for bar in stream_bars:
            t = bar["t"]
            dt_str = datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            rest_raw = rest_by_t.get(t)

            if rest_raw is None:
                print(f"{dt_str:>18}  --- MISSING in REST ---")
                all_match = False
                continue

            mismatches = []
            for i, field in enumerate(["o", "h", "l", "c", "v"], start=1):
                ws_v = bar[field]
                rs_v = float(rest_raw[i])
                base = max(abs(ws_v), abs(rs_v), 1e-12)
                if abs(ws_v - rs_v) / base > tol:
                    mismatches.append(f"{field}:ws={ws_v} rest={rs_v}")
                    all_match = False

            status = "MATCH" if not mismatches else "DIFF  " + ", ".join(mismatches)
            print(
                f"{dt_str:>18}  {bar['o']:>12.4f}  {float(rest_raw[1]):>12.4f}"
                f"  {bar['c']:>12.4f}  {float(rest_raw[4]):>12.4f}  {status}"
            )

        print()
        if all_match:
            print("Result: ALL BARS MATCH")
        else:
            print("Result: MISMATCHES FOUND")
        return all_match

    finally:
        await r.aclose()
        try:
            await rest.close()
        except Exception:
            pass


async def cmd_align(symbol: str, timeframe: str, n: int) -> None:
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        stream_key = f"stream:candles:{settings.ingestion_exchange}:{symbol}:{timeframe}"
        entries = await r.xrevrange(stream_key, count=n)
        if not entries:
            print(f"ERROR: No bars in {stream_key}", file=sys.stderr)
            return

        entries = list(reversed(entries))

        print(f"\nAlignment check: {symbol} {timeframe}, last {len(entries)} closed bars")
        print(f"{'epoch_ms':>15}  {'UTC time':>22}  {'close':>14}  aligned")
        print("-" * 72)

        for _, fields in entries:
            t = int(fields["t"])
            c = float(fields["c"])
            dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
            dt_str = dt.strftime("%Y-%m-%d %H:%M UTC")

            if timeframe == "1m":
                aligned = dt.second == 0
            elif timeframe == "5m":
                aligned = dt.second == 0 and dt.minute % 5 == 0
            elif timeframe == "15m":
                aligned = dt.second == 0 and dt.minute % 15 == 0
            elif timeframe == "30m":
                aligned = dt.second == 0 and dt.minute % 30 == 0
            elif timeframe == "1h":
                aligned = dt.second == 0 and dt.minute == 0
            elif timeframe == "2h":
                aligned = dt.second == 0 and dt.minute == 0 and dt.hour % 2 == 0
            elif timeframe == "4h":
                aligned = dt.second == 0 and dt.minute == 0 and dt.hour % 4 == 0
            elif timeframe == "8h":
                aligned = dt.second == 0 and dt.minute == 0 and dt.hour % 8 == 0
            elif timeframe == "1d":
                aligned = dt.second == 0 and dt.minute == 0 and dt.hour == 0
            else:
                aligned = True

            status = "YES" if aligned else "NO *** MISALIGNED ***"
            print(f"{t:>15}  {dt_str:>22}  {c:>14.4f}  {status}")

        print()
        print("TV comparison: ____")

    finally:
        await r.aclose()


async def cmd_simulate_gap(seconds: int) -> None:
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        stall_until_ms = int(time.time() * 1000) + seconds * 1000
        key = f"ingestion:stall_until:{settings.ingestion_exchange}"
        await r.set(key, str(stall_until_ms), ex=seconds + 60)
        dt = datetime.fromtimestamp(stall_until_ms / 1000, tz=timezone.utc)
        print(f"Stall flag set: exchange={settings.ingestion_exchange}")
        print(f"  Duration   : {seconds}s")
        print(f"  Stall until: {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print()
        print("The ingestor will skip writing to Redis streams during this window.")
        print("Gap-stitch will fire automatically when the stall expires.")
        print(f"Monitor: docker compose logs -f market-ingestion")
    finally:
        await r.aclose()


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]

    if cmd == "xrest":
        symbol = args[1] if len(args) > 1 else "BTC-USDT"
        timeframe = args[2] if len(args) > 2 else "1h"
        n = int(args[3]) if len(args) > 3 else 20
        ok = asyncio.run(cmd_xrest(symbol, timeframe, n))
        sys.exit(0 if ok else 1)

    elif cmd == "align":
        symbol = args[1] if len(args) > 1 else "BTC-USDT"
        timeframe = args[2] if len(args) > 2 else "1h"
        n = int(args[3]) if len(args) > 3 else 6
        asyncio.run(cmd_align(symbol, timeframe, n))

    elif cmd == "simulate-gap":
        seconds = int(args[1]) if len(args) > 1 else 180
        asyncio.run(cmd_simulate_gap(seconds))

    else:
        print(f"Unknown command: {cmd!r}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
