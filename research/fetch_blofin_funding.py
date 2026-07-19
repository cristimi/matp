"""Fetch Blofin 8h funding-rate history (the REAL second leg of the cross-venue
funding-spread trade; Binance was only the research proxy).

OKX-style pagination: `after=<ts>` returns records strictly older than ts,
newest-first, 100/page. History reaches back to ~Nov 2023. Caches per coin
(/tmp/edge-data/BLOFIN_{COIN}_funding.csv); rerun resumes.

Run: docker compose exec -T strategy-tester python - < research/fetch_blofin_funding.py
"""
import csv
import json
import os
import time
import urllib.request

OUT = "/tmp/edge-data"
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "LTC", "DOT",
         "NEAR", "BNB", "ADA", "APT", "ARB", "SUI", "TIA", "SEI", "JTO",
         "PYTH", "JUP", "APE", "BLUR", "STRK", "GMT"]
FLOOR_MS = 1688169600000  # 2023-07-01


def get(url):
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "matp-research"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"gave up on {url}: {last}")


def fetch_coin(coin: str) -> list[list]:
    rows, after = [], int(time.time() * 1000)
    while True:
        d = get("https://openapi.blofin.com/api/v1/market/funding-rate-history"
                f"?instId={coin}-USDT&limit=100&after={after}")
        data = d.get("data") or []
        if not data:
            break
        rows += [[int(r["fundingTime"]), r["fundingRate"]] for r in data]
        oldest = int(data[-1]["fundingTime"])
        if len(data) < 100 or oldest <= FLOOR_MS:
            break
        after = oldest
        time.sleep(0.12)
    rows.sort()
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    for coin in COINS:
        path = f"{OUT}/BLOFIN_{coin}_funding.csv"
        if os.path.exists(path):
            print(f"{coin}: cached")
            continue
        try:
            rows = fetch_coin(coin)
        except Exception as exc:  # noqa: BLE001
            print(f"{coin}: FAILED ({exc})")
            continue
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["funding_time", "funding_rate"])
            w.writerows(rows)
        if rows:
            d0 = time.strftime("%Y-%m-%d", time.gmtime(rows[0][0] / 1000))
            d1 = time.strftime("%Y-%m-%d", time.gmtime(rows[-1][0] / 1000))
            print(f"{coin}: {len(rows)} points ({d0}..{d1})")
        else:
            print(f"{coin}: no data (not listed on Blofin?)")


if __name__ == "__main__":
    main()
