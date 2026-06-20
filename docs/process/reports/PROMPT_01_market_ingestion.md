# PROMPT 01 — market-ingestion service (Blofin feed validation)

Branch: `feat/market-ingestion`  
Date: 2026-06-20  
ccxt version: **4.5.59**  
Resolved symbol mapping: **BTC-USDT → BTC/USDT:USDT** (tickSize=0.1)

---

## Phase A — Scaffold + compose wiring

### `./scripts/redeploy.sh market-ingestion` (tail)
```
 Image matp-market-ingestion Built
 Container matp-market-ingestion-1 Started
NAME                      IMAGE                   COMMAND                SERVICE            CREATED         STATUS         PORTS
matp-market-ingestion-1   matp-market-ingestion   "python -m app.main"   market-ingestion   8 seconds ago   Up 4 seconds
✓ market-ingestion redeployed.
```

### `docker compose ps market-ingestion`
```
NAME                      IMAGE                   COMMAND                SERVICE            CREATED          STATUS          PORTS
matp-market-ingestion-1   matp-market-ingestion   "python -m app.main"   market-ingestion   20 seconds ago   Up 16 seconds
```
No host port published. On `matp_net`. ✓

### Startup log (ccxt version + watchOHLCV assertion)
```
2026-06-20 16:08:39,830 INFO __main__: ccxt version: 4.5.59
2026-06-20 16:08:39,854 INFO __main__: Startup check passed: ccxt=4.5.59 exchange=blofin watchOHLCV=True
2026-06-20 16:08:40,782 INFO app.exchange: Symbol resolved: BTC-USDT -> BTC/USDT:USDT (tickSize=0.1)
2026-06-20 16:08:40,782 INFO app.exchange: Symbol resolved: BTC-USDT -> BTC/USDT:USDT (tickSize=0.1)
```

---

## Phase B — Warmup + streaming + Redis writes

### Warmup log
```
2026-06-20 16:08:40,783 INFO app.ingestor: Warmup starting: BTC-USDT 1h (500 candles)
2026-06-20 16:08:40,783 INFO app.ingestor: Warmup starting: BTC-USDT 1m (500 candles)
2026-06-20 16:08:47,693 INFO app.ingestor: Warmup done: BTC-USDT 1h — 500 closed bars written
2026-06-20 16:08:47,746 INFO app.ingestor: Warmup done: BTC-USDT 1m — 500 closed bars written
2026-06-20 16:08:48,001 INFO __main__: Starting 2 watch loop(s) for exchange=blofin
```

### `XLEN stream:candles:blofin:BTC-USDT:1h`
```
500
```

### `XREVRANGE stream:candles:blofin:BTC-USDT:1h + - COUNT 2`
```
1781971727689-0
t
1781967600000
o
63929.5
h
64359.0
l
63810.0
c
64135.5
v
240.9448
1781971727682-0
t
1781964000000
o
63360.1
h
64144.9
l
63146.2
c
63926.6
v
401.2965
```

### `GET candle:forming:blofin:BTC-USDT:1h`
```json
{"t": 1781971200000, "o": 64140.5, "h": 64145.5, "l": 63977.7, "c": 64000.9, "v": 21.8619}
```

### 1m forming→closed transition log
```
2026-06-20 16:08:51,043 INFO app.ingestor: Gap detected BTC-USDT 1m: 1781971560000->1781971680000, fetching ~1 bars
2026-06-20 16:08:51,510 INFO app.ingestor: Gap stitch complete: BTC-USDT 1m 1781971560000->1781971680000, inserted 1 bars
2026-06-20 16:09:04,029 INFO app.ingestor: Closed bar: BTC-USDT 1m t=1781971680000 o=63997.1000 h=64009.6000 l=63990.0000 c=63992.8000 v=1.2583
2026-06-20 16:10:03,067 INFO app.ingestor: Closed bar: BTC-USDT 1m t=1781971740000 o=63999.9000 h=64017.7000 l=63997.7000 c=64009.5000 v=0.1254
```
Note: a 1-bar startup gap was auto-stitched between warmup REST end and first WS event — gap-stitch working as designed.

