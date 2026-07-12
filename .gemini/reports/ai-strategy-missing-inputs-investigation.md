# AI strategies: missing-inputs investigation

## Method

`ai_signal_log.missing_inputs` (text[]) already tracks, per cycle, which
enabled data sources came back empty (`node_dispatch.py::_missing_inputs`).
Queried this over the last 48h and cross-referenced with ai-signal-generator
source/logs.

```sql
SELECT unnest(missing_inputs) AS source, count(*) AS miss_count
FROM ai_signal_log WHERE triggered_at > now() - interval '48 hours'
GROUP BY source ORDER BY miss_count DESC;
```

```
 economic_calendar   | 161
 fear_greed          | 113
 technical           |  54
 funding_rate        |  52
 volume_profile      |  36
 open_interest       |  32
 volatility_regime   |  28
 mtf_structure       |  24
 momentum_divergence |  21
 geometry            |  15
 orderbook           |   5
 funding_history     |   5
 liquidations        |   4
 news                |   3
```
(413 total cycles in the window)

## Finding 1 — `economic_calendar`: 100% failure, always, root-caused

Direct call to the Finnhub endpoint the fetcher uses:
```
$ curl .../calendar/economic?...&token=<key>
403 {"error":"You don't have access to this resource."}
```
Not a network blip or bad key — Finnhub's economic calendar is a paid-plan
feature the current key's plan doesn't include. Confirmed via DB: every
strategy with `use_economic_calendar=true` missed it in **100%** of cycles
in the last 12h (12/12, 12/12, 12/12, 46/46 for BNB Scalper, BTC Regime
Router, ETH Geometric Range, TAO Range Rotation respectively).

**Action taken:** disabled `use_economic_calendar` on all 4 strategies via
`PUT /ai/strategies/:id/config` (which also fires the scheduler's
`/internal/schedulers/:id/reconcile` reload hook, so it took effect on the
next cycle without a service restart). Verified in DB — all 7 strategies
now show `use_economic_calendar = f`. This input was pure dead weight: an
HTTP round trip every cycle that always returned nothing.

## Finding 2 — everything else: real-but-rare failures, amplified by synchronized scheduling

Strategies wake on **candle-close-aligned schedules** (`seconds_until_aligned_wake`
in `app/scheduling.py`) — deliberate, so a strategy gets fresh candle data
right after close. But every strategy sharing an interval wakes within the
same second. Two shared resources then turn one rare blip into a mass
simultaneous outage:

1. `app/data/cache.py`'s `ttl_cached` (`success_ttl=600s`, `failure_ttl=60s`)
   is a **process-wide** cache for symbol-agnostic fetchers (fear_greed,
   economic_calendar, macro, btc_dominance, news) — one failed fetch poisons
   the shared value for every strategy for 60s.
2. `app/data/compute_executor.py`'s 4-worker thread pool is shared across
   all strategies for CPU-bound indicator/geometry computation — when 5+
   strategies fire in the same second, they contend for it, and observed
   contention stalls the event loop enough to blow 10s HTTP timeouts on
   unrelated concurrent fetches too.

Evidence: raw `fetch_fear_greed error` log lines totaled only 13 in 12h, yet
DB-tracked misses for that window were ~75% for several strategies. Exact
timestamps show why — e.g. 2026-07-12 16:01:07.929 through 16:01:08.846: **5
strategies triggered within 0.9 seconds of each other**, and all 5
simultaneously lost technical, volume_profile, momentum_divergence,
volatility_regime, mtf_structure, fear_greed, funding_rate, and
open_interest in the same cycle — one shared blip/contention event, fanned
out across nearly every strategy at once.

**Not fixed (scheduling change was declined for now)** — candle-close
alignment is intentional and changing it (e.g. adding per-strategy jitter)
changes trigger-timing behavior, so left as-is pending a separate decision.

## Finding 3 (why this took a while to diagnose) — empty error messages

`app/data/sentiment.py`'s fetchers (`fetch_fear_greed`, `fetch_funding_rate`,
`fetch_open_interest`, `_fetch_venue_oi`, `fetch_open_interest_aggregate`)
logged `str(exc)` — empty for the exact timeout-class exceptions causing
these failures (e.g. bare `asyncio.TimeoutError`/httpx timeout subtypes).
Real log output was just `fetch_fear_greed error: ` with no message.
Additionally, `node_ingest.py`'s fear_greed/funding_rate/open_interest
blocks (unlike ohlcv/mtf/orderbook/etc.) never called `logger.warning` at
all — failures there were silent except for the DB's `missing_inputs`
column.

**Fixed:**
- `ai-signal-generator/app/data/sentiment.py`: all 5 warning calls now log
  `type(exc).__name__` + `%r` (repr) alongside the message, so future
  failures show the actual exception type even when `str(exc)` is empty.
- `ai-signal-generator/app/graph/nodes/node_ingest.py`: added
  `logger.warning` calls for the fear_greed/funding_rate/open_interest
  blocks, matching every other source's pattern in that file.

## Verification

```
$ docker compose exec postgres psql -U matp -d matp -c "
  SELECT s.name, aic.use_economic_calendar FROM strategies s
  JOIN ai_strategy_config aic ON aic.strategy_id = s.id ORDER BY s.name;"
          name          | use_economic_calendar
------------------------+-----------------------
 BNB AI Scalper         | f
 BTC AI Regime router   | f
 ETH AI Geometric Range | f
 HYPE AI Mean Reversion | f
 SOL AI Trend Follower  | f
 TAO AI Range Rotation  | f
 XRP AI Breakout Hunter | f
```

Redeployed `ai-signal-generator` via `./scripts/redeploy.sh`. Startup
completed clean, all 7 schedulers restarted, health check passing:
```
$ docker compose exec nginx wget -qO- http://ai-signal-generator:8005/health
{"status":"ok","service":"ai-signal-generator","collector":{"running":true,...}}
$ docker inspect matp-ai-signal-generator-1 --format '{{.State.Health.Status}}'
healthy
```
