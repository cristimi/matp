# Phase 2 — Plumbing Specs for `[REQUIRES PLUMBING]` Fields

> **Design artifact — nothing here is applied.** Each entry is a build recipe for one
> `<field-id>` referenced by the Phase-1 target-state prompts (`10_*.md`–`16_*.md`).
> No code, schema, or DB change accompanies this document.

**Plumbing skeleton every entry follows** (the existing pattern, per audit `00_audit.md` §2):
`app/data/<source>.py` fetcher → called in `app/graph/nodes/node_ingest.py` under a
`strategy_config` toggle (try/except, failure appends to `data_fetch_errors`, never fatal) →
written into `AgentState` (`app/graph/state.py`) → rendered by a `_render_*` section in
`app/prompt/builder.py` → appears in the prompt.

**Exchange-isolation boundary:** all nine fields are *public market data* or *external API*
data. The existing precedent is that public market data is fetched via ccxt directly inside
`app/data/*` (`ohlcv.py`, `sentiment.py` already do this); anything scoped to the strategy's
own orders/positions goes via the **listener** (`node_ingest.py::_fetch_open_orders`), never
the executor. None of the nine fields need order/position data, so none touch the listener
or executor.

**Migration numbering (re-confirmed at spec-writing time):** `ls db/migrations` shows
`044_ai_signal_log_geometry_data.sql` as the highest-numbered file → the next free number is
**045**. All new toggles ship in a single migration, specced in §10; `economic_calendar`
needs **no** migration because the `use_economic_calendar` column already exists unwired
(audit §2.11).

**Prompt section ordering after all entries land** (extends `build_prompt()`'s current 1–9):

```
1    Header                          (existing)
2    TECHNICAL INDICATORS            (existing)
2.1  MULTI-TIMEFRAME STRUCTURE       (new — mtf_structure)
2.2  VOLATILITY REGIME               (new — volatility_regime)
2.3  MOMENTUM DIVERGENCE             (new — momentum_divergence)
2.4  VOLUME PROFILE                  (new — volume_profile_hvn_lvn)
2.5  GEOMETRIC PATTERN               (existing)
2.6  OPEN ORDERS                     (existing)
2.7  ORDER BOOK                      (new — orderbook_depth)
2.8  ORDER FLOW (CVD)                (new — cvd_delta)
2.9  LIQUIDATIONS                    (new — liquidation_data)
3    SENTIMENT                       (existing; funding_history adds lines inside it)
4    NEWS DIGEST                     (existing)
4.5  SCHEDULED EVENTS                (new — economic_calendar)
5    Macro / 6 Portfolio / 7 Data Warnings / 8 Strategy Instructions / 9 Task (existing)
```

---

## 1. `mtf_structure`

- **Consumed by:** `trend_following`, `conservative`, `breakout`, `geometric_range`.
- **Fetcher:** new `app/data/mtf.py`:
  `async def fetch_mtf_structure(exchange_id: str, symbol: str, timeframes: list[str] = ['1h', '4h', '1d']) -> list[dict] | None`
  Modeled on `ohlcv.py::fetch_ohlcv` (it *reuses* it: one `fetch_ohlcv` call per timeframe,
  then a pure local classification on each `closed_candles` set — EMA50/EMA200 posture,
  slope-based `trend_direction`, and swing-pivot `swing_structure` (HH/HL vs LH/LL vs mixed)
  reusing the swing-detection approach of `geometry.py::_find_swings`). Source: exchange
  public OHLCV via ccxt — same path as today.
  Per-TF output: `{'tf': '4h', 'trend_direction': 'uptrend|downtrend|sideways', 'ema_posture': str, 'swing_structure': 'HH/HL|LH/LL|mixed'}`.
- **Ingest wiring:** `node_ingest.py`, new block at the end of the OHLCV/indicators/geometry
  section (after the `if closed_candles:` block), gated by `sc.get('use_mtf_structure')`,
  try/except appending `f"mtf_structure:{exc}"`.
- **State:** `mtf_structure: Optional[list]` added to `AgentState` under "Ingested data".
- **Render:** new `_render_mtf_structure(state)` emitting a `MULTI-TIMEFRAME STRUCTURE:`
  section, one line per timeframe. Slot 2.1 (immediately after Technical).
- **Migration:** `use_mtf_structure boolean DEFAULT false NOT NULL` — in migration 045 (§10).
- **Effort/risk:** Medium. 3 extra OHLCV round-trips per cycle (latency + rate-limit budget);
  cache candidate if cycles are short. Interacts with ROADMAP Open Question #4 (analysis
  timeframe vs cycle interval) — fixed timeframes here actually *reduce* that inconsistency,
  but the timeframe list should become a config column if #4 lands.

