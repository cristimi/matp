"""Fetch Hyperliquid hourly funding history for the cross-venue spread study.

Caches one CSV per coin (/tmp/edge-data/HL_{COIN}_funding.csv) and skips coins
already cached, so an interrupted run resumes where it left off. HL history
starts ~2023-07; ~26k hourly points -> ~53 paginated requests per coin.

Run: docker compose exec -T strategy-tester python - < research/fetch_hl_funding.py
"""
import csv
import json
import os
import time
import urllib.request

OUT = "/tmp/edge-data"
START_MS = 1688169600000  # 2023-07-01
# coins with cached Binance funding from phases 1/4 that also trade on HL
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "LTC", "DOT",
         "NEAR", "BNB", "ADA", "APT", "ARB", "SUI", "TIA", "SEI", "JTO",
         "PYTH", "JUP", "APE", "BLUR", "STRK", "GMT"]


def post(payload: dict):
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                "https://api.hyperliquid.xyz/info",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"gave up on {payload}: {last}")


def fetch_coin(coin: str) -> list[list]:
    rows, start = [], START_MS
    while True:
        data = post({"type": "fundingHistory", "coin": coin, "startTime": start})
        if not data:
            break
        rows += [[d["time"], d["fundingRate"]] for d in data]
        if len(data) < 500:
            break
        start = data[-1]["time"] + 1
        time.sleep(0.15)
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    for coin in COINS:
        path = f"{OUT}/HL_{coin}_funding.csv"
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
            print(f"{coin}: {len(rows)} hourly points ({d0}..{d1})")
        else:
            print(f"{coin}: no data (not listed on HL?)")


if __name__ == "__main__":
    main()
