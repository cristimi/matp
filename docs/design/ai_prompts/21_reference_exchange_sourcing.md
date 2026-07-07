# Implementation Plan v2 — Multi-Venue Sourcing for Market-Flow Fields

> **Design artifact — v2 validated 2026-07-07; build authorized starting Stage A.**
> Follow-up to `20_plumbing_specs.md`: Waves 1–3 sourced every ccxt field from the
> strategy's *execution* exchange. For market-flow signals that samples the wrong
> population. v2 goal (framing confirmed 2026-07-07): **true multi-venue aggregation**,
> built in two phases — REST-feasible wins first, then a websocket collector for the
> fields where REST cannot produce a real aggregate.

**Status:** v2 validated — Stage A in progress
**Scope if built:** `ai-signal-generator` only (Phase 2 adds a background task inside the
same service — no new compose service, no new host port) · **No migration** (§6)
**Depends on:** Waves 1–4 (all nine fields plumbed, templates cut over)

---

## 0. Confirmed decisions (supersede v1 §12)

| # | Decision | Confirmed choice |
|---|---|---|
| 1 | Aggregation model | **True multi-venue** (binance+bybit+okx), phased: REST now, websocket collector for full CVD/liquidation aggregates |
| 2 | Open interest | **Aggregate-always** — one market-wide line, own-venue suffix when available |
| 3 | Config surface | **Global `SIGNAL_VENUES` setting** (default `binance,bybit,okx`); no per-strategy column, no migration — per-symbol availability is handled by venues dropping out of the aggregate |
| 4 | Liquidations source ladder | OKX REST proxy (Phase 1, labeled) → DIY ws collector aggregate (Phase 2, labeled with throttling caveat) → Coinglass paid true aggregate (out of scope, ROADMAP) |

## 1. Problem — evidence from the live stack (Wave 3/4 verification runs)

The strategy asks "is the *market* buying?", but we sample the execution venue:

| Field | On `ai-btc-6f8c` (hyperliquid) | On `hype-breakout-da2e` (blofin) |
|---|---|---|
| `cvd_delta` | **Absent** — ccxt `fetch_trades` is user-fills-only | 100 trades ≈ **4 minutes** of a thin tape (`cvd_1h`/`cvd_4h` never covered) |
| `liquidation_data` | **No-op** — `has['fetchLiquidations']` = False | **No-op** — = None |
| `open_interest` | Works (single-venue view) | **Silently absent** — `blofin fetchOpenInterest() is not supported yet` (renders `$0.00B`) |

Prices are arbitraged across venues, so cross-venue *levels* (CVD-vs-price divergence,
liquidation clusters) transfer to the execution venue; flow volume does not need to come
from where we execute.

## 2. Field classification (unchanged design rule)

**Market-flow → multi-venue aggregate:** `cvd_delta`, `liquidation_data`,
`open_interest` (+ long/short ratio riding in the same fetcher).

**Execution-mechanics → stay venue-local, deliberately:**

| Field | Why it must not move |
|---|---|
| `orderbook_depth` | Prompt uses walls for *placement mechanics* (queue position, "don't park on a wall") — only the book where our order rests matters |
| OHLCV → technicals/geometry/volume profile/MTF/volatility/divergence | Boundary prices become limit prices on our venue; mixing candle sources across fields breaks "boundary coincides with HVN" confluence reads. Volume-profile re-sourcing **explicitly deferred** (§8) |
| `funding_rate` / `funding_history` | The funding we *pay* is our venue's (carry). A reference-funding *sentiment* line is deferred (§8) |

## 3. Shared infrastructure

### 3.1 `app/data/signal_sources.py` (both phases)

```python
# settings.signal_venues, env SIGNAL_VENUES, default "binance,bybit,okx"

async def resolve_signal_venues(symbol: str, capability: str | None = None) -> list[tuple[str, str]]
    # -> [(venue_id, venue_symbol), ...] — every configured venue that lists the
    #    symbol (resolve_ccxt_symbol fallback chain) and satisfies `capability`
    #    (static class-level ccxt `has` check, no network). Empty list is a valid
    #    result: caller degrades to execution-venue behavior, then None.
```

- **Market-catalog cache:** module-memory `{venue: set(symbols)}` with ~1h TTL — cold
  cost ≤3 `load_markets` calls per TTL, shared across strategies and fields.
- Venue that doesn't list the symbol (e.g. HYPE on binance, pending §9 probe) simply
  drops out of that symbol's aggregate — this replaces v1's per-strategy pinning column.
- Config addition: `signal_venues` in `app/config.py` + `SIGNAL_VENUES` compose env
  (same sanctioned pattern as `FINNHUB_API_KEY` in Wave 3). No DB change.

