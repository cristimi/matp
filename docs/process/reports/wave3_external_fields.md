# Wave 3 Report — Final Three AI Data Fields (cvd, economic_calendar, liquidations)

**Date:** 2026-07-07
**Scope:** `ai-signal-generator` only, plus its `app/config.py`, its `docker-compose.yml`
env block (one new key), and a one-line `docs/ROADMAP.md` backlog entry — per the wave
scope guard.
**Design authority:** `docs/design/ai_prompts/20_plumbing_specs.md` §4 (cvd_delta),
§8 (economic_calendar), §9 (liquidation_data), §11 (cross-cutting rules).

Every claim below is backed by pasted command output from the live stack. All functional
verification ran **inside the running container** on real data — **no strategy toggle was
enabled at any point**.

---

## 1. Feasibility probe (verbatim — drove two of the judgment calls)

Run in-container against the two actually-configured exchanges (confirmed from
`exchange_accounts`: hyperliquid, blofin):

```
=== hyperliquid (symbol resolves to BTC/USDC:USDC) ===
  has['fetchLiquidations'] = False
  has['fetchMyLiquidations'] = False
  has['fetchTrades'] = True
  has['watchLiquidations'] = None
=== hyperliquid probe failed: ArgumentsRequired: hyperliquid fetchTrades() requires a user parameter inside 'params' or the wallet address set
=== blofin (symbol resolves to BTC/USDT:USDT) ===
  has['fetchLiquidations'] = None
  has['fetchMyLiquidations'] = None
  has['fetchTrades'] = True
  has['watchLiquidations'] = None
  fetch_trades(limit=1000) returned 100 trades
  time span: 374s (6.2 min); sides present: {'sell', 'buy'}
```

## 2. The three judgment calls (approved at the field-1 review stop)

1. **CVD fetch strategy — single-call snapshot, honestly labeled.** ONE
   `fetch_trades` call capped at 1000 trades, no pagination: a cycle can never hang on a
   busy tape and the rate-limit budget is exactly one call per cycle. The exchange decides
   the returned depth (blofin: 100 trades ≈ 4–6 min), so the specced `cvd_1h`/`cvd_4h`
   keys are `None` unless the snapshot genuinely spans that window, and the renderer
   always prints the full-snapshot delta plus a coverage line stating trades/minutes and
   that short coverage is a data limit. Rationale: the spec's own "realistic v1" —
   pagination on a busy perp is thousands of rows per cycle; Redis incremental accumulation
   is a later upgrade. Known per-exchange gap surfaced by the probe: hyperliquid's ccxt
   `fetch_trades` is user-fills-only (requires a wallet param), so CVD returns `None`
   there and the section is honestly absent on the primary exchange.
