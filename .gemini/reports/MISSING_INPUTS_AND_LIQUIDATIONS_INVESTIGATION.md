# Missing inputs still frequent + liquidations never populated

## Request

Following the `missing_inputs` feature, the user asked (1) why missing inputs are still
frequent on LLM calls, and (2) specifically look into liquidations — it looked like they were
never sent.

## Frequency breakdown (6h window before investigation)

```sql
SELECT unnest(missing_inputs) AS input, count(*)
FROM ai_signal_log WHERE triggered_at > now() - interval '6 hours'
GROUP BY 1 ORDER BY 2 DESC;
```

| input | count | cause |
|---|---|---|
| fear_greed | 14 | **fixed** — no caching, hit under concurrent load (below) |
| economic_calendar | 12 | **external** — Finnhub 403, needs a valid key/plan (below) |
| technical | 10 | blofin/hyperliquid REST slowness under concurrent load (partially mitigated by the earlier load_markets/timeout fix; residual) |
| funding_rate | 10 | same as technical |
| volume_profile | 7 | downstream of `technical` (needs OHLCV, see node_ingest.py:125) |
| volatility_regime | 6 | downstream of `technical` |
| mtf_structure | 4 | own OHLCV fetches, same REST slowness |
| momentum_divergence | 4 | downstream of `technical` |
| liquidations | 3 | **fixed** — collector status bug (below) |
| geometry | 3 | downstream of `technical` |
| open_interest | 3 | multi-venue fan-out, occasional per-venue REST failures |
| news | 2 | CoinGecko transient errors |
| funding_history | 1 | blofin REST slowness |

Two items fixed this session, one flagged as needing your action (external), one flagged as
still open (needs a judgment call on scope).

## 1. Liquidations — fixed (collector.py)

**Root cause**: `_stream_task()` in `app/collector.py` only wrote `state: 'connected'` to Redis
*after* `ex.watch_liquidations(venue_symbol)` returned — i.e., after the first actual
liquidation event arrived on that symbol's stream. Binance's liquidation stream only pushes a
message when a real liquidation happens; for a symbol like BNB that can be many minutes between
events. Until the first event landed, the status hash stayed unset/stale, and
`read_liquidations_window()` requires `state == 'connected'` to count a venue at all — so a
perfectly healthy, subscribed-but-quiet stream was indistinguishable from "collector never
connected" and silently dropped out of every window. Confirmed live before the fix:

```
$ docker compose exec redis redis-cli HGETALL "collector:status:binance:BNB/USDT:liquidations"
state: stopped   connected_since_ms: <stale, pre-restart>   last_error: Connection timeout
```

Meanwhile bybit and okx's liquidation streams for BNB were *also* down at the same moment —
that part is real and separate (see "still open" below) — so all three venues were failing
simultaneously for different reasons, and `missing_inputs` correctly reported `liquidations`.

**Fix**: mark `state: 'connected'` optimistically as soon as the stream task starts its loop
(before the `watch_*` await), not after the first message. An actual connection/subscribe
failure still immediately demotes it to `'reconnecting'` via the existing exception handler —
this only changes "subscribed and waiting" from looking like "never connected" to correctly
looking like "connected, no events yet", exactly matching the module's own documented intent
("a connected-but-quiet window returns real zeros — distinct from no data").

Applied uniformly to both `trades` and `liquidations` kinds (trades never showed this symptom
in practice since trade events are frequent, but the corrected semantics are the same either
way).

**Verified live** — restarted `ai-signal-generator` and confirmed all 21 liquidation streams
(7 symbols × 3 venues) log `connected` immediately on startup, including BNB across all three
venues:
```
13:13:12 collector: binance BNB/USDT liquidations connected
13:13:19 collector: bybit BNB/USDT liquidations connected
13:13:28 collector: okx BNB/USDT liquidations connected
```
And `read_liquidations_window('BNB/USDT', 4)` now returns real data instead of `None`:
```python
{'events': [...6 events...], 'venues': ['binance', 'bybit', 'okx'],
 'covered_from_ms': 1783775607579, 'ref_price': 580.2}
```

## 2. fear_greed — fixed (sentiment.py)

**Root cause**: `fetch_fear_greed()` opened a brand-new `httpx.AsyncClient` (fresh TCP+TLS
handshake) to `api.alternative.me` on *every single call*, with zero caching — and every AI
strategy's `node_ingest` calls it independently every cycle. The Fear & Greed index is a daily
figure; refetching it per-strategy per-cycle was pure waste, and under the concurrent burst of
several strategies' cycles landing close together it was intermittently failing with empty-
message connection errors (`fetch_fear_greed error: ` with no text — an `httpx` timeout/
connection-reset exception whose `str()` is empty by default). Confirmed: an isolated single
call succeeds cleanly (200 OK, ~0.4s); the failures only showed up under concurrent load.

