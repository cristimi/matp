"""Fetch hourly klines for the spread-trade gates:
- HL 1h candles (candleSnapshot; API keeps only ~5000 candles ≈ 7 months) — for
  the HL-vs-Blofin basis study (gate 3).
- Blofin 1H candles over the same window (paginated, 1440/page).
- Binance 1h klines over the FULL 3y — for the margin/max-adverse-excursion
  study (gate 4; price paths are venue-agnostic).

Caches per coin/venue in /tmp/edge-data; rerun resumes.
Run: docker compose exec -T strategy-tester python - < research/fetch_basis_klines.py
"""
import csv
import json
import os
import time
import urllib.request

OUT = "/tmp/edge-data"
COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "NEAR", "APE", "GMT",
         "SEI", "TIA", "JTO", "BLUR"]
NOW_MS = int(time.time() * 1000)
HL_START = NOW_MS - 220 * 86400000       # covers HL's ~5000-candle window
BN_START = 1688169600000                 # 2023-07-01, matches funding span


def http(url, payload=None):
    last = None
    for attempt in range(5):
        try:
            if payload is None:
                req = urllib.request.Request(url, headers={"User-Agent": "matp-research"})
            else:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"gave up on {url}: {last}")


def write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "close"])
        w.writerows(rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    for coin in COINS:
        # HL hourly
        path = f"{OUT}/HL1H_{coin}.csv"
        if not os.path.exists(path):
            rows, start = [], HL_START
            while True:
                d = http("https://api.hyperliquid.xyz/info",
                         {"type": "candleSnapshot",
                          "req": {"coin": coin, "interval": "1h",
                                  "startTime": start, "endTime": NOW_MS}})
                if not d:
                    break
                rows += [[c["t"], c["c"]] for c in d]
                if len(d) < 5000:
                    break
                start = d[-1]["t"] + 1
                time.sleep(0.2)
            write(path, rows)
            print(f"HL {coin}: {len(rows)} hourly candles")
        # Blofin hourly (same window as HL availability)
        path = f"{OUT}/BLOFIN1H_{coin}.csv"
        if not os.path.exists(path):
            rows, after = [], NOW_MS
            while True:
                d = http("https://openapi.blofin.com/api/v1/market/candles"
                         f"?instId={coin}-USDT&bar=1H&limit=1440&after={after}")
                data = d.get("data") or []
                if not data:
                    break
                rows += [[int(c[0]), c[4]] for c in data]
                oldest = int(data[-1][0])
                if oldest <= HL_START:
                    break
                after = oldest
                time.sleep(0.15)
            rows.sort()
            write(path, rows)
            print(f"Blofin {coin}: {len(rows)} hourly candles")
        # Binance hourly, full span
        path = f"{OUT}/BN1H_{coin}.csv"
        if not os.path.exists(path):
            rows, start = [], BN_START
            while True:
                d = http(f"https://fapi.binance.com/fapi/v1/klines?symbol={coin}USDT"
                         f"&interval=1h&limit=1500&startTime={start}")
                if not d:
                    break
                rows += [[r[0], r[4]] for r in d]
                if len(d) < 1500:
                    break
                start = d[-1][0] + 1
                time.sleep(0.25)
            write(path, rows)
            print(f"Binance {coin}: {len(rows)} hourly candles")


if __name__ == "__main__":
    main()
