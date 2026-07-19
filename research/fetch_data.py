"""Fetch historical daily klines + funding rates from Binance USDT-perps.

Offline research script — NOT service code. Runs inside the strategy-tester
container (has network + pandas):
    docker compose exec -T strategy-tester python - < research/fetch_data.py
Caches CSVs to /tmp/edge-data inside the container so backtests can re-run fast.

Binance is used for its deep public history (2021+). Live deployment would be on
Hyperliquid, whose funding is hourly and can diverge from Binance — Binance here
answers "does the premium exist at all", not "what will HL pay".
"""
import csv
import json
import os
import time
import urllib.request

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
        "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "DOTUSDT", "NEARUSDT"]
START_MS = 1609459200000  # 2021-01-01
OUT = "/tmp/edge-data"


def get(url: str):
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001 — retry any transient network error
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"gave up on {url}: {last}")


def fetch_klines(sym: str) -> list[list]:
    rows, start = [], START_MS
    while True:
        data = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}"
                   f"&interval=1d&limit=1500&startTime={start}")
        if not data:
            break
        rows += [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in data]
        if len(data) < 1500:
            break
        start = data[-1][0] + 1
        time.sleep(0.3)
    return rows


def fetch_funding(sym: str) -> list[list]:
    rows, start = [], START_MS
    while True:
        data = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}"
                   f"&limit=1000&startTime={start}")
        if not data:
            break
        rows += [[r["fundingTime"], r["fundingRate"]] for r in data]
        if len(data) < 1000:
            break
        start = data[-1]["fundingTime"] + 1
        time.sleep(0.3)
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    for sym in SYMS:
        kpath = f"{OUT}/{sym}_daily.csv"
        fpath = f"{OUT}/{sym}_funding.csv"
        if os.path.exists(kpath) and os.path.exists(fpath):
            print(f"{sym}: cached")
            continue
        k = fetch_klines(sym)
        with open(kpath, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["open_time", "open", "high", "low", "close", "volume"])
            w.writerows(k)
        fr = fetch_funding(sym)
        with open(fpath, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["funding_time", "funding_rate"])
            w.writerows(fr)
        d0 = time.strftime("%Y-%m-%d", time.gmtime(k[0][0] / 1000)) if k else "-"
        d1 = time.strftime("%Y-%m-%d", time.gmtime(k[-1][0] / 1000)) if k else "-"
        print(f"{sym}: {len(k)} days ({d0}..{d1}), {len(fr)} funding points")


if __name__ == "__main__":
    main()