### 3.2 Phase-2 stream collector (`app/collector.py`, background task)

A single asyncio task started from the service lifespan (same process — no new compose
service, no new port), using `ccxt.pro` websocket streams:

- Subscribes per (venue, symbol) in the active strategy set: `watchTrades` +
  `watchLiquidations` (or venue-specific liquidation topics).
- Accumulates into Redis (already in the stack, currently idle for this service):
  - `cvd:{venue}:{symbol}` — per-minute taker-delta buckets, retention 24h
  - `liq:{venue}:{symbol}` — liquidation events (ts, side, price, notional), retention 24h
- Reconnect with exponential backoff; a venue's gap is *recorded* (gap markers), so
  window queries can state coverage honestly instead of silently under-counting.
- Fetchers read Redis and never wait on a socket; if the collector has <window coverage
  (fresh boot, long disconnect), output degrades to the Phase-1 method per field.
- Watchdog: collector task death is logged and restarted; health endpoint gains a
  `collector` status field (additive JSON — no consumer parses beyond `status` today).

## 4. Per-field plans

### 4.1 `open_interest` v2 — aggregate-always (Phase 1, REST)

- `fetch_open_interest_aggregate(symbol)`: `fetch_open_interest` on every venue from
  `resolve_signal_venues(symbol, 'fetchOpenInterest')`, in parallel; sum USD notional;
  24h change from `fetch_open_interest_history` where supported, volume-weighted.
- Plus one attempt on the execution venue (if it supports OI) for the suffix.
- Render (inside SENTIMENT, replacing the current line):
  `Open Interest (binance+bybit+okx): $XX.XB (+2.1% 24h)  [own venue: $X.XB]`
  — venue list printed is the list that actually responded, not the configured list.
- Partial failure: venues that error drop out and the label shrinks; zero venues → the
  existing honest-absence path. Fixes the silent blofin `$0.00B` bug as a side effect.
- Long/short ratio: taken from the first venue that supports it (binance), labeled.

### 4.2 `cvd_delta` v2 (Phase 1) / v3 (Phase 2)

**Phase 1 — Binance klines taker-volume method** (`method: klines_taker`):

