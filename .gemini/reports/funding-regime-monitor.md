# Funding-regime monitor (2026-07-19)

Implements the one actionable that survived the edge research (feat/edge-research,
phase-2 walk-forward): the delta-neutral funding-harvest premium is real but
episodic, so instead of an always-on strategy we now *watch* for the regime and
notify. No automated trading — alert only.

## What was built

- **`ai-signal-generator/app/funding_monitor.py`** — background loop (wired into
  the lifespan next to the stream collector). Hourly, for each of the 12
  research-universe coins, fetches the last 9 Binance 8h funding settlements
  (trailing 3 days) and annualizes the mean. Per-coin hysteresis: cool→hot when
  trailing > 40%/yr, hot→cool when < 20%/yr (the thresholds the walk-forward
  selected in every fold). State in Redis hash `funding_monitor:state` so
  restarts don't re-alert; transitions emit `funding.hot`/`funding.cooled` onto
  the existing `notifications:events` stream. Config via env
  (`FUNDING_MONITOR_ENABLED/SYMBOLS/ENTER_ANN/EXIT_ANN/INTERVAL_S`), defaults in
  `app/config.py`. Status endpoint: `GET /internal/funding-monitor/status`.
- **`notification-service/app/render.py`** — render + dedup entries for the two
  new event types (24h dedup window as second guard against restart spam).

Exchange note: the monitor reads public Binance funding directly from within
ai-signal-generator, which already owns public market-data fetching
(app/data/funding.py etc.) — the executor-adapter isolation rule covers the
trading path, not this data layer. Hyperliquid (hourly funding) validation
remains a research open thread.

## Verification (live)

Startup + first cycle:

```
app.funding_monitor: Funding monitor started: universe=BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,LINK,LTC,DOT,NEAR enter>40%/yr exit<20%/yr every 3600s
```

Status endpoint (via nginx container, all 12 coins computed, regime quiet as the
research predicted):

```
$ wget -qO- http://ai-signal-generator:8005/internal/funding-monitor/status
{"enabled":true,"enter_ann":0.4,"exit_ann":0.2,...,
 "coins":{"BTC":{"trailing_ann_pct":5.11,"state":"cool"},
          "ETH":{"trailing_ann_pct":3.67,"state":"cool"},
          ... "LTC":{"trailing_ann_pct":6.62,"state":"cool"},
          "DOT":{"trailing_ann_pct":-2.48,"state":"cool"}}}
```

End-to-end alert path, proven with a synthetic event for fake symbol TEST
(`redis-cli XADD notifications:events ... funding.hot TEST 0.42`):

```
   event_type    |    dedup_key     |             title             | status | device_count
 funding.hot     | funding:TEST:hot | 🔥 Funding hot: TEST 42.0%/yr | sent   |            1
```

Both services redeployed via `./scripts/redeploy.sh`; containers healthy.

Pre-existing issue noticed (not caused by this change): one push subscription
errors with "Invalid EC key" on every send — a stale/corrupt subscription row
worth pruning eventually.
