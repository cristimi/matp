# MATP Roadmap & Design Decisions

## Architecture Rules
- All exchange calls (authenticated or public) go through adapters in `order-executor`. Dashboard-api, UI, and listener are exchange-agnostic.
- AI strategies run against a **demo account** (not dry-run). Demo is preferred — gives real order feedback, fill prices, SL/TP validation, and margin mechanics.

---

## Open Design Questions

### 1. Capital Allocation per Strategy
**Decision pending.** Each strategy should have a dedicated USDC allocation it sizes against exclusively, rather than drawing from shared available balance.

- Proposed field: `capital_allocation_usdc` on the `strategies` table (shared by AI and TradingView strategies).
- Sizing should use `allocated_capital` as the base, not `available_balance` (current) or `total_balance`.
- Hard cap: total open notional for a strategy must not exceed its allocation.

**Why not `available_balance`:** available balance fluctuates with other strategies' open positions, making `size_pct` inconsistent across cycles.

### 2. Position Sizing Method
**DECIDED & IMPLEMENTED 2026-07-13** (migration 054, `node_guard._resolve_entry_sizing`).
Per-strategy `ai_strategy_config.sizing_mode`:
- `margin` (default, legacy): notional = `margin_per_trade` × leverage; $ at the stop = sl_pct × notional (observed 10-25% of allocated margin with structural stops).
- `risk`: notional = `risk_per_trade` / sl_frac, so a stop-out loses ≈ `risk_per_trade` dollars. `margin_per_trade` is reinterpreted as the hard collateral cap (notional ≤ margin × leverage — same bound the order-listener margin clamp enforces independently); when the cap binds, the shortfall is flagged in order `signal_metadata.sizing`.

Configured in the strategy edit form (Position Sizing). Prerequisite hardening shipped alongside: fill-price stop revalidation (listener) + wrong-side stop rejection for amend/adjust (guard) — a degenerate-tight stop would otherwise size a monster position.

### 4. Separate OHLCV Timeframe from Analysis Interval
**Decision pending.** Currently `cycle_interval` (scheduler sleep time) doubles as the OHLCV candle timeframe passed to `fetch_ohlcv`. This means the LLM sees different candle resolutions depending on strategy state (no position → 4h candles, position open → 15m candles), which is an inconsistent market view.

Proposed: add `ohlcv_timeframe` column to `ai_strategy_config` (fixed per strategy, standard exchange intervals only: 1m/3m/5m/15m/30m/1h/2h/4h/6h/8h/12h/1d). `node_ingest.py` reads this instead of `cycle_interval`. The three analysis intervals become pure scheduler sleep timers with no candle-timeframe side effect.

Changes required: DB migration, one line in `node_ingest.py`, `ai.ts` GET/PUT, UI Add/Edit modals, optional prompt builder update.

### 5. Multi-Strategy / Same-Symbol Coordination
**Deferred — gather data first.** Up to 3 strategies may run concurrently on the same symbol (AI scalper, AI swing, TradingView).

- Risk: unintended stacked exposure or self-hedging.
- Approach when ready: per-symbol net exposure cap in the executor. First strategy in gets full size; subsequent ones are reduced or blocked if cap is reached.
- Coordination must live in the executor, not the AI layer, because TradingView strategies bypass the AI gate.

---

