# BNB AI Scalper — "inputs missing" investigation

## Symptom

`bnb-ai-scalper-edbb` (blofin, BNB-USDT, scalper template) was repeatedly holding with
`gate_rejection_reason=hold_or_adjust`, and the LLM's `reasoning` complained on nearly every
cycle that core scalping inputs were absent from its context: `VWAP`, `depth_imbalance_ratio`,
`bid_depth_1pct_usd` / `ask_depth_1pct_usd`, `largest_bid_wall` / `largest_ask_wall`, and CVD
with adequate coverage — even though the strategy's `ai_strategy_config` row has
`use_orderbook=true` and `use_cvd=true`.

Sample (before fix), `ai_signal_log` id 1071, 2026-07-11 10:00:18Z:

```
PHASE 1 GATE FAILURE — Critical order-flow data missing. Analysis shows CVD data present
(CVD 1h: -$985,245, falling trend) but VWAP, depth_imbalance_ratio, bid_depth_1pct_usd,
ask_depth_1pct_usd, largest_bid_wall, largest_ask_wall — all core scalping inputs — are
absent from context.
```

## Root causes (two, compounding)

`ai-signal-generator/app/graph/nodes/node_ingest.py` fans out ~8-10 independent async
fetchers per cycle (OHLCV, funding rate, open interest [multi-venue], orderbook, CVD, funding
history, MTF structure...). Every one of those fetchers (`app/data/{ohlcv,funding,sentiment,
orderbook,cvd,signal_sources}.py`) independently instantiated its own `ccxt.async_support`
exchange object and called `await exchange.load_markets()` — a full REST round trip to fetch
the exchange's entire instrument catalog, repeated on every single call.

With 5 strategies on blofin and 2 on hyperliquid all scheduled on the same hourly candle-close
boundary (`app/scheduler.py::seconds_until_aligned_wake`), this produced dozens of concurrent
`load_markets()` (and other) requests to the same exchange within the same few seconds —
confirmed live:

```
$ docker compose exec ai-signal-generator python3 -c "... 8x concurrent blofin.load_markets() ..."
0 OK 5.03s   1 OK 5.09s   2 OK 9.79s ... 7 OK 10.14s
```

1. **Root cause 1 — redundant `load_markets()` storm.** Every fetcher reloaded the full
   market catalog from scratch instead of sharing it, multiplying outbound request volume
   for no reason (markets don't change intra-hour).
2. **Root cause 2 — timeout too tight for the resulting load.** ccxt's default timeout is
   10s. Under the concurrency this codebase generates, actual data calls (not just
   `load_markets()`) routinely took 10-16s to complete on blofin:
   ```
   20x concurrent blofin.fetch_ohlcv(BNB/USDT, 1h): ... up to 16.47s, none actually failed
   ```
   So even successful-but-slow responses were being killed by the client-side timeout and
   surfaced to the LLM as `None` → "missing data" — not a blofin outage, a self-inflicted
   timeout under self-inflicted concurrency.

A secondary bug made this harder to see: `node_dispatch.py::_data_sources_used()` only
listed 8 of the 18 possible source flags (missing `orderbook`, `cvd`, `mtf_structure`,
`volume_profile`, `momentum_divergence`, `volatility_regime`, `funding_history`,
`liquidations`, `limit_orders`, `economic_calendar`) — so `ai_signal_log.data_sources_used`
never showed `orderbook`/`cvd` as requested even when they were actually being fetched
(or attempted), obscuring the real picture.

## Fix

1. **Shared, cached market catalogs** (`app/data/ohlcv.py::load_markets_cached`): a
   process-wide cache keyed by `exchange_id`, 1h TTL, with an `asyncio.Lock` per exchange so
   concurrent callers share one in-flight refetch instead of firing their own. Populates
   `exchange.markets` via ccxt's own `set_markets()` (no reduced functionality — same
   internal state `load_markets()` would have built). Applied at all 9 call sites across
   `ohlcv.py`, `funding.py`, `sentiment.py` (×3), `orderbook.py`, `cvd.py` (×2),
   `signal_sources.py`.
2. **Widened ccxt timeout** from the 10s default to 25s on all 9 of those exchange
   instantiations, giving genuinely-slow-but-successful responses room to complete under the
   concurrency this ingest pipeline generates.
3. **Fixed `_data_sources_used()`** to report all 18 flags so `ai_signal_log` accurately
   reflects what was actually requested.

## Verification

Redeployed `ai-signal-generator` via `./scripts/redeploy.sh ai-signal-generator` (twice — once
per fix layer). Both redeploys restart all 7 strategies simultaneously ("startup" trigger),
which is the worst-case concurrency scenario for this bug — a stronger test than steady-state.

**After fix 1 only** (markets cache, no timeout change) — orderbook now present, CVD/OHLCV
still occasionally missing (id 1079, 10:13:00Z):
```
data_sources_used: {technical,fear_greed,funding_rate,open_interest,news,
                     economic_calendar,orderbook,cvd,funding_history,liquidations}
...
- Order book shows balanced depth imbalance: 1.197 (neutral, not "skewed hard")
...
- CVD (1h window): "not covered by snapshot" ... coverage 7.6 minutes
- No VWAP price level provided in context to anchor location
```

**After fix 2** (widened timeout, same 7-strategy simultaneous restart) — full data present,
id 1088, 10:19:58Z:
```
- Liquidity: Volume +46.0% above 20MA (strong), bid depth $186.3M / ask depth $137.6M
- Location: Price 577.67 is -5.84% below VWAP (612.5 implied)
- Imbalance: depth_imbalance_ratio 1.354 (bids heavier)
- CVD (1h): -$2.06M, CVD (4h): -$2.96M — all negative and falling
- Largest bid wall $19.78M @ 576.55, largest ask wall $8.17M @ 578.53
- RSI 57.75, MACD signal cross 2 bars ago, BB near upper band
```

The strategy still held — but now on a genuine judgment call (price far below VWAP,
depth imbalance contradicting falling CVD) instead of a missing-data punt. That's the
intended behavior for a cautious scalper gate.

## Scope note

The `app.collector: read_cvd_window error ... Timeout connecting to server / reading from
redis` warnings seen in logs are a **separate**, pre-existing issue in the CVD
stream-aggregate collector path (Redis connectivity), unrelated to the ccxt fetchers touched
here. `fetch_cvd()` already degrades gracefully past it to the klines/trades-snapshot
fallback, which is what's shown above. Not addressed in this change — flag if it recurs
after this fix lands, since it would need separate investigation into the collector service.