**Fix**: process-wide cache, 600s TTL on success / 60s TTL on failure (same success/failure-TTL
split pattern already used in `signal_sources.py`'s venue-resolution cache), lock-guarded so
concurrent callers during a cache miss share the one in-flight request instead of each opening
their own connection.

**Verified live** — 10 concurrent calls: first one took 0.7s (the real fetch), the other 9 all
returned in ~0.4s each with the identical cached value — confirming only one actual network
call happened for the burst.

## 3. economic_calendar — external, needs your action (not fixed)

`fetch_economic_calendar()` is dormant-by-design without a key, but `FINNHUB_API_KEY` **is**
set (40 chars). The provider itself is rejecting every request:
```
HTTP/1.1 403 Forbidden — https://finnhub.io/api/v1/calendar/economic?from=...&to=...&token=...
```
Reproduced in isolation (not a concurrency artifact — a single clean request gets 403 every
time). This is almost certainly Finnhub's free tier not including the `/calendar/economic`
endpoint (it's a known premium-only endpoint on their pricing page) — the key works for
whatever tier it's on, just not this one. No code fix is possible here; either upgrade the
Finnhub plan, swap in a different economic-calendar provider, or turn off
`use_economic_calendar` on the strategies that have it enabled if it's not worth the spend.
Flagging rather than guessing which you'd prefer.

(Same class of issue as the Google Gemini spend-cap / Groq daily-quota notes from the earlier
`missing_inputs` verification report — external account/billing limits, not application bugs.)

## 4. technical / funding_rate / mtf_structure / open_interest — still open

These are residual blofin/hyperliquid/binance REST slowness under concurrent load — the same
class of problem fixed for BNB's orderbook/CVD in the earlier `load_markets()` + timeout
investigation, but evidently not fully eliminated: 5 strategies share blofin and all trigger
on the same 1h candle-close boundary, so their `node_ingest` fetchers still cluster tightly in
time even without the old startup-burst (now removed). Also observed during this session:
bybit and okx's liquidation *and* trades websocket streams periodically die with "ping-pong
keepalive missing on time" errors, for every symbol on that venue simultaneously (they share
one multiplexed connection per venue) — consistent with the process's event loop being briefly
starved by the same concurrent-fetch bursts, missing the keepalive ping's scheduling window.

Not fixed in this pass — the clean fixes here were caching-based (load_markets, fear_greed,
liquidations status semantics); this last piece is a genuine concurrency/scheduling question
(e.g., a global per-exchange concurrency cap, or staggering strategies' candle-close triggers
by a few seconds each so they don't all land in the same instant) and deserves a decision
before I implement it rather than a unilateral architecture change.

## Follow-up: "shouldn't strategies fetch shared data once?"

The user asked why strategies don't just fetch once and share — correct instinct, and it
turned out most of the fixed/flagged sources above are **not per-symbol at all**: `fear_greed`,
`economic_calendar`, `btc_dominance`, `macro` (DXY/US10Y), and general crypto `news` are all
identical regardless of which strategy or symbol asks for them, yet every strategy's
`node_ingest` was independently refetching each one from scratch, every cycle. (Per-symbol data
— OHLCV, orderbook, funding rate, open interest, CVD, liquidations — genuinely differs per
strategy since each of the 7 AI strategies trades a different symbol, so those can't be shared
the same way; the `load_markets()`/venue-resolution caching from the earlier BNB investigation
already covers the exchange-level redundancy that *is* shareable there.)

Added `ai-signal-generator/app/data/cache.py`: a small `@ttl_cached(success_ttl, failure_ttl)`
decorator — process-wide single-slot cache, lock-guarded so concurrent callers on a miss share
one in-flight fetch instead of each firing their own. `fetch_fear_greed()` (already
hand-rolled this same pattern from the earlier fix) was refactored onto it, and it's now also
applied to:
- `fetch_btc_dominance()` / `fetch_macro()` (macro.py) — 600s / 1800s success TTL respectively
  (DXY/US10Y are daily-resolution series, so 30 minutes costs no real freshness)
- `fetch_economic_calendar()` (econ_calendar.py) — 900s success / 300s failure TTL (currently
  always hits the Finnhub 403, so this also cuts how often a broken key gets hammered)
- `fetch_news()` (news.py) — 600s. Deliberately cached at this inner layer rather than on
  `fetch_news_digest()`, because two different callers pass different `lookback_hours` labels
  for the same underlying data (node_ingest uses 24h, event_watcher's high-impact check uses
  1h) — caching one layer down keeps each caller's label accurate while still sharing the
  actual HTTP/RSS work.

**Verified live**: fired 6 concurrent calls at each of the four newly-cached fetchers.
`btc_dominance`/`macro`/`news` each showed one real-latency call (0.3–2.1s) followed by five
near-instant (~0.0s) cache hits; `econ_calendar` showed one real 403 followed by five cached
403s (instead of six independent hits against an already-broken key). Also confirmed
`fetch_news_digest(lookback_hours=24)` and `fetch_news_digest(lookback_hours=1)` called back to
back return their own correct label (24 and 1 respectively) with identical underlying `items`
— confirming the digest-layer decision above didn't break per-caller correctness.