## Known Issues Fixed
| Date | Issue | Fix |
|------|-------|-----|
| 2026-06-10 | OHLCV returning stale price (~2400 instead of ~1600) because `since=90d_ago` + exchange candle cap left last candle 50 days in past | Removed `since` param — exchange now returns most recent N candles |
| 2026-06-10 | `gemini-3-pro-preview` deprecated, returning 404 | Updated eth-range strategy to `gemini-3.1-pro-preview` |
| 2026-06-10 | Config-reload was a no-op; scheduler slept full interval after interval change | Added interruptible sleep + immediate cycle on config reload |
| 2026-06-10 | `volume_vs_avg_pct` always showed -70 to -99% because Binance returns the current incomplete candle as the last OHLCV entry | Fixed by computing volume average and current value from `volume.iloc[:-1]` (completed candles only) |
| 2026-06-10 | On service restart, schedulers slept a full interval before the first cycle, leaving strategies idle for hours | Added immediate startup cycle before the sleep loop in `AdaptiveScheduler._loop()` |
| 2026-06-20 | `capital_allocation` was static; drawdown used an anchor-PnL delta model (doubled Guard 5 bug) | Dynamic allocation: `capital_allocation` compounds on close, `initial_allocation` + `allocation_peak` added, Guard 5 replaced with high-water peak model |
| 2026-07-07 | `_render_task` instructed the model to output "increase" when a position shows strong continuation, but `LLMSignalOutput.action` has no `increase` member — models following the instruction failed structured-output validation (`llm_signal=None`, logged as `llm_failed`, ~5-7%/day with a position open) | Deleted the instruction line from `_render_task` (the backlog's designated small safe fix); every action the position-open task offers is now schema-valid. Scaling-in support (Literal + guard sizing + dispatch) remains unbuilt by choice |
| 2026-07-15 | Zhipu key knocked out as `auth_failed`: a transient Zhipu server 500 carried an error-id timestamp containing "…0401…", and `classify_llm_error`'s bare substring match on `'401'` classified it as an auth failure — key dead in memory, every later zhipu cycle ran keyless ("Missing credentials") until reload | Key recovered instantly via `POST /internal/llm-keys/reload` (dead state is memory-only; DB row untouched). Classifier hardened: SDK exception class names (`RateLimitError`, `AuthenticationError`, …) and `status_code` attribute checked first (5xx → never the key's fault); bare 401/403/429 in text now require standalone digits (regex, not substring). 4 regression tests added |
| 2026-07-14 | `/internal/trigger` (manual dashboard cycles) selected a hand-picked column list that silently omitted every newer `use_*` flag (orderbook, cvd, geometry, mtf, …) — manual cycles ran with those data sources disabled, so the LLM held citing "missing flow data" while scheduled cycles saw full context | Endpoint now loads `SELECT s.*, a.*` exactly like the scheduler |
| 2026-07-13 | 4 of 33 filled AI entries went live with degenerate or inverted stops: limit fills with price improvement left SL $0.09 from fill / a long born below its own SL; market slippage put a short's TP above its fill; one amend carried a TP below its own entry through the guard unchecked | Fill-price stop revalidation in order-listener (`stop_revalidation.py`, hooked into the market-fill path and the reconciler's limit-fill path — wrong-side/degenerate stops re-anchored to the fill preserving original % distances, audited in `signal_metadata.stops_reanchored_to_fill`); `node_guard` now rejects wrong-side absolute stops on `adjust_stops`/`amend_order` (`stop_wrong_side`) |
| 2026-07-12 | Recurring mass `missing_inputs` (technical + sentiment absent from LLM prompts) on candle-close wakes: all strategies wake in the same second on a single-core host; 4 GIL-heavy compute threads starved the event loop (timers 39s late, all collector websockets dropped keepalive, even local redis reads timed out) and ~50 simultaneous HTTP fetches blew the fetchers' 10s timeouts | `compute_executor` max_workers 4→1 (single core = zero real parallelism, so this only removes GIL thrash) + `warmup()` at startup (the ~14s cold `pandas_ta` import — 81-96s under startup contention — now lands at boot, not the first wake) + `node_ingest` wrapped in a 2-slot `asyncio.Semaphore` so ingest queues instead of stampeding (wake timing untouched). Live-verified: 21:00 wake had `missing_inputs = {}` for all 5 waking strategies and 0 keepalive deaths, vs mass failures at 18:00/19:00/20:00 |
| 2026-07-10 | `node_ingest.py` awaited 15+ independent external data-fetch calls one at a time (plus `fetch_mtf_structure`'s own 3 internal per-timeframe OHLCV fetches, also sequential) — a single eth-ai-34d2 cycle traced live to a 2.5-minute gap between the AI's `close_long` decision and the webhook actually reaching order-listener, entirely inside data-ingestion (the LLM call and dispatch were each under 10s). `_probe_groq`'s discovery that `groq/compound` rejects tool-calling was unrelated but found in the same investigation | Every fetch in `node_ingest.py` now starts as an `asyncio.create_task` up front and is awaited at its original call site (same per-source error handling, ~max(latency) instead of sum(latency)); `fetch_mtf_structure`'s 3 timeframes now run via `asyncio.gather`; RSS news fetch (`feedparser.parse`, no built-in timeout) now wrapped in a 10s `asyncio.wait_for`. Live-verified: trigger-to-LLM-decision dropped from ~120s to ~93s on a same-symbol retest (further gains likely masked by an unrelated concurrent 145-model startup probe sweep competing for the same event loop during testing) |

---

## Deferred Backlog
- **`config.max_order_size_btc` / `max_order_size_eth` look dead — investigate dropping**: these two
  rows exist in the `config` table (seeded by `db/init.sql`, last touched 2026-05-18) but a full-repo
  grep for `max_order_size` (2026-07-08) found zero reads in any service — not order-listener,
  order-generator, order-executor, or dashboard-api. No UI ever exposed them either (Settings page
  doesn't show them). Confirm nothing reads them at runtime (check for dynamic/string-built column
  access too, not just literal grep), then drop the rows (and the `config` table if `active_platform`
  is also removed per the Settings-page rework below) via a migration.
- **`active_platform` config value also looks dead**: the 2026-07-08 Settings rework dropped its
  toggle from the Settings page, but it's still editable from `Dashboard.tsx`/`PlatformSelector.tsx`
  — orphaned there too now that Tree replaced Dashboard as the landing page and nothing links to
  `/dashboard` anymore. The toggle predates the multi-account model (strategies now carry their own
  `account_id`, and `order-generator/app/strategies/base.py`'s `platform` field is explicitly
  commented `"legacy, kept for compat"`). Its read/write endpoints (`order-listener/app/config_api.py`)
  are still live but a grep found no other service consulting it for actual webhook routing. Verify, then
  remove the endpoints and the config row together with the `max_order_size_*` cleanup above.
- **`economic_calendar` blocked on provider access — Finnhub calendar is paid-tier**: verified 2026-07-07 with a valid free-tier key (`/quote` → 200, `/calendar/economic` → 403 "You don't have access to this resource"); the spec's free-tier assumption is outdated. `FINNHUB_API_KEY` is plumbed (.env → compose → container) and the fetcher degrades to None, so the SCHEDULED EVENTS section stays honestly absent. Activates with zero code changes on a Finnhub plan upgrade; free alternatives surveyed and rejected (Trading Economics free tier excludes US data, others paywalled/scraping). Wave 4 must not assume this field is available.
- **`liquidation_data` — live via the Phase-2 stream collector since 2026-07-07** (`app/collector.py` watchLiquidations → Redis; REST was unusable market-wide per the Wave-3/Stage-A probes). Data is a stream-venue aggregate labeled with its cascade-under-reporting caveat (venue stream throttling); paid Coinglass remains the true-aggregate upgrade. `use_liquidations` is still false on every strategy — only the `scalper` template consumes it and none runs it; enabling it is a deliberate decision. Refinement candidate: quiet liq streams only mark `connected` after their first event, so coverage labels under-claim on quiet venues (safe direction; a heartbeat would make them exact).
- **Minimum order value guard**: before sending to exchange, check notional value (qty × price) against known exchange minimums. Reject with `size_too_small` before hitting the exchange API.
- **AI prompt template management page**: no runtime CRUD exists for `ai_prompt_templates` — templates are seed-only (migrations 006/010, `ON CONFLICT DO NOTHING`). `GET /api/ai/templates` is read-only; there is no POST/PUT/DELETE anywhere. Build a create/edit page.
  - **Safety model — clone-to-edit, not edit-in-place.** Templates are shared: every `ai_strategy_config.template_id` points at one. Editing a base template in place silently changes behavior for all strategies referencing it (incl. live ones) and breaks backtest/live parity. Seed templates must stay immutable; user clones one into a custom template and edits that.
  - Needs `is_system` (or `created_by`) flag to distinguish/protect seeded rows; CRUD endpoints; a "N strategies use this" warning before destructive actions.
  - `ai_prompt_templates` is a single canonical table in `public` — tester reads it, has no duplicate — so no schema-sync needed.
  - Display side is **already done** (committed): the config modal shows `system_prompt` + active data sources read-only via the `TemplatePreview` component in `Strategies.tsx`.
- **Flip live-path validation**: the one-way exchange flip path (an `open_long` that nets/closes an
  existing short, with the exchange returning realized PnL in the open order's `exec_result`) is wired
  — the opposite leg is closed via `close_strategy_position(skip_exchange=True, realized_pnl=...)`,
  booked once, with the new leg flattened if the flip breaches drawdown. But it was only verified via
  SQL fixtures (Phase 1.5 C5/C6), never driven through a real exchange flip. Validate on BloFin demo:
  confirm `exec_result` actually carries the realized PnL on the flip, allocation compounds once, the
  opposite leg closes with no stale `open` row, and a breaching flip ends `enabled=false` with zero
  open legs.
- **Delta-booking on multi-order partial external reductions**: `sync_position_pnl` books allocation
  on the `pnl_realized` NULL→value transition (first attribution). Its correction branch (part 2)
  updates `pnl_realized` when an already-booked position's closing-order sum later changes, but does
  **not** book the increment into `capital_allocation`. A native SL / liquidation is a single
  full-close order so the common path is covered; staged *partial* external reductions that accumulate
  across multiple closing orders would under-book the later increments. Design: on correction, book the
  delta `(new_sum − previous pnl_realized)` via `_book_realized_pnl`.
- **HYPE historical unbooked PnL row**: a pre-existing closed position carries a non-NULL `pnl_realized`
  whose realized PnL was never compounded into its strategy's `capital_allocation` (predates the
  booking fix; migration 026's `0→NULL` backfill only touched 0-rows, so non-NULL unbooked rows were
  left as-is). One-off data correction: identify closed positions whose `pnl_realized` is non-NULL but
  was never reflected in `capital_allocation`/`allocation_peak`, and reconcile the allocation.
- **Persist data-fetch health into `ai_signal_log`**: today the log stores `data_sources_used`
  (derived purely from config flags), so a cycle looks identical whether fetches succeeded or
  failed. Add a nullable column (e.g. `data_fetch_errors JSONB`, and/or a boolean like
  `had_technical_data`) populated from the graph state's `data_fetch_errors` /
  `technical_indicators` in `node_dispatch`. New numbered migration (next free number, currently
  029), never edit an existing one, self-verifying `DO $$ ... RAISE EXCEPTION` block, zero-padded.
  Rationale: the 2026-06-24 exchange-resolution fix could only be proven from container logs, not
  from the DB — this closes that gap for future data-routing changes.
- **`stop_reason = 'error'` never reaches the DB**: the strategy-tree header chip supports a
  `user | drawdown | error` distinction, and migration 032 added `strategies.stop_reason`. The
  user and drawdown stops persist it (order-listener), but the order-generator error path
  (`scheduler.py` `disable()` ~line 220 + the `_run_strategy` error handler ~line 130) only
  flips an in-memory flag and logs — it never writes `enabled = false` or `stop_reason` to the
  DB. So error-disabled strategies show the generic gray "stopped" chip and may not even be
  marked disabled in the DB. To make the `'error'` chip meaningful, that path must persist
  `enabled = false, stop_reason = 'error'` to `strategies` when it disables a strategy.
- **Auto-stamp `strategy_source = 'social' | 'internal'` at creation**: the tree-filter Type
  column supports Social and Internal buckets (migration 033 formalised those values), but
  neither the social-copy pipeline nor any internal/deterministic engine creates strategy rows
  today — so those buckets stay empty until the pipelines exist. When a social-copy pipeline
  (or internal engine) gains a strategy-row creation point, set `strategy_source = 'social'`
  (or `'internal'`) at that point.
- **`tester.*` schema cleanup (parity with the `public` sweep)**: migrations 030/031 dropped
  the dead columns from `public` (`drawdown_anchor_pnl`, `win_count`, `loss_count`,
  `platform_override`, `blofin_token`, `signals_today`, `max_daily_signals`,
  `order_execution_log.avg_fill_price`, `strategy_positions.current_price`). The same columns
  still exist in the `tester.*` schema, along with the long-flagged `max_position_size`
  leftover. Do a matching cleanup migration for `tester.*`, and update the strategy-tester
  copy/migrate code that still references those columns. Deferred intentionally during the live
  sweep to keep blast radius small.
- **`tester.ai_strategy_config` toggle parity**: the tester copy (26 columns on the live DB
  as of 2026-07-06) is missing the newer data-source toggles that `public.ai_strategy_config`
  has — `use_geometry` (migration 035) and `use_economic_calendar`. Backtests of a strategy
  with geometry enabled cannot mirror its live config. Any future data-source toggles widen
  the gap (the specced-but-unapplied migration 045 in
  `docs/design/ai_prompts/20_plumbing_specs.md` would add 8 more). Fold the toggle columns
  into the `tester.*` schema-cleanup migration above rather than doing a separate pass.
- **notification-service: iOS web push**: v1 (`notification-service/`, 2026-07-04) only targets
  Android/Chrome web push. iOS Safari's web-push support (PWA installed to home screen, iOS
  16.4+) has different registration quirks; needs its own verification pass.