2. **Economic calendar — Finnhub, shipping dormant.** Provider: Finnhub
   `/api/v1/calendar/economic` (the spec's first suggestion; single keyed GET, fits the
   `news.py`/`macro.py` HTTP-degradation pattern). `finnhub_api_key` added to
   `app/config.py` and `FINNHUB_API_KEY: ${FINNHUB_API_KEY:-}` to the service's compose
   env. **The key is not expected now — the field ships dormant:** with the key unset the
   fetcher returns `None` immediately (no HTTP call, no crash, no fabricated events) and
   the section is absent. Setting the env var is the only activation step. Naive provider
   timestamps are normalized to UTC at parse (the spec's flagged bug source).
3. **Liquidations — documented no-op, no aggregator.** The probe shows
   `has['fetchLiquidations']` = False (hyperliquid) / None (blofin). Per the approved
   call, no paid/fragile aggregator was wired: the fetcher does a **static** ccxt
   capability check (class-level `has` map — no network call) and returns `None`;
   the renderer returns `''`. A one-line entry was appended to `docs/ROADMAP.md`
   "Deferred Backlog" recording the probe result and the revisit conditions. The renderer
   and slot 2.9 exist so a future supporting exchange only needs the aggregation body.

## 3. Fields delivered, with render slots

| Field | Fetcher | State key | Renderer | Slot |
|---|---|---|---|---|
| `cvd_delta` | `app/data/cvd.py::fetch_cvd(exchange_id, symbol, windows_hours=(1, 4))` | `cvd_data` | `_render_cvd` → `ORDER FLOW (CVD):` | **2.8** (after Order Book) |
| `liquidation_data` | `app/data/liquidations.py::fetch_liquidations(exchange_id, symbol, window_hours=4)` — documented no-op | `liquidation_data` | `_render_liquidations` → `LIQUIDATIONS:` | **2.9** (currently always absent) |
| `economic_calendar` | `app/data/econ_calendar.py::fetch_economic_calendar(horizon_hours=48)` — dormant without key | `calendar_data` | `_render_calendar` → `SCHEDULED EVENTS (next 48h):` | **4.5** (after News) |

All ingest wiring is gated by the specced toggles (`use_cvd`, `use_liquidations`,
`use_economic_calendar` — the pre-existing unwired column), try/except appending the
specced error keys to `data_fetch_errors`, never fatal.

### 3.1 Code present in the built image (grep inside the running container)

CVD (field-1 build):

```
$ docker compose exec ai-signal-generator grep -n "_render_cvd\|fetch_cvd" /app/app/prompt/builder.py /app/app/data/cvd.py /app/app/graph/nodes/node_ingest.py
/app/app/prompt/builder.py:198:def _render_cvd(state: dict) -> str:
/app/app/prompt/builder.py:587:        cv = _render_cvd(state)
/app/app/data/cvd.py:53:async def fetch_cvd(
/app/app/data/cvd.py:133:        logger.warning("fetch_cvd error [%s %s]: %s", exchange_id, symbol, exc)
/app/app/data/cvd.py:151:            result = await fetch_cvd(eid, "BTC/USDT")
/app/app/graph/nodes/node_ingest.py:6:from app.data.cvd import fetch_cvd
/app/app/graph/nodes/node_ingest.py:209:            cvd_data = await fetch_cvd(exchange_id, ccxt_symbol)
```

Calendar + liquidations (after the second rebuild — builder line numbers shifted), plus
proof the env var is plumbed into the container:

```
$ docker compose exec ai-signal-generator grep -n "_render_calendar\|fetch_economic_calendar\|_render_liquidations\|fetch_liquidations" /app/app/prompt/builder.py /app/app/data/econ_calendar.py /app/app/data/liquidations.py /app/app/graph/nodes/node_ingest.py
/app/app/prompt/builder.py:304:def _render_liquidations(state: dict) -> str:
/app/app/prompt/builder.py:329:def _render_calendar(state: dict) -> str:
/app/app/prompt/builder.py:644:        lq = _render_liquidations(state)
/app/app/prompt/builder.py:663:        cal = _render_calendar(state)
/app/app/data/econ_calendar.py:45:async def fetch_economic_calendar(horizon_hours: int = 48) -> dict | None:
/app/app/data/econ_calendar.py:96:        logger.warning("fetch_economic_calendar error: %s", exc)
/app/app/data/econ_calendar.py:106:        result = await fetch_economic_calendar()
/app/app/data/liquidations.py:30:async def fetch_liquidations(
/app/app/data/liquidations.py:37:    no configured exchange supports ccxt fetch_liquidations (see module
/app/app/data/liquidations.py:54:                "fetch_liquidations: %s has no public liquidation endpoint "
/app/app/data/liquidations.py:62:            "fetch_liquidations: %s reports fetchLiquidations support but the "
/app/app/data/liquidations.py:69:        logger.warning("fetch_liquidations error [%s %s]: %s", exchange_id, symbol, exc)
/app/app/data/liquidations.py:80:            result = await fetch_liquidations(eid, "BTC/USDT")
/app/app/graph/nodes/node_ingest.py:8:from app.data.econ_calendar import fetch_economic_calendar
/app/app/graph/nodes/node_ingest.py:12:from app.data.liquidations import fetch_liquidations
/app/app/graph/nodes/node_ingest.py:174:            calendar_data = await fetch_economic_calendar()
/app/app/graph/nodes/node_ingest.py:229:            liquidation_data = await fetch_liquidations(exchange_id, ccxt_symbol)
FINNHUB_API_KEY in env: <unset/empty>
```

### 3.2 Functional proof (in-container, real data)

CVD on blofin (real trades) and the hyperliquid gap:

```
fetch_cvd('blofin', 'BTC/USDT') -> {'cvd_1h': None, 'cvd_4h': None, 'cvd_window_usd': 712067.85, 'cvd_trend': 'rising', 'cvd_divergence': 'bullish', 'coverage_minutes': 4.0, 'trades_count': 100}
--- ORDER FLOW (CVD) section ---
ORDER FLOW (CVD):
CVD (1h window):      not covered by snapshot
CVD (4h window):      not covered by snapshot
CVD (full snapshot):  +$712,068
CVD Trend:            rising
CVD/Price Divergence: bullish
Coverage:             100 trades spanning 4.0 min (single snapshot, one API call — short coverage is a data limit, not low activity).
--- end ---

fetch_cvd('hyperliquid', 'BTC/USDT') -> None
render on hyperliquid result: repr=''  empty=True
```

Economic calendar — dormant path live (no key), plus the two distinguishable
data-present render shapes (quiet vs busy window; these are the exact structures the
fetcher emits once a key is set):

```
finnhub_api_key configured: False
fetch_economic_calendar() -> None
render (dormant, calendar_data=None): repr=''  empty=True

--- SCHEDULED EVENTS (data present, quiet window) ---
SCHEDULED EVENTS (next 48h):
No high-impact events in the window.
--- end ---
--- SCHEDULED EVENTS (data present, events) ---
SCHEDULED EVENTS (next 48h):
[HIGH] CPI (US) — in 12.5h
[MEDIUM] Initial Claims (US) — in 36.0h
--- end ---
```

Liquidations — the no-op on both real exchanges:

```
fetch_liquidations('hyperliquid', 'BTC/USDT') -> None
fetch_liquidations('blofin', 'BTC/USDT') -> None
honest absence (liquidation_data=None): repr=''  empty=True
```

### 3.3 Honest absence — all three renderers

```
honest absence (cvd_data=None): repr=''  empty=True
render (dormant, calendar_data=None): repr=''  empty=True
honest absence (liquidation_data=None): repr=''  empty=True
```

### 3.4 Deploy verification

Rebuilt + force-recreated via `./scripts/redeploy.sh ai-signal-generator` after each stage
(never `docker compose restart`). Post-deploy health, both stages:

```
$ docker compose exec nginx wget -qO- http://ai-signal-generator:8005/health
{"status":"ok","service":"ai-signal-generator"}
```

---

## 4. No migration; no toggles enabled; untouched surfaces

**No migration:** `use_cvd`/`use_liquidations` exist from migration 045;
`use_economic_calendar` pre-existed. The wave diff-stat contains no `db/migrations/` file:

```
$ git diff --stat HEAD~2..HEAD
 ai-signal-generator/app/config.py                  |   1 +
 ai-signal-generator/app/data/cvd.py                | 154 +++++++++++++++++++++
 ai-signal-generator/app/data/econ_calendar.py      | 109 +++++++++++++++
 ai-signal-generator/app/data/liquidations.py       |  83 +++++++++++
 ai-signal-generator/app/graph/nodes/node_ingest.py |  33 +++++
 ai-signal-generator/app/graph/state.py             |   3 +
 ai-signal-generator/app/prompt/builder.py          |  97 +++++++++++++
 docker-compose.yml                                 |   1 +
 docs/ROADMAP.md                                    |   1 +
 9 files changed, 482 insertions(+)
```

The only files outside `ai-signal-generator/` are the sanctioned compose-env line
(`FINNHUB_API_KEY`) and the ROADMAP backlog line. `ai_prompt_templates` was not edited;
`order-executor` / `order-listener` / `dashboard-api` / `dashboard-ui` /
`strategy-tester` untouched (none appear above). Dashboard toggle exposure and tester
parity stay deferred to their ROADMAP items.

**Final cross-check — all nine wave-delivered toggles false on every strategy:**

```
$ docker compose exec postgres psql -U matp -d matp -c "SELECT count(*) AS strategies_with_new_toggles_on FROM ai_strategy_config WHERE use_cvd OR use_liquidations OR use_economic_calendar OR use_mtf_structure OR use_orderbook OR use_volume_profile OR use_momentum_divergence OR use_volatility_regime OR use_funding_history;"
 strategies_with_new_toggles_on
--------------------------------
                              0
(1 row)
```

---

## 5. Final section ordering in `build_prompt()` — all nine slots now landed

From the deployed image:

```
$ docker compose exec ai-signal-generator grep -n "^    # [0-9]" /app/app/prompt/builder.py
586:    # 1. Header — always included; contains position warning if position_open
589:    # 2. Technical — only if toggled on and OHLCV data is available
593:    # 2.1. Multi-timeframe structure — immediately after Technical
599:    # 2.2. Volatility regime — right after Technical
605:    # 2.3. Momentum divergence
611:    # 2.4. Volume profile — just before Geometry, so boundary/HVN confluence reads adjacently
617:    # 2.5. Geometry — only if toggled on and geometry data is available
623:    # 2.6. Open orders — only if toggled on (geometry gates the range-working actions)
629:    # 2.7. Order book — only if toggled on and snapshot is available
635:    # 2.8. Order flow (CVD) — only if toggled on and snapshot is available
641:    # 2.9. Liquidations — only if toggled on and data is available
648:    # 3. Sentiment — only if at least one sentiment source is toggled on
657:    # 4. News — only if toggled on and digest is available
661:    # 4.5. Scheduled events — right after News (past news, then future events)
667:    # 5. Macro — only if at least one macro source is toggled on
673:    # 6. Portfolio — always included
676:    # 7. Data warnings — inserted between portfolio and instructions if errors occurred
682:    # 8. Strategy instructions — from DB template, always included
690:    # 9. Task section — always included
```

This completes the plumbing for all nine `[REQUIRES PLUMBING]` fields across Waves 1–3
(funding_history has no numbered slot — it renders inside section 3 by design). Everything
is inert until the Wave 4 template cutover; activation requirements beyond toggles:
`FINNHUB_API_KEY` for scheduled events, and a liquidation-capable source per the ROADMAP
entry.