## 2. `orderbook_depth`

- **Consumed by:** `scalper`, `breakout`, `range_rotation`, `geometric_range`.
- **Fetcher:** new `app/data/orderbook.py`:
  `async def fetch_orderbook_depth(exchange_id: str, symbol: str, depth_pct: tuple[float, float] = (1.0, 2.0)) -> dict | None`
  Modeled on `sentiment.py::fetch_open_interest` (same ccxt public-endpoint lifecycle:
  `_make_exchange`/`load_markets`/`resolve_ccxt_symbol`, close in `finally`). Uses ccxt
  `fetch_order_book(symbol, limit=…)`; aggregates notional within ±1%/±2% of mid and finds
  the largest single resting level per side.
  Output: `{'bid_depth_1pct_usd', 'ask_depth_1pct_usd', 'bid_depth_2pct_usd', 'ask_depth_2pct_usd', 'depth_imbalance_ratio', 'largest_bid_wall': {'price', 'size_usd'}, 'largest_ask_wall': {'price', 'size_usd'}}`.
- **Ingest wiring:** new block after the open-orders block, gated by `sc.get('use_orderbook')`,
  error key `orderbook`.
- **State:** `orderbook_data: Optional[dict]`.
- **Render:** new `_render_orderbook(state)` → `ORDER BOOK:` section, slot 2.7.
- **Migration:** `use_orderbook boolean DEFAULT false NOT NULL` — migration 045 (§10).
- **Effort/risk:** Small-medium. Snapshot-at-cycle-time only — on 4h cycles a book snapshot
  is weak evidence (walls are ephemeral/spoofable); genuinely useful on the scalper's 15m–1h
  cycles. The prompt drafts already treat it as corroboration, not primary signal.

## 3. `volume_profile_hvn_lvn`

- **Consumed by:** `mean_reversion`, `breakout`, `range_rotation`, `geometric_range`.
- **Fetcher:** new `app/data/volume_profile.py`:
  `def compute_volume_profile(candles: list[dict], num_bins: int = 50) -> dict | None`
  Pure local computation on already-fetched `closed_candles` (no external call) — modeled on
  `indicators.py::compute_indicators` (sync, takes candles, returns dict). Histogram of
  volume by price bin over the lookback window; POC = max bin; value area = 70% volume
  around POC; HVNs/LVNs = local maxima/minima of the smoothed profile.
  Output: `{'poc_price', 'value_area_high', 'value_area_low', 'hvn_levels': [prices], 'lvn_levels': [prices]}`.
- **Ingest wiring:** inside the existing `if closed_candles:` block (alongside indicators and
  geometry), gated by `sc.get('use_volume_profile')`, error key `volume_profile`.
- **State:** `volume_profile: Optional[dict]`.
- **Render:** new `_render_volume_profile(state)` → `VOLUME PROFILE (lookback window):`
  section, slot 2.4 (just before Geometry, so boundary/HVN confluence reads adjacently).
- **Migration:** `use_volume_profile boolean DEFAULT false NOT NULL` — migration 045 (§10).
- **Effort/risk:** Small. No new I/O, no new failure mode beyond math edge cases (flat
  volume, tiny candle counts → return None). Highest value-per-effort of the nine.

## 4. `cvd_delta`

- **Consumed by:** `trend_following`, `breakout`, `scalper`, `geometric_range`.
- **Fetcher:** new `app/data/cvd.py`:
  `async def fetch_cvd(exchange_id: str, symbol: str, windows_hours: tuple[int, ...] = (1, 4)) -> dict | None`
  Modeled on `sentiment.py::fetch_open_interest` for the ccxt lifecycle. Source: ccxt
  `fetch_trades` (public), classifying each trade by taker side and accumulating
  buy-minus-sell volume per window; divergence flag compares CVD slope vs price slope over
  the longest window.
  Output: `{'cvd_1h', 'cvd_4h', 'cvd_trend': 'rising|falling|flat', 'cvd_divergence': 'bullish|bearish|none'}`.