- **notification-service: `TelegramSink`**: the `Sink` abstract base
  (`notification-service/app/sinks/base.py`) is designed for this — add a new class +
  bot-token env var, register it in the sinks list in `consumer.py`, zero publisher changes.
- **notification-service: live/threshold PnL updates**: v1 only notifies on open/close;
  no periodic or threshold-crossing (e.g. "position down 10%") notification while a position
  is open.
- **notification-service: per-account auth-ping health**: no notification today if an
  exchange API key/secret starts failing auth (as opposed to the feed just going stale).
- **notification-service: health beyond executor+listener**: `health_watcher.py` only polls
  `order-executor` and `order-listener`. Other services (`dashboard-api`, `ai-signal-generator`,
  `strategy-tester`) have no heartbeat/health signal wired into the notification stream yet.
- **notification-service: multi-device onboarding UI**: v1's "Enable notifications" button
  (`dashboard-ui/src/pages/Settings.tsx`) registers one device with no way to see/manage/revoke
  multiple registered `push_subscriptions` rows from the UI.
- **notification-service: notification-history dashboard view**: `notification_log` is written
  on every event (audit/dedup) but nothing in `dashboard-ui` surfaces it — no way to see past
  notifications without querying Postgres directly.
- **notification-service: retention/prune job for `notification_log`**: the table has no
  TTL/archival; it grows unbounded. Needs a periodic prune (e.g. drop rows older than N days)
  once volume becomes a concern.