---

## Phase C — WS-vs-REST cross-check

### `docker compose exec market-ingestion python -m app.validate xrest BTC-USDT 1h 20`
```
WS-vs-REST cross-check: BTC-USDT 1h, 20 bars
        Time (UTC)          o-ws        o-rest          c-ws        c-rest  status
-------------------------------------------------------------------------------------
  2026-06-19 20:00    63006.8000    63006.8000    63217.3000    63217.3000  MATCH
  2026-06-19 21:00    63217.3000    63217.3000    63046.9000    63046.9000  MATCH
  2026-06-19 22:00    63046.9000    63046.9000    63287.8000    63287.8000  MATCH
  2026-06-19 23:00    63294.7000    63294.7000    63512.6000    63512.6000  MATCH
  2026-06-20 00:00    63504.5000    63504.5000    63496.8000    63496.8000  MATCH
  2026-06-20 01:00    63496.9000    63496.9000    63551.0000    63551.0000  MATCH
  2026-06-20 02:00    63547.2000    63547.2000    63307.1000    63307.1000  MATCH
  2026-06-20 03:00    63307.0000    63307.0000    63440.6000    63440.6000  MATCH
  2026-06-20 04:00    63439.4000    63439.4000    63553.8000    63553.8000  MATCH
  2026-06-20 05:00    63555.7000    63555.7000    63645.5000    63645.5000  MATCH
  2026-06-20 06:00    63645.5000    63645.5000    63710.1000    63710.1000  MATCH
  2026-06-20 07:00    63708.6000    63708.6000    63624.6000    63624.6000  MATCH
  2026-06-20 08:00    63629.9000    63629.9000    63384.1000    63384.1000  MATCH
  2026-06-20 09:00    63384.1000    63384.1000    63700.4000    63700.4000  MATCH
  2026-06-20 10:00    63706.6000    63706.6000    63649.7000    63649.7000  MATCH
  2026-06-20 11:00    63649.7000    63649.7000    63652.8000    63652.8000  MATCH
  2026-06-20 12:00    63651.3000    63651.3000    63627.7000    63627.7000  MATCH
  2026-06-20 13:00    63624.8000    63624.8000    63368.1000    63368.1000  MATCH
  2026-06-20 14:00    63360.1000    63360.1000    63926.6000    63926.6000  MATCH
  2026-06-20 15:00    63929.5000    63929.5000    64135.5000    64135.5000  MATCH

Result: ALL BARS MATCH
```

---

## Phase D — Alignment output

### `validate align BTC-USDT 1h 6`
```
Alignment check: BTC-USDT 1h, last 6 closed bars
       epoch_ms                UTC time           close  aligned
------------------------------------------------------------------------
  1781949600000    2026-06-20 10:00 UTC      63649.7000  YES
  1781953200000    2026-06-20 11:00 UTC      63652.8000  YES
  1781956800000    2026-06-20 12:00 UTC      63627.7000  YES
  1781960400000    2026-06-20 13:00 UTC      63368.1000  YES
  1781964000000    2026-06-20 14:00 UTC      63926.6000  YES
  1781967600000    2026-06-20 15:00 UTC      64135.5000  YES

TV comparison: ____
```

### `validate align BTC-USDT 4h 6`
```
Alignment check: BTC-USDT 4h, last 6 closed bars
       epoch_ms                UTC time           close  aligned
------------------------------------------------------------------------
  1781884800000    2026-06-19 16:00 UTC      62998.6000  YES
  1781899200000    2026-06-19 20:00 UTC      63512.6000  YES
  1781913600000    2026-06-20 00:00 UTC      63440.6000  YES
  1781928000000    2026-06-20 04:00 UTC      63624.6000  YES
  1781942400000    2026-06-20 08:00 UTC      63652.8000  YES
  1781956800000    2026-06-20 12:00 UTC      64135.5000  YES

TV comparison: ____
```