- **Ingest wiring:** new block adjacent to the orderbook block, gated by `sc.get('use_cvd')`,
  error key `cvd`.
- **State:** `cvd_data: Optional[dict]`.
- **Render:** new `_render_cvd(state)` → `ORDER FLOW (CVD):` section, slot 2.8.
- **Migration:** `use_cvd boolean DEFAULT false NOT NULL` — migration 045 (§10).
- **Effort/risk:** **Largest of the nine.** `fetch_trades` is paginated and rate-limited;
  covering 4h of trades on a busy perp can be thousands of rows per cycle. Realistic v1:
  cap at the exchange's single-call trade limit and label the window honestly (e.g. "CVD
  over last N trades"), or persist incremental deltas in Redis between cycles. Some
  exchanges also expose taker buy/sell volume directly in OHLCV extensions — check the
  specific exchange before building the trades aggregator. Known blocker: per-exchange
  variance; validate on the exchange(s) actually configured before trusting the flag.

## 5. `momentum_divergence`

- **Consumed by:** `trend_following`, `mean_reversion`, `conservative`.
- **Fetcher:** new `app/data/divergence.py`:
  `def detect_momentum_divergence(candles: list[dict], lookback: int = 60) -> dict | None`
  Pure local computation on `closed_candles` — modeled on `geometry.py::detect_geometry`
  (sync, swing-based): find price swing highs/lows (reuse the `_find_swings` approach),
  compare against RSI and MACD-histogram values at the same swings; classic
  higher-high-price / lower-high-oscillator = bearish, mirror = bullish.
  Output: `{'rsi_divergence': 'bullish|bearish|none', 'rsi_divergence_bars_since': int, 'macd_divergence': 'bullish|bearish|none', 'macd_divergence_bars_since': int}`.
- **Ingest wiring:** inside the existing `if closed_candles:` block, gated by
  `sc.get('use_momentum_divergence')`, error key `momentum_divergence`.
- **State:** `momentum_divergence: Optional[dict]`.
- **Render:** new `_render_momentum_divergence(state)` → `MOMENTUM DIVERGENCE:` section,
  slot 2.3. (Named `momentum_divergence`/`rsi_divergence` deliberately — `builder.py`
  already emits a geometry "Divergence Rate:" label; the distinct names keep grep and LLM
  reads unambiguous.)
- **Migration:** `use_momentum_divergence boolean DEFAULT false NOT NULL` — migration 045 (§10).
- **Effort/risk:** Small. No new I/O. Main risk is false positives from naive swing pairing —
  require a minimum swing separation, mirroring geometry's touch tolerances.

## 6. `volatility_regime`

- **Consumed by:** `trend_following`, `mean_reversion`, `breakout`.
- **Fetcher:** new `app/data/volatility.py`:
  `def compute_volatility_regime(candles: list[dict], percentile_window: int = 200) -> dict | None`
  Pure local computation on `closed_candles` — modeled on `indicators.py::compute_indicators`.
  ATR(14) series percentile rank of the latest value vs the trailing window; BB-width series
  percentile likewise; `squeeze_flag` = BB-width percentile below a low threshold (e.g. 15).
  Output: `{'atr_percentile': float, 'bb_width_percentile': float, 'squeeze_flag': bool}`.
- **Ingest wiring:** inside the existing `if closed_candles:` block, gated by
  `sc.get('use_volatility_regime')`, error key `volatility_regime`.
- **State:** `volatility_regime: Optional[dict]`.
- **Render:** new `_render_volatility_regime(state)` → `VOLATILITY REGIME:` section, slot 2.2.
- **Migration:** `use_volatility_regime boolean DEFAULT false NOT NULL` — migration 045 (§10).
- **Effort/risk:** Small. Needs enough closed candles for the percentile window (already
  satisfied: `fetch_ohlcv` requests ≥500); degrade to None below a floor.

## 7. `funding_history`

