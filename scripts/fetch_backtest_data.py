"""Fetch 1m OHLCV + funding history for the social-listener backtest.

Binance USDT-M perp, because Blofin's public API only serves ~1000 bars (~3.5d).
Writes two JSON files in the shapes backtest_replay.py expects:
  ohlcv:   [{"timestamp": ms, "open":, "high":, "low":, "close":, "volume":}]
  funding: [{"t": ms, "r": rate}]

Runs inside strategy-tester — it is the only container with ccxt. Backtest
inputs are deliberately re-fetchable rather than stored: container /tmp does not
survive a --force-recreate redeploy, and the 62-day attempt lost its bars that
way. Fetching is free and takes ~2 min, so re-fetch instead of hoarding.

    docker cp scripts/fetch_backtest_data.py "$(docker compose ps -q strategy-tester)":/tmp/
    docker compose exec -T strategy-tester \
        python /tmp/fetch_backtest_data.py 14 /tmp/ohlcv_14d.json /tmp/funding_14d.json
"""
import json
import sys
import time

import ccxt

DAYS = int(sys.argv[1])
OUT_OHLCV = sys.argv[2]
OUT_FUNDING = sys.argv[3]

SYMBOL = "BTC/USDT:USDT"
ex = ccxt.binanceusdm({"enableRateLimit": True})

now_ms = ex.milliseconds()
since = now_ms - DAYS * 86400 * 1000

bars = []
cursor = since
while cursor < now_ms:
    chunk = ex.fetch_ohlcv(SYMBOL, "1m", since=cursor, limit=1500)
    if not chunk:
        break
    bars.extend(chunk)
    nxt = chunk[-1][0] + 60_000
    if nxt <= cursor:
        break
    cursor = nxt
    time.sleep(0.15)

seen = set()
ohlcv = []
for t, o, h, l, c, v in bars:
    if t in seen or t < since:
        continue
    seen.add(t)
    ohlcv.append({"timestamp": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
ohlcv.sort(key=lambda b: b["timestamp"])

funding = []
cursor = since
while cursor < now_ms:
    chunk = ex.fetch_funding_rate_history(SYMBOL, since=cursor, limit=1000)
    if not chunk:
        break
    for f in chunk:
        funding.append({"t": f["timestamp"], "r": float(f["fundingRate"])})
    nxt = chunk[-1]["timestamp"] + 1
    if nxt <= cursor:
        break
    cursor = nxt
    time.sleep(0.15)

funding = sorted({f["t"]: f for f in funding}.values(), key=lambda f: f["t"])

with open(OUT_OHLCV, "w") as f:
    json.dump(ohlcv, f)
with open(OUT_FUNDING, "w") as f:
    json.dump(funding, f)

mean_r = sum(f["r"] for f in funding) / len(funding) if funding else 0.0
print(f"ohlcv bars: {len(ohlcv)}  "
      f"{ohlcv[0]['timestamp']} -> {ohlcv[-1]['timestamp']}  "
      f"close {ohlcv[0]['close']} -> {ohlcv[-1]['close']}")
print(f"funding pts: {len(funding)}  mean {mean_r*100:.5f}%/8h ({mean_r*3*365*100:.2f}%/yr)")