- **UI: order/position diagnostic trail** — investigating "why did this close/take this long"
  today means manually cross-referencing `ai_signal_log`, `orders`/`strategy_positions`, service
  container logs, and (for exchange-side events) raw Hyperliquid/Blofin API calls by hand each
  time. Surfaced twice in one session (2026-07-10): reconstructing why an ETH position's
  `close_reason` was the generic "Closed on exchange" (it was actually the AI's own tightened
  stop-loss triggering) and why a cycle took 2.5+ minutes to reach the exchange (fully serialized
  data-ingestion fetches, since fixed — see Known Issues Fixed). Want a UI-surfaced version of
  this: per-order/position, show the reconstructed lifecycle (which order/trigger actually closed
  it, SL/TP/liquidation/manual-on-exchange once that distinction is built per the close_reason
  gap below, and — for AI-driven activity — the `ai_signal_log` row(s) and per-cycle timing/data-
  fetch-error breakdown) instead of requiring a manual investigation each time.
- **close_reason: distinguish SL-hit / TP-hit / manual-on-exchange** — both exchange adapters'
  `get_closed_position_details()` (Blofin, Hyperliquid) only check a liquidation flag; every
  other externally-detected close (SL, TP, manual close on the exchange UI) collapses into the
  same generic `"Closed on exchange"` string. Distinguishing them requires cross-referencing each
  exchange's trigger-order history against the close timestamp (Hyperliquid: match the closing
  fill's `oid` against `historicalOrders`/`userFills` to read the actual `orderType` — "Stop
  Market" vs "Take Profit Market" — as done manually during the 2026-07-10 investigation; Blofin:
  equivalent lookup via its TP/SL order history endpoint). Feeds directly into the UI diagnostic
  trail item above.
