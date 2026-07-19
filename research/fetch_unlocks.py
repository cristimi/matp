"""Fetch token cliff-unlock events (DefiLlama emissions CDN) + daily perp klines.

Offline research script — phase 3 of the edge research (unlock supply-pressure
hypothesis). The paid DefiLlama API is not needed: the per-protocol CDN payload
at defillama-datasets.llama.fi/emissions/{slug} is free and includes
`metadata.events` with pre-classified cliff unlocks (timestamp, token amount,
allocation category) plus per-label daily cumulative unlock series, from which
a circulating-supply proxy is derived to size each event.

Run: docker compose exec -T strategy-tester python - < research/fetch_unlocks.py
Outputs to /tmp/edge-data: unlock_events.csv + {SYM}_daily.csv klines.
"""
import csv
import json
import os
import time
import urllib.request

# slug -> Binance USDT-perp symbol (slugs verified against the CDN 2026-07-19)
UNIVERSE = {
    "aptos": "APTUSDT", "arbitrum": "ARBUSDT", "sui": "SUIUSDT",
    "celestia": "TIAUSDT", "dydx": "DYDXUSDT", "immutable": "IMXUSDT",
    "stepn": "GMTUSDT", "worldcoin": "WLDUSDT", "sei": "SEIUSDT",
    "starknet": "STRKUSDT", "jito": "JTOUSDT", "pyth": "PYTHUSDT",
    "jupiter": "JUPUSDT", "ethena": "ENAUSDT", "blur": "BLURUSDT",
    "axie-infinity": "AXSUSDT", "apecoin": "APEUSDT",
}
CDN = "https://defillama-datasets.llama.fi/emissions/"
OUT = "/tmp/edge-data"
NOW = time.time()


def get(url: str):
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


def circulating_series(payload: dict) -> list[tuple[int, float]]:
    """(timestamp, total unlocked across labels) per day, ascending."""
    total: dict[int, float] = {}
    for group in payload.get("documentedData", {}).get("data", []):
        for pt in group.get("data", []):
            total[pt["timestamp"]] = total.get(pt["timestamp"], 0.0) + (pt.get("unlocked") or 0.0)
    return sorted(total.items())


def circ_at(series: list[tuple[int, float]], ts: int) -> float | None:
    """Cumulative unlocked at the last daily point strictly before ts."""
    prev = None
    for t, v in series:
        if t >= ts:
            break
        prev = v
    return prev


def extract_events(slug: str, symbol: str) -> list[dict]:
    payload = get(CDN + slug)
    series = circulating_series(payload)
    if not series:
        return []
    first_live = next((t for t, v in series if v > 0), None)
    by_date: dict[str, dict] = {}
    for ev in payload.get("metadata", {}).get("events", []) or []:
        # unlockType is None on some payloads; the type then only lives in the
        # description ("A cliff of ..." / "On {timestamp} ... were unlocked"
        # vs "Linear unlock was ...").
        ut = ev.get("unlockType")
        desc = (ev.get("description") or "").lower()
        is_cliff = ut == "cliff" or (ut is None and ("cliff" in desc or desc.startswith("on {timestamp}")))
        if not is_cliff:
            continue
        ts = ev.get("timestamp")
        if not ts or ts > NOW:
            continue
        if first_live is not None and ts < first_live + 7 * 86400:
            continue  # TGE-window unlock, different phenomenon than a vesting cliff
        tokens = sum(ev.get("noOfTokens") or [])
        if tokens <= 0:
            continue
        date = time.strftime("%Y-%m-%d", time.gmtime(ts))
        d = by_date.setdefault(date, {"slug": slug, "symbol": symbol, "date": date,
                                      "ts": ts, "tokens": 0.0, "categories": set()})
        d["tokens"] += tokens
        if ev.get("category"):
            d["categories"].add(ev["category"])
    events = []
    for d in by_date.values():
        circ = circ_at(series, d["ts"])
        if not circ or circ <= 0:
            continue
        size_pct = d["tokens"] / circ * 100
        if size_pct < 0.5:
            continue  # dust cliffs — no plausible supply shock
        events.append({
            "slug": d["slug"], "symbol": d["symbol"], "date": d["date"],
            "tokens": round(d["tokens"], 2), "circ_before": round(circ, 2),
            "size_pct": round(size_pct, 3),
            "categories": "|".join(sorted(d["categories"])),
        })
    return events


def fetch_klines(sym: str) -> list[list]:
    rows, start = [], 1609459200000
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


def main():
    os.makedirs(OUT, exist_ok=True)
    all_events = []
    for slug, sym in UNIVERSE.items():
        try:
            evs = extract_events(slug, sym)
        except Exception as exc:  # noqa: BLE001
            print(f"{slug}: FAILED ({exc})")
            continue
        all_events += evs
        kpath = f"{OUT}/{sym}_daily.csv"
        if not os.path.exists(kpath):
            k = fetch_klines(sym)
            with open(kpath, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["open_time", "open", "high", "low", "close", "volume"])
                w.writerows(k)
            kn = len(k)
        else:
            kn = "cached"
        print(f"{slug} ({sym}): {len(evs)} cliff events >=0.5% circ, klines: {kn}")
    all_events.sort(key=lambda e: e["date"])
    with open(f"{OUT}/unlock_events.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["slug", "symbol", "date", "tokens",
                                          "circ_before", "size_pct", "categories"])
        w.writeheader()
        w.writerows(all_events)
    print(f"\ntotal: {len(all_events)} events -> {OUT}/unlock_events.csv")


if __name__ == "__main__":
    main()