- **Consumed by:** `mean_reversion`, `scalper`, `conservative`, `range_rotation`.
- **Fetcher:** new `app/data/funding.py`:
  `async def fetch_funding_history(exchange_id: str, symbol: str, days: int = 30) -> dict | None`
  Modeled on `sentiment.py::fetch_funding_rate` (same ccxt lifecycle). Source: ccxt
  `fetch_funding_rate_history` (public). Computes the current rate's percentile vs the
  trailing window and the streak of consecutive same-sign settlements.
  Output: `{'funding_percentile': float, 'funding_streak': int, 'streak_direction': 'positive|negative'}`.
- **Ingest wiring:** inside the existing sentiment section of `node_ingest.py`, a fourth
  fetch gated by `sc.get('use_funding_history')`, error key `funding_history`; result stored
  as `sentiment_data['funding_history']` (it is sentiment-class data — grouping matches the
  existing `fear_greed`/`funding_rate`/`open_interest` trio).
- **State:** no new top-level key — extends the existing `sentiment_data: Optional[dict]`.
- **Render:** small helper `_render_funding_history(sd)` invoked from inside
  `_render_sentiment` (adds `Funding Percentile:` and `Funding Streak:` lines under the
  existing `Funding Rate:` line). Renders **inside** section 3, not as a new top-level
  section — the deliberate exception to the one-renderer-one-section pattern, because a
  split funding read across two sections would be worse for the LLM.
- **Migration:** `use_funding_history boolean DEFAULT false NOT NULL` — migration 045 (§10).
- **Effort/risk:** Small. `fetch_funding_rate_history` support varies slightly by exchange
  (limit caps); fall back to fewer days rather than failing.

## 8. `economic_calendar`

- **Consumed by:** `scalper`, `conservative`, `range_rotation`, `geometric_range`.
- **Fetcher:** new `app/data/econ_calendar.py`:
  `async def fetch_economic_calendar(horizon_hours: int = 48) -> dict | None`
  Modeled on `news.py::fetch_news_digest` (external HTTP API, httpx, severity-classified
  items). Source: an external economic-calendar API (e.g. Finnhub's free-tier
  `/calendar/economic`, or Trading Economics) — requires a new API-key setting in
  `app/config.py` + compose env var, same pattern as existing external keys. Filter to
  high/medium-impact US+global events (FOMC, CPI, NFP, PPI, GDP).
  Output: `{'events': [{'impact': 'high|medium', 'event_name': str, 'time_until_hours': float}], 'horizon_hours': int}`.
- **Ingest wiring:** new block directly after the news block, gated by
  `sc.get('use_economic_calendar')` — **the existing, currently-unwired column** (audit
  §2.11). Error key `economic_calendar`.
- **State:** `calendar_data: Optional[dict]`.
- **Render:** new `_render_calendar(state)` → `SCHEDULED EVENTS (next 48h):` section with
  `[IMPACT] {event_name} — in {time_until_hours}h` lines, slot 4.5 (right after News: past
  news then future events). Renders "No high-impact events in the window." when empty —
  three templates gate entries on this, so silence must be distinguishable from missing data.
- **Migration:** **none** — reuses `use_economic_calendar`.
- **Effort/risk:** Medium. External dependency + API key + provider terms; event-time
  timezone handling is the classic bug source (normalize to UTC at fetch). No schema work
  at all makes this the cheapest external-data entry.

## 9. `liquidation_data`

- **Consumed by:** `scalper` (lowest-priority field of the nine).
- **Fetcher:** new `app/data/liquidations.py`:
  `async def fetch_liquidations(exchange_id: str, symbol: str, window_hours: int = 4) -> dict | None`
  Modeled on `sentiment.py::fetch_open_interest` (ccxt lifecycle). Source: ccxt
  `fetch_liquidations` **where the exchange supports it** — support is patchy (some
  exchanges expose only their own recent force-orders, some nothing); the alternative is an
  aggregator API (Coinglass — paid). Clusters = price-binned liquidation volume near
  current price.
  Output: `{'liq_long_volume_4h', 'liq_short_volume_4h', 'liq_clusters': [{'price', 'volume_usd'}]}`.
- **Ingest wiring:** new block adjacent to orderbook/cvd, gated by `sc.get('use_liquidations')`,
  error key `liquidations`.