### Dynamic strategy allocation (realized-PnL-compounding base)

**Status:** COMPLETE — implemented 2026-06-20 across 5 phases.

**Summary of what was built:**

- `capital_allocation` is now a **live compounding balance**: `+= realized_pnl`
  on every position close (order-listener, all three close-path UPDATEs).
- `initial_allocation` (new column) = committed capital (seed + net manual
  deposits). Never updated by PnL. Used as the `total_return` denominator.
- `allocation_peak` (new column) = high-water mark of `capital_allocation`.
  Ratchets up on winning closes; shifts by delta on deposit/withdraw; re-anchors
  to `capital_allocation` when a strategy is re-enabled after auto-disable.
- **Guard 5** (order-listener) trips when
  `capital_allocation <= allocation_peak × (1 − max_drawdown_pct/100)`,
  auto-disables the strategy, and returns 429.
- **Deposit/withdraw** via PUT `allocation_delta` (signed). All three allocation
  columns shift by the delta — capital moves are not drawdown events.
- **UI** surfaces "Allocation" (live) and "Committed" (seed) on every card.
  Edit modal shows a deposit/withdraw delta input with live preview.
- `drawdown_anchor_pnl` is fully retired from all logic (column left in schema,
  drop deferred).

**Phase 6 (2026-06-20):** the high-water drawdown stop now also fires on the
breaching close (immediate auto-disable via `_disable_if_drawdown_breached`), not
only on the next open signal. Open-time Guard 5 is retained as the backstop. Both
paths share the `_is_drawdown_breached` pure helper (single source of truth).

**Open tester-parity note:** strategy-tester backtest sizing does not yet apply
the compounding rule — backtests still use the static `capital_allocation` seed.
This divergence is accepted for now; tester parity is a separate backlog item.

**Superseded by the disable-invariant arc (Phase 1–2.1):**
- **Guard 5** (drawdown-on-open) was **removed** — drawdown auto-disable now fires on the close path
  for every close (signal, manual, native-SL/reconciler, flip), not on the open attempt.
- Allocation booking was **consolidated** out of the three `handle_webhook` close blocks into a single
  `_book_realized_pnl`, invoked on the `pnl_realized` NULL→value transition (close-time,
  `sync_position_pnl`, and `_recover_manual_close_pnl`).
- The disabled⇒flat invariant is now enforced: disabling closes open legs first (`_flatten_strategy_positions`).
