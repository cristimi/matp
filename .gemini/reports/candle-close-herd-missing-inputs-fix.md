# Candle-close herd: indicators missing from LLM prompts — root cause + fix

Recurrence of the issue investigated in
`ai-strategy-missing-inputs-investigation.md` (where the scheduling change
was declined and only logging was fixed). This session fixes the mechanism
without touching trigger timing — candle-close alignment is unchanged.

## Symptom (user report: "again the indicators are failing to be sent to llm")

Every synchronized wake (18:00, 19:00, 20:00 UTC) lost `technical`,
`volume_profile`, `volatility_regime`, `mtf_structure`, `fear_greed`,
`funding_rate`, `open_interest` across ALL waking strategies, while manual
mid-candle triggers were clean:

```
 1364 | xrp-ai-3844  | 19:00:49 | {technical,volume_profile,volatility_regime,mtf_structure,fear_greed,funding_rate,open_interest}
 1366 | ai-btc-6f8c  | 19:00:49 | {technical,geometry,volume_profile,momentum_divergence,volatility_regime,mtf_structure,funding_rate,open_interest}
 ... (all 7 strategies similar)
```

## Root cause (two layers, both on a 1-core / 2GB host — `nproc`=1)

1. **GIL starvation of the event loop.** All 7 strategies wake in the same
   second; each queues ~6 GIL-heavy pandas/pandas_ta computations into the
   shared 4-thread pool. On one core, 4 compute threads + the loop thread is
   pure GIL contention. Direct evidence at 19:00: schedulers woke **39s
   late** (19:00:49 vs ~19:00:10 target), every collector websocket (bybit +
   okx, all symbols) dropped on "ping-pong keepalive missing" at 19:01:43
   simultaneously, and `fetch_fear_greed` hit ConnectTimeout.
2. **Fetch stampede.** ~50 concurrent HTTP requests (7 strategies × 3-tf
   OHLCV + funding + 3-venue OI + orderbook…), each response JSON-parsed in
   the event loop, blow the 10s fetch timeouts. Smoking gun at 20:00 (after
   fix #1): `Timeout reading from redis:6379` — a LOCAL service — plus
   RequestTimeouts across five unrelated venues at once. Not rate limiting;
   the box can't service the burst.

## Fixes (no trigger-timing change — that option remains declined)

1. `app/data/compute_executor.py`: **max_workers 4 → 1**. With one core,
   more workers add zero parallelism and only multiply GIL thrash;
   computations now queue. Added `warmup()` (called from `main.py` lifespan)
   so the one-time `pandas_ta` import cost lands at startup — measured
   **14.7s** standalone and **81–96s** under startup contention, which
   previously would have landed inside the first candle-close wake. Warmup
   logs completion and failures (submit() otherwise swallows exceptions).
2. `app/graph/nodes/node_ingest.py`: ingest wrapped in a module-level
   **2-slot `asyncio.Semaphore`**. Wake timing untouched — strategies still
   trigger at candle close; their fetch phases queue instead of stampeding.
   Waits >1s are logged.

## Verification (live, per wake)

| Wake | State | Result |
|---|---|---|
| 19:00 | before fixes | all 7 strategies mass-missing inputs; 39s-late wakes; all websockets dropped |
| 20:00 | fix 1 only | 0 keepalive deaths; last 2 strategies in queue clean, first 5 still missing inputs (fetch stampede remained) |
| 21:00 | fixes 1+2 | **all clean** |

21:00 wake (verbatim):

```
  id  |     strategy_id     |         triggered_at          | llm_tier | missing_inputs
------+---------------------+-------------------------------+----------+----------------
 1377 | bnb-ai-scalper-edbb | 2026-07-12 21:00:10.820323+00 | scout    | {}
 1378 | ai-btc-6f8c         | 2026-07-12 21:00:15.833993+00 | scout    | {}
 1381 | hype-breakout-da2e  | 2026-07-12 21:00:25.037424+00 | premium  | {}
 1379 | xrp-ai-3844         | 2026-07-12 21:00:25.203862+00 | premium  | {}
 1380 | eth-ai-34d2         | 2026-07-12 21:00:25.207897+00 |          | {}
(5 rows)

── ingest slot waits:
node_ingest strategy=hype-breakout-da2e waited 3.3s for an ingest slot
node_ingest strategy=xrp-ai-3844 waited 10.5s for an ingest slot
node_ingest strategy=eth-ai-34d2 waited 18.6s for an ingest slot

── keepalive deaths:
0
```

Empty `missing_inputs` for every waking strategy — including `fear_greed`,
which had been missing on nearly every synchronized wake. Ingest waits
topped out at 18.6s, i.e. data still fetched well within the candle.

Deployed both steps via `./scripts/redeploy.sh ai-signal-generator`; full
test suite re-run in the disposable container: `1 failed, 64 passed` (the
failure is the known pre-existing ccxt-drift issue in `test_ohlcv`,
unrelated). ROADMAP "Known Issues Fixed" updated.

## Residual notes

- `sol-ai-6486` / `tao-ai-range-rotation-d257` were on longer intervals and
  didn't wake at 21:00 — nothing suggests they'd behave differently.
- The host is genuinely tight (1 core, 2GB, 1GB into swap, ai-signal-generator
  RSS 775MB). If more AI strategies are added, the 2-slot semaphore keeps the
  burst bounded, but tail ingest latency grows ~linearly with strategy count.