All 1h bars land exactly on :00 minutes. All 4h bars land on 00/04/08/12/16/20:00 UTC anchors. ✓

---

## Phase E — Gap-stitch test

### Method
`validate simulate-gap 180` sets Redis key `ingestion:stall_until:blofin` for 180s.  
The ingestor checks this flag before each `watch_ohlcv` call; when active, it sleeps the full duration without writing to Redis. After waking, `watch_ohlcv` returns the current forming candle (3 bars ahead of `prev_open_time`), gap is detected, REST fills the missing bars.

### Stall + gap log
```
2026-06-20 16:30:01,186 INFO app.ingestor: Closed bar: BTC-USDT 1m t=1781972940000 o=63990.7000 h=63990.7000 l=63966.7000 c=63966.7000 v=0.0592
2026-06-20 16:30:44,166 INFO app.ingestor: Simulated stall: BTC-USDT 1m sleeping 180s
2026-06-20 16:33:44,159 INFO app.ingestor: Closed bar: BTC-USDT 1m t=1781973000000 o=63966.7000 h=63975.0000 l=63936.6000 c=63936.6000 v=8.0208
2026-06-20 16:33:44,159 INFO app.ingestor: Gap detected BTC-USDT 1m: 1781973000000->1781973180000, fetching ~2 bars
2026-06-20 16:33:44,736 INFO app.ingestor: Gap stitch complete: BTC-USDT 1m 1781973000000->1781973180000, inserted 2 bars
```

### `XREVRANGE stream:candles:blofin:BTC-USDT:1m + - COUNT 8`
```
1781973224735-0  t=1781973120000  (16:32 UTC — gap-stitched)
1781973224730-0  t=1781973060000  (16:31 UTC — gap-stitched)
1781973224158-0  t=1781973000000  (16:30 UTC — written on resume)
1781973001176-0  t=1781972940000  (16:29 UTC — last bar before stall)
1781972944157-0  t=1781972880000  (16:28 UTC)
1781972881179-0  t=1781972820000  (16:27 UTC)
1781972826156-0  t=1781972760000  (16:26 UTC)
1781972761182-0  t=1781972700000  (16:25 UTC)
```

### Timestamp diff verification (all diffs = −60000ms = 1 minute)
```
1781973240000 -> 1781973180000 diff=-60000
1781973180000 -> 1781973120000 diff=-60000
1781973120000 -> 1781973060000 diff=-60000
1781973060000 -> 1781973000000 diff=-60000
1781973000000 -> 1781972940000 diff=-60000
1781972940000 -> 1781972880000 diff=-60000
1781972880000 -> 1781972820000 diff=-60000
```

No missing bars. Contiguous across the 3-minute outage window. ✓

---

## Summary

| Item | Result |
|---|---|
| ccxt version | 4.5.59 |
| `watchOHLCV` asserted at startup | ✓ True |
| BTC-USDT ccxt symbol | `BTC/USDT:USDT` |
| tickSize | `0.1` |
| Redis keys match spec | ✓ |
| WS-vs-REST cross-check (20 bars) | ALL MATCH |
| 1h alignment (on-the-hour) | ALL YES |
| 4h alignment (4h anchors) | ALL YES |
| Gap-stitch (3-min simulated outage) | 2 bars stitched, stream contiguous |
| No credentials / no trading calls | ✓ |
| No host port published | ✓ |
| Service on `matp_net` | ✓ |

## Deviations / notes
- `INGESTION_SUBSCRIPTIONS` was expanded to `BTC-USDT:1h,BTC-USDT:4h,BTC-USDT:1m` to satisfy Phase D's `align BTC-USDT 4h` check. The 4h subscription is additive and does not affect other phases.
- A 1-bar startup gap (warmup REST → first WS event) was auto-stitched on initial boot — gap-stitch logic worked correctly in this real scenario before the explicit Phase E test.
- No DB migrations used (next free slot is 024 as noted).
