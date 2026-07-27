#!/usr/bin/env python3
"""
The band tested on the path the order ACTUALLY took (amends included), for the
ETH buy order 86ee9b20 — the only stretch where the amend prices still exist
in the container logs (they are not stored anywhere else).

For each amend interval: would a resting buy at `price + band x (price - stop)`
have been touched before the next amend replaced it?
"""
import csv, datetime as dt
from pathlib import Path

D = Path(__file__).parent

# (utc time, price, sl) for the BUY order 86ee9b20, from order-listener logs.
AMENDS = [
    ("2026-07-26 09:01:21", 1869.560, 1863.95),   # original placement
    ("2026-07-26 10:01:18", 1876.436, 1871.40),
    ("2026-07-26 11:00:55", 1877.156, 1871.70),
    ("2026-07-26 12:01:16", 1877.877, 1872.50),
    ("2026-07-26 13:01:28", 1878.597, 1873.13),
    ("2026-07-26 17:01:56", 1886.526, 1878.60),
    ("2026-07-26 19:01:13", 1888.710, 1881.08),
    ("2026-07-26 22:03:41", 1891.995, 1884.21),
    ("2026-07-26 22:50:41", 1891.995, 1884.21),
    ("2026-07-27 01:20:27", 1895.277, 1884.89),
    ("2026-07-27 02:01:14", 1896.371, 1885.88),
    ("2026-07-27 03:00:55", 1897.460, 1886.50),
    ("2026-07-27 07:01:12", 1901.840, 1890.46),
]

c1m = []
with open(D / "eth_1m.csv") as f:
    for row in csv.reader(f):
        if len(row) == 5:
            c1m.append((int(row[0]), float(row[3])))   # (t, low)
c1m.sort()

NOW = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


def ms(s):
    return int(dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
               .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


print(f"{'amend at':>17} {'order px':>9} {'stop':>9} {'R':>6} "
      f"{'low until next':>15} {'gap':>7} {'gap as % of R':>14} {'40% band fills?':>16}")
print("-" * 104)

first_hit = None
for i, (ts, px, sl) in enumerate(AMENDS):
    t0 = ms(ts)
    t1 = ms(AMENDS[i + 1][0]) if i + 1 < len(AMENDS) else NOW
    lows = [l for t, l in c1m if t0 <= t < t1]
    if not lows:
        print(f"{ts[5:]:>17} {px:9.2f} {sl:9.2f} {px-sl:6.2f} {'no 1m candles':>15}")
        continue
    low = min(lows)
    gap = low - px
    r = px - sl
    band_px = px + 0.40 * r
    fills = low <= band_px
    if fills and first_hit is None:
        first_hit = (ts, band_px, low)
    print(f"{ts[5:]:>17} {px:9.2f} {sl:9.2f} {r:6.2f} {low:15.2f} "
          f"{gap:7.2f} {gap/r*100:13.0f}% {'YES' if fills else 'no':>16}")

print("-" * 104)
if first_hit:
    ts, band_px, low = first_hit
    print(f"With a 40% band the order would first have been filled in the interval "
          f"starting {ts}, at {band_px:.2f} (price traded down to {low:.2f}).")
else:
    print("A 40% band would not have filled this order at any point.")
print("\nNOTE: only the last ~2 days of amend prices still exist (container logs).")
print("Everything before 2026-07-25 20:45 is unrecoverable — nothing stores it.")