- **State:** `liquidation_data: Optional[dict]`.
- **Render:** new `_render_liquidations(state)` → `LIQUIDATIONS:` section, slot 2.9.
- **Migration:** `use_liquidations boolean DEFAULT false NOT NULL` — migration 045 (§10).
- **Effort/risk:** Medium build, **known blocker: source availability.** Verify ccxt
  `has['fetchLiquidations']` on the exchange(s) actually configured before building; if
  unsupported, this field waits or takes the paid-API route. Build last.

---

## 10. Migration 045 (specced, NOT applied)

One forward-only migration for all eight new toggles (economic_calendar excluded — column
exists). Pattern copied from `035_use_geometry_flag.sql` (ADD COLUMN IF NOT EXISTS,
`DEFAULT false NOT NULL` so every existing strategy is unaffected, self-verifying `DO $$`
block that RAISEs on failure):

```sql
-- Migration 045: add per-data-source toggles for target-state AI prompt fields.
-- All default FALSE — no existing strategy's prompt changes until a toggle is
-- explicitly enabled. See docs/design/ai_prompts/20_plumbing_specs.md.

BEGIN;

ALTER TABLE public.ai_strategy_config
    ADD COLUMN IF NOT EXISTS use_mtf_structure       boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_orderbook           boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_volume_profile      boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_cvd                 boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_momentum_divergence boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_volatility_regime   boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_funding_history     boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_liquidations        boolean DEFAULT false NOT NULL;

COMMIT;

-- Self-verification
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(t.col, ', ')
    INTO missing
    FROM (VALUES
        ('use_mtf_structure'), ('use_orderbook'), ('use_volume_profile'),
        ('use_cvd'), ('use_momentum_divergence'), ('use_volatility_regime'),
        ('use_funding_history'), ('use_liquidations')
    ) AS t(col)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'ai_strategy_config'
          AND column_name  = t.col
          AND column_default LIKE '%false%'
    );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 045 FAILED: missing/wrong-default columns: %', missing;
    END IF;

    RAISE NOTICE 'Migration 045 verified OK: 8 data-source toggle columns present, default=false';
END $$;
```

If field-ids are built incrementally rather than all at once, split this per-field and
re-confirm the free number each time — 045 is only guaranteed for the *first* migration
that lands.

---

## 11. Cross-cutting effort/risk notes (apply to every entry)

- **Toggle exposure:** new toggles are invisible/unsettable until
  `dashboard-api/src/routes/ai.ts` (GET/PUT of `ai_strategy_config`) and the Add/Edit
  strategy modals in `dashboard-ui/src/pages/Strategies.tsx` include them — same follow-on
  work `use_geometry` needed. Until then they are DB-settable only.
- **Tester parity:** `tester.ai_strategy_config` (26 columns on the live DB) already lacks
  `use_geometry` and `use_economic_calendar`; migration 045 widens that existing gap. The
  ROADMAP already carries a `tester.*` schema-cleanup/parity item — fold the toggle columns
  into it rather than blocking this work on it.
- **Token budget:** all nine sections enabled together add roughly 40–60 lines of context
  per cycle. Enable per-strategy per the Phase-1 consumption tables, not globally.
- **Honest-absence rendering:** every new `_render_*` must follow the geometry precedent
  (`builder.py` `_render_geometry` comments): when a toggled-on source fails, the
  `data_fetch_errors` → DATA WARNINGS path already tells the LLM the data is missing — the
  Phase-1 prompts contain explicit fallback/cap-confidence rules keyed to DATA WARNINGS, so
  renderers should return `''` on missing data rather than fabricating neutral values.

## 12. Not specced: `allocation_context`

Audit §3.3 flagged the static `PORTFOLIO CONTEXT` placeholder. A real allocation field
(live `capital_allocation`, drawdown headroom vs `allocation_peak`) is **deliberately not
specced here**: its shape depends on ROADMAP Open Questions #1 (per-strategy capital
allocation) and #2 (risk-unit sizing), both *decision pending*. Speccing it now would
pre-empt those decisions. None of the seven Phase-1 drafts reference it. Revisit once #1/#2
are decided.