- Binance USDⓈ-M klines expose `takerBuyQuoteAssetVolume` per candle → real 1h/4h CVD
  in **one API call** (~48 5m-candles). ccxt's generic `fetch_ohlcv` drops the field →
  implicit API (`fapiPublicGetKlines`), confined to one function, probed before trust
  (§9), any parse failure falls back to the Wave-3 trades-snapshot on the execution
  venue (today's behavior), then None.
- Per candle: `delta = 2 × taker_buy_quote − total_quote_volume`; `cvd_trend` from the
  cumulative-delta slope; `cvd_divergence` = CVD slope vs price slope over the longest
  window (finally meaningful, matching spec §4's intent).
- Honestly labeled: `Source: binance (klines taker-volume — largest single venue, not
  yet multi-venue)`. Binance alone is the majority of BTC perp flow, so Phase 1 already
  answers "is the market buying" far better than any execution-venue read.

**Phase 2 — true multi-venue** (`method: stream_aggregate`):

- Windows summed from the collector's per-venue Redis buckets (USD-notional deltas add
  directly); per-venue breakdown retained in the dict for the log, single aggregate line
  in the prompt: `Source: aggregate binance+bybit+okx (stream-collected)`.
- Coverage honesty: if any venue has a gap inside the window, the label lists venues
  actually covered; if aggregate coverage < window, fall back to Phase-1 method.

### 4.3 `liquidation_data` — Phase 1 DROPPED (probe outcome; deferral approved 2026-07-07)

**Stage-A probe falsified the Phase-1 REST plan:** okx `fetch_liquidations` is
`NotSupported` in the shipped ccxt (4.5.59) — the v2 doc's claim was wrong. The ccxt-wide
survey found only `bitfinex, bitmex, deribit, gate, htx` implement it, and a live 4h BTC
window returned 0 entries on four of them and 6 entries (~$6k notional) on htx — not a
usable signal. **Liquidations therefore skip Phase 1 entirely and land with the Phase-2
collector**, which the probe confirmed viable (`watchLiquidations` native on
binance+bybit, emulated on okx). The Wave-3 no-op fetcher and ROADMAP entry stand
unchanged until then.

**Phase 2 — collector aggregate:** windows/clusters computed from the Redis liquidation
events across all covered venues. **Known caveat to verify, not assert (§9 probe):**
venues throttle their public liquidation streams (Binance documents ~1 event/sec/symbol;
Bybit's newer `allLiquidation` topic claims full coverage) — a DIY aggregate
**under-reports during cascades**, the exact moments the field matters most. The render
label must carry this: `Source: aggregate (stream-collected; may under-report during
cascades — venue stream throttling).` The true-aggregate rung stays Coinglass (paid,
ROADMAP).

## 5. Renderer changes (additive only — no template edits)

Source labels per §4; all honest-absence behavior unchanged (`''`/no lines on `None`).
**No `ai_prompt_templates` change** — the cut-over prompts reference section fields, not
sources. A later template polish may exploit the labels; not required for correctness.

## 6. Config — no migration

- `signal_venues: str = "binance,bybit,okx"` in `app/config.py` + `SIGNAL_VENUES`
  compose env. That is the entire config surface. No DB column, no dashboard work, no
  tester-parity widening. (Revisit a per-strategy override only if a concrete case
  appears — recorded as a non-goal, not built speculatively.)

## 7. Live-impact statement

CVD and OI are already toggled on for both live strategies, so Phase 1 changes the
*quality* of live inputs without any toggle flip: ai-btc gains a CVD section it
currently lacks; both strategies' OI line becomes a real market-wide number instead of
single-venue (blofin: instead of a silent `$0.00B`). Liquidations stay prompt-invisible
for the live strategies (`use_liquidations` false; not in `geometric_range`'s
consumption table) — Phase 1 makes the field *available*, enabling it anywhere remains a
separate decision. Same watch-the-first-cycles discipline as the Wave-4 cutover.

## 8. Explicitly deferred (with reasons)

- **Coinglass** (paid) — the only true liquidation aggregate incl. ws-less venues;
  blocked on budget, tracked in ROADMAP.
- **Volume profile / candle re-sourcing** — entangled with geometry's level consistency
  (§2); candles for all price-derived fields move together or not at all.
- **Reference-funding sentiment line** — additive nuance, low proven value.
- **Per-strategy venue override** — superseded by aggregation (§0.3); revisit on demand.

## 9. Probe stage (no code) — pasted output decides the build details

1. Binance `fapiPublicGetKlines` returns taker-buy fields for BTCUSDT (shape check).
2. HYPE perp listing + OI support across binance/bybit/okx (drives what hype-breakout
   actually gains in each phase).
3. `okx.fetch_liquidations` returns real BTC entries over a 4h window (volume sanity vs
   Coinglass's public UI numbers).
4. OI REST support/limits per venue (`fetchOpenInterest`, `fetchOpenInterestHistory`).
5. Phase-2 pre-probe: `ccxt.pro` availability in the image (it ships with ccxt ≥4);
   watchTrades/watchLiquidations support map per venue; Bybit `allLiquidation` coverage
   claim tested against its throttled topic for one busy hour.

## 10. Build order (wave pattern, each stage ends in a push)

Every stage verified by pasted in-container output; **no live toggle changes** in any
stage; reports to `docs/process/reports/`.

- **Stage A (Phase 1a):** probes (§9) + `signal_sources.py` + aggregate OI. Exemplar —
  **push, hard stop for review.** (Chosen as exemplar: smallest blast radius, fixes a
  live bug, exercises the venue-resolution core every later stage reuses.)
- **Stage B (Phase 1b):** CVD klines method + renderer labels. (Liquidations dropped
  from Phase 1 per §4.3 probe outcome — deferral approved.) Push.
- **Stage C (Phase 1 report).** Push. **Validation checkpoint before Phase 2** — decide
  whether Phase-1 CVD quality already suffices before paying for the collector.
- **Stage D (Phase 2):** collector + Redis accumulation + CVD/liq aggregate methods +
  gap/coverage handling + health field. Push, hard stop for review.
- **Stage E (Phase 2 report).** Push last.

## 11. Risks

| Risk | Mitigation |
|---|---|
| ccxt implicit-API brittleness (`fapiPublicGetKlines` shape) | One function; probed in Stage A; falls back to trades-snapshot v1 → None |
| Venue stream throttling → cascade under-reporting (Phase 2) | Probed (§9.5); label carries the caveat; Coinglass remains the honest ceiling |
| Collector process health (Phase 2) | Watchdog restart + health field + fetcher fallback to Phase-1 methods on insufficient coverage |
| Rate limits (Phase 1 adds ~5 REST calls/cycle across venues) | Well inside public limits; venues fetched in parallel with per-venue try/except |
| HYPE listed on fewer venues | Aggregation label lists actual venues; worst case = today's behavior, honestly labeled |
| Backtest/live parity | tester consumes the same `app/data` path only when ROADMAP parity lands; flagged in each report |
| Better CVD/OI changes live decisions | Intended; called out in reports; watch first cycles as with Wave 4 |
