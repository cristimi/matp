# MATP Strategy Tester — Software Development Document

**Version:** 1.1  
**Date:** 2026-06-10  
**Status:** Design / Pre-Implementation  
**Author:** Architecture session — Claude + Cristi  
**Related repo:** https://github.com/cristimi/matp  

---

## Changelog from v1.0

This revision addresses 14 correctness and robustness issues identified in a critical review of v1.0. The major changes:

1. **Cooldown uses simulated time, not wall-clock.** `node_guard_sim` and `node_dispatch_sim` now operate on `candle_ts` injected from the engine, not `NOW()`. Without this fix every backtest produced meaningless results.
2. **New `historical_ohlcv.py` module.** The live `fetch_ohlcv` cannot fetch historical ranges (it explicitly rejects the `since` parameter). A paginating fetcher is required.
3. **`node_analyze` is copied, not shared.** Service-level pool isolation prevents reuse via import. The copy is checksum-tracked to detect drift.
4. **Explicit causality rules.** SL/TP checks happen on candles *after* position open, never on the open candle itself. The engine loop is reordered to make this enforceable.
5. **Per-candle equity snapshots.** Drawdown is computed from mark-to-market balance, not closed-trade balance only.
6. **`search_path` set via asyncpg init hook + fully-qualified writes.** Both defences applied; pool reconnects no longer risk writing to `public.*`.
7. **Strategy IDs use 12-char hex suffixes.** Collision probability negligible.
8. **Cost section widened with measurement endpoint.** `POST /api/tester/estimate-cost` returns measured token counts from the actual loaded template.
9. **`partial_close` disabled in v1.** Treated as `hold` until the sim resolution rule is specified.
10. **Pre-build copy step replaces symlinks.** Explicit `make sync-vendored` target.
11. **Balance compounding stated explicitly.** `engine.current_balance` updates after every close and feeds the next `state['sim_balance']`.
12. **LLM failure tracking and threshold abort.** `llm_failures` column; runs fail if failure rate > 5%.
13. **Concurrent run limit.** `TESTER_MAX_CONCURRENT_RUNS=1` default.
14. **`from-matp` migration handles missing AI config rows.** Falls back to default values; response includes a flag.

Additional minor fixes: SL/TP priority changed to entry-distance heuristic; `ON DELETE CASCADE` audit complete; funding rates added to Non-Goals.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Goals and Non-Goals](#2-goals-and-non-goals)
3. [Architecture](#3-architecture)
4. [Database Design](#4-database-design)
5. [Service: strategy-tester](#5-service-strategy-tester)
6. [Backtest Engine](#6-backtest-engine)
7. [Graph Node Variants](#7-graph-node-variants)
8. [REST API](#8-rest-api)
9. [Strategy Migration](#9-strategy-migration)
10. [UI Specification](#10-ui-specification)
11. [Docker Integration](#11-docker-integration)
12. [LLM Cost Model](#12-llm-cost-model)
13. [Implementation Order](#13-implementation-order)
14. [Database Migration SQL](#14-database-migration-sql)
15. [Verification Checklist](#15-verification-checklist)

---

## 1. Overview

The MATP Strategy Tester is a self-contained backtesting service that runs AI-driven trading strategies against historical OHLCV data using the **same LangGraph signal pipeline** used by `ai-signal-generator`. The system simulates fills without touching any exchange, writes results to an isolated `tester` schema in the existing PostgreSQL instance, and provides a UI for reviewing results and promoting strategies to live MATP.

The key design principle is **engine equivalence**: the LLM prompt, indicator computation, and guard logic are duplicated verbatim from the live service so that a strategy performing well in backtesting will behave identically when promoted to live. Verbatim sharing via Python imports is not possible because each service maintains its own database pool and connection settings (see §3.3), so the relevant files are **copied at build time** with a strict no-drift policy.

### System context

```
┌────────────────────────────────────────────────────────────────────┐
│  Existing MATP services (unchanged)                                │
│  order-listener:8001  order-executor:8004  dashboard-api:8003      │
│  ai-signal-generator:8005  dashboard-ui:3000                       │
└──────────────────────┬─────────────────────────────────────────────┘
                       │ shared postgres + redis
┌──────────────────────▼─────────────────────────────────────────────┐
│  strategy-tester:8006   (new service — this document)              │
│  ┌──────────────┐  ┌───────────────┐  ┌────────────────────────┐  │
│  │ FastAPI API  │  │ Backtest      │  │ Tester UI              │  │
│  │ /api/tester/ │  │ Engine        │  │ /tester/ (React, 3001) │  │
│  └──────────────┘  └───────────────┘  └────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
                       │
               ┌───────▼────────┐
               │  tester schema │  (same Postgres instance, matp DB)
               └────────────────┘
```

---

## 2. Goals and Non-Goals

### Goals

- Run any MATP AI strategy against historical candle data and produce a full trade-by-trade simulation result.
- Reuse `compute_indicators`, `build_prompt`, and `LLMSignalOutput` schemas through file-level duplication with a no-drift check.
- Store strategies, orders, and positions in a schema-isolated mirror of the MATP tables.
- Allow copying/moving strategies between the tester and live MATP in both directions via a single API call.
- Cache OHLCV data so reruns of the same backtest period are free (no repeated ccxt fetches).
- Provide a UI with the same design tokens as the MATP dashboard, using the existing component vocabulary from `matp-ui-v0.37.html`.
- Display meaningful simulation metrics: equity curve, win rate, profit factor, long/short split, max drawdown, per-trade P&L.
- Run as an independent Docker service with no impact on live MATP operation.

### Non-Goals

- This service does **not** send orders to any exchange. No exchange credentials are held or used.
- Live paper trading (simulated execution against live market data in real-time) is out of scope for v1.
- Multi-asset portfolio backtesting (simultaneous open positions across multiple symbols from one strategy) is out of scope; the simulation assumes one position open at a time per strategy, matching MATP's live behavior.
- Optimisation loops (grid search over strategy parameters) are out of scope.
- **Funding rates are not simulated.** A 6-month perpetuals backtest at 10× leverage with positions held days-to-weeks may understate cost by ~1–10% of final result. Document this in run output.
- **`partial_close` is not simulated in v1.** Treated as `hold`. The live system's partial-close resolution mechanism is undocumented in the current codebase; until it is specified the sim cannot reproduce it faithfully.
- **Historical sentiment data (fear/greed, funding, news, macro) is not replayed in v1.** These inputs are set to `None` during backtesting. Strategies that materially depend on sentiment inputs will behave differently in sim vs. live.

---

## 3. Architecture

### 3.1 Service topology

```
Nginx (port 80)
  └── /api/tester/   → strategy-tester:8006 (FastAPI, Python)
  └── /tester/       → tester-ui:3001       (React/Vite, TypeScript)

strategy-tester:8006
  ├── app/api/             FastAPI routers
  ├── app/engine/          Backtest engine + sim node variants
  │   ├── backtest_engine.py
  │   ├── node_ingest_replay.py
  │   ├── node_guard_sim.py
  │   └── node_dispatch_sim.py
  ├── app/data/
  │   └── historical_ohlcv.py    (NEW: paginating fetcher; see §6.2)
  ├── app/graph/
  │   └── graph_sim.py           (builds the simulation graph)
  └── app/_vendored/             (copied at build time from ai-signal-generator)
      ├── node_analyze.py
      ├── indicators.py
      ├── prompt_builder.py
      └── prompt_templates.py

tester-ui:3001
  ├── src/screens/
  │   ├── StrategiesScreen.tsx
  │   └── SimulationScreen.tsx
  └── src/components/    Shared component library (cloned from dashboard-ui)
```

### 3.2 Code duplication policy — `_vendored/` (replaces the symlink approach)

Each MATP service has its own Docker build context, which means symlinks across service boundaries (proposed in v1.0) do not work. The tester instead uses a **pre-build copy step** with explicit drift detection.

The following files are copied from `ai-signal-generator/app/` into `strategy-tester/app/_vendored/`:

| Source | Destination |
|--------|-------------|
| `ai-signal-generator/app/graph/nodes/node_analyze.py` | `_vendored/node_analyze.py` |
| `ai-signal-generator/app/data/indicators.py`         | `_vendored/indicators.py` |
| `ai-signal-generator/app/prompt/builder.py`           | `_vendored/prompt_builder.py` |
| `ai-signal-generator/app/prompt/templates.py`         | `_vendored/prompt_templates.py` |

**Sync mechanism:** A `Makefile` target at the repo root:

```makefile
sync-vendored:
	@cp ai-signal-generator/app/graph/nodes/node_analyze.py  strategy-tester/app/_vendored/
	@cp ai-signal-generator/app/data/indicators.py            strategy-tester/app/_vendored/
	@cp ai-signal-generator/app/prompt/builder.py             strategy-tester/app/_vendored/prompt_builder.py
	@cp ai-signal-generator/app/prompt/templates.py           strategy-tester/app/_vendored/prompt_templates.py
	@sha256sum strategy-tester/app/_vendored/*.py > strategy-tester/app/_vendored/CHECKSUMS

check-vendored:
	@cd strategy-tester/app/_vendored && sha256sum -c CHECKSUMS

build: sync-vendored
	docker compose build strategy-tester tester-ui
```

The `check-vendored` target runs as the first step of `docker compose build` for `strategy-tester` (via Dockerfile `RUN make check-vendored`). If a vendored file has been modified locally without being re-synced, the build fails. If an upstream file has changed without `make sync-vendored` being run, the build fails. Both directions are protected.

A header comment is added to each vendored file:

```python
# ───────────────────────────────────────────────────────────────────
# VENDORED FROM: ai-signal-generator/app/graph/nodes/node_analyze.py
# DO NOT EDIT THIS FILE DIRECTLY.
# Run `make sync-vendored` from repo root after upstream changes.
# Build will fail if checksums do not match.
# ───────────────────────────────────────────────────────────────────
```

### 3.3 What runs in the simulation graph

The tester's `graph_sim.py` builds a `StateGraph(AgentState)` with the following nodes:

| Node | Source | Notes |
|------|--------|-------|
| `ingest`   | `engine/node_ingest_replay.py`   | Reads candle slice from engine; no network calls |
| `analyze`  | `_vendored/node_analyze.py`      | Verbatim copy; pool is tester's pool with `search_path = tester, public` |
| `guard`    | `engine/node_guard_sim.py`       | Substantial rewrite — see §7.2 |
| `dispatch` | `engine/node_dispatch_sim.py`    | Writes simulated fill to `tester.orders` |

The vendored `node_analyze` works correctly inside the tester because it imports `from app.database import get_pool` — which in the tester service resolves to the tester's `app/database.py`, returning the tester's connection pool. This works because `_vendored/` is placed under `app/`, so `from app...` imports are correctly resolved to the tester service.

The same logic applies for the vendored `prompt_builder.py` and `prompt_templates.py`: their `from app.prompt.templates import ...` lines resolve to vendored copies as long as the import paths inside `_vendored/` files are adjusted at sync time. **Sync requires this rewrite:**

```bash
# In the Makefile's sync-vendored target, after copying:
sed -i 's|from app.prompt.templates|from app._vendored.prompt_templates|g' \
    strategy-tester/app/_vendored/prompt_builder.py
sed -i 's|from app.prompt.builder|from app._vendored.prompt_builder|g' \
    strategy-tester/app/_vendored/node_analyze.py
```

Path rewriting is mechanical and idempotent; it's part of the sync step. The checksum is computed after rewriting.

---

## 4. Database Design

### 4.1 Schema isolation and connection management

All tester tables live in a separate `tester` schema within the existing `matp` PostgreSQL database. **Reads** from shared reference tables (e.g. `public.ai_prompt_templates`) are permitted via search_path; **writes** to tester data must always be fully-qualified to defend against pool reconnects or misconfiguration.

```sql
CREATE SCHEMA IF NOT EXISTS tester;
```

Connection strings remain identical to the rest of MATP (`postgresql://matp:matp@postgres:5432/matp`). The tester service initialises its asyncpg pool with a per-connection `init` hook so that every connection — including reconnects from idle timeouts or broken sockets — gets the correct search_path:

```python
async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.execute("SET search_path = tester, public")

pool = await asyncpg.create_pool(
    dsn=settings.database_url,
    init=_init_conn,
    min_size=2,
    max_size=10,
)
```

**Defence-in-depth:** Even with the init hook in place, all `INSERT`, `UPDATE`, and `DELETE` statements in tester code must explicitly write to `tester.*` tables:

```python
# Required
await conn.execute("INSERT INTO tester.strategies (...) VALUES (...)")
# Forbidden — relies on search_path
await conn.execute("INSERT INTO strategies (...) VALUES (...)")
```

Reads from shared tables (e.g. `public.ai_prompt_templates`) may use unqualified names because the `tester` schema does not contain those tables and search_path resolution will find the public ones unambiguously. This rule is checked by a `grep` in CI:

```bash
# Fail the build if any tester source contains unqualified write to a shared table name
grep -rE 'INSERT INTO (strategies|orders|strategy_positions|ai_signal_log)\b' strategy-tester/app/ \
  && echo "FAIL: use tester.* prefix" && exit 1
```

### 4.2 Mirrored tables

The following tables are created in `tester` schema with column structures matching their `public` counterparts. Foreign keys that referenced `exchange_accounts` are removed (no real exchange accounts in simulation). All FKs that link to other `tester.*` tables use `ON DELETE CASCADE` so that deleting a backtest run cleanly removes all derived data.

#### `tester.strategies`

Columns match `public.strategies` plus:
- `id` prefix convention: `tst_` with 12 hex chars (e.g. `tst_a3f2e8c14b9d`).
- `account_id` is nullable with no foreign key constraint.
- `webhook_secret` is generated but never used for authentication.
- `source_matp_id VARCHAR(100)` — nullable; populated when imported from MATP live.

#### `tester.orders`

Columns match `public.orders` plus:
- `backtest_run_id UUID NOT NULL REFERENCES tester.backtest_runs(id) ON DELETE CASCADE`
- `candle_timestamp TIMESTAMPTZ NOT NULL` — the historical candle close time at which this order was generated. Used everywhere `received_at` is used in `public.orders`.
- `fee NUMERIC` — simulated taker fee charged at fill.

#### `tester.strategy_positions`

Columns match `public.strategy_positions` plus:
- `backtest_run_id UUID NOT NULL REFERENCES tester.backtest_runs(id) ON DELETE CASCADE`
- `fee_open NUMERIC` — fee charged at open.
- `fee_close NUMERIC` — fee charged at close.
- `close_reason VARCHAR(50)` — one of `tp_hit`, `sl_hit`, `llm_close`, `run_end`.

#### `tester.ai_signal_log`

Mirrors `public.ai_signal_log` plus:
- `backtest_run_id UUID REFERENCES tester.backtest_runs(id) ON DELETE CASCADE`
- `triggered_at` is set to the **candle close timestamp**, not `NOW()`. This is essential for cooldown logic; see §7.2.

#### `tester.ai_strategy_config` and `tester.ai_risk_config`

Mirror `public.ai_strategy_config` and `public.ai_risk_config` exactly. Cascade from `tester.strategies` on delete.

### 4.3 Tester-only tables

#### `tester.backtest_runs`

```sql
CREATE TABLE tester.backtest_runs (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id         VARCHAR(100) NOT NULL REFERENCES tester.strategies(id),
    timeframe           VARCHAR(10)  NOT NULL,
    date_from           DATE         NOT NULL,
    date_to             DATE         NOT NULL,
    lookback_days       INTEGER      NOT NULL DEFAULT 90,
    initial_balance     NUMERIC      NOT NULL DEFAULT 1000.0,
    slippage_pct        NUMERIC      NOT NULL DEFAULT 0.05,
    fee_pct             NUMERIC      NOT NULL DEFAULT 0.02,
    status              VARCHAR(20)  NOT NULL DEFAULT 'pending',
    -- progress
    candles_processed   INTEGER  DEFAULT 0,
    total_candles       INTEGER,
    -- aggregate metrics
    total_signals       INTEGER,
    gate_passed         INTEGER,
    llm_failures        INTEGER  DEFAULT 0,         -- NEW: track LLM errors
    llm_failure_rate    NUMERIC(5,2),               -- NEW
    total_trades        INTEGER,
    winning_trades      INTEGER,
    losing_trades       INTEGER,
    win_rate            NUMERIC(5,2),
    total_pnl           NUMERIC(18,8),
    total_pnl_pct       NUMERIC(8,4),
    profit_factor       NUMERIC(10,4),
    max_drawdown_pct    NUMERIC(8,4),               -- computed from mark-balance series
    sharpe_approx       NUMERIC(8,4),
    long_count          INTEGER,
    short_count         INTEGER,
    avg_win             NUMERIC(18,8),
    avg_loss            NUMERIC(18,8),
    largest_win         NUMERIC(18,8),
    largest_loss        NUMERIC(18,8),
    total_fees_paid     NUMERIC(18,8),              -- NEW: sum of simulated fees
    -- execution metadata
    llm_provider        VARCHAR(50),
    llm_model           VARCHAR(100),
    estimated_cost_usd  NUMERIC(10,6),
    actual_tokens_used  INTEGER,
    error_message       TEXT,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('pending','running','completed','failed','cancelled','aborted_high_failure_rate'))
);
```

The `aborted_high_failure_rate` status is set when LLM failures exceed the configured threshold (see §6.7).

#### `tester.ohlcv_cache`

```sql
CREATE TABLE tester.ohlcv_cache (
    id          BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(20)  NOT NULL,
    timeframe   VARCHAR(10)  NOT NULL,
    exchange    VARCHAR(30)  NOT NULL DEFAULT 'binance',
    candle_ts   TIMESTAMPTZ  NOT NULL,
    open        NUMERIC      NOT NULL,
    high        NUMERIC      NOT NULL,
    low         NUMERIC      NOT NULL,
    close       NUMERIC      NOT NULL,
    volume      NUMERIC      NOT NULL,
    fetched_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, timeframe, exchange, candle_ts)
);
```

Cache invalidation runs on service start: rows older than 30 days are deleted for timeframes ≥ `1d`, 7 days for timeframes ≤ `1h`.

#### `tester.equity_curve`

Per-candle mark-balance snapshots (rewritten from v1.0 to support correct drawdown computation).

```sql
CREATE TABLE tester.equity_curve (
    id              BIGSERIAL PRIMARY KEY,
    backtest_run_id UUID        NOT NULL REFERENCES tester.backtest_runs(id) ON DELETE CASCADE,
    candle_ts       TIMESTAMPTZ NOT NULL,
    realized_balance NUMERIC    NOT NULL,            -- balance from closed trades only
    mark_balance     NUMERIC    NOT NULL,            -- realized + unrealized P&L
    trade_pnl       NUMERIC,                         -- non-null on candle a trade closed
    drawdown_pct    NUMERIC,                         -- computed against running peak of mark_balance
    UNIQUE (backtest_run_id, candle_ts)
);

CREATE INDEX ON tester.equity_curve (backtest_run_id, candle_ts);
```

Every candle in the run produces one row (not just trade-close candles). For a 4,320-candle run that's 4,320 inserts — performed in batches of 500 via `executemany` to keep DB pressure low.

---

## 5. Service: strategy-tester

### 5.1 Directory structure

```
strategy-tester/
├── Dockerfile
├── Makefile                       (defines sync-vendored, check-vendored)
├── requirements.txt
└── app/
    ├── config.py
    ├── database.py                (asyncpg pool with init hook → search_path)
    ├── main.py
    ├── api/
    │   ├── strategies.py
    │   ├── runs.py
    │   ├── results.py
    │   └── migrate.py
    ├── engine/
    │   ├── backtest_engine.py
    │   ├── node_ingest_replay.py
    │   ├── node_guard_sim.py
    │   └── node_dispatch_sim.py
    ├── data/
    │   └── historical_ohlcv.py    (paginating fetcher; see §6.2)
    ├── graph/
    │   └── graph_sim.py
    └── _vendored/
        ├── CHECKSUMS              (sha256 hashes verified at build time)
        ├── node_analyze.py
        ├── indicators.py
        ├── prompt_builder.py
        └── prompt_templates.py
```

### 5.2 Environment variables

```
DATABASE_URL                  postgresql://matp:matp@postgres:5432/matp
REDIS_URL                     redis://redis:6379
GEMINI_API_KEY                (same key as ai-signal-generator)
OPENAI_API_KEY                (optional)
ANTHROPIC_API_KEY             (optional)
TESTER_DEFAULT_BALANCE        1000.0
TESTER_DEFAULT_SLIPPAGE_PCT   0.05    (0.05 = 0.05%)
TESTER_DEFAULT_FEE_PCT        0.02    (taker fee per fill)
TESTER_MAX_CONCURRENT_RUNS    1       (NEW: hard limit on parallel backtests)
TESTER_LLM_FAILURE_THRESHOLD  0.05    (NEW: abort run if >5% of LLM calls fail)
TESTER_OHLCV_FETCH_BATCH      1000    (ccxt fetch_ohlcv limit)
TESTER_EQUITY_INSERT_BATCH    500     (rows per executemany)
```

### 5.3 Startup sequence

On service start (`lifespan`):

1. Connect to PostgreSQL with init hook (`SET search_path = tester, public`).
2. Verify `tester` schema exists; if migrations are pending, fail with a clear error (migrations must be run separately, not at app start).
3. Verify `_vendored/CHECKSUMS` matches the actual files (defence-in-depth — the Dockerfile already checked at build time).
4. Run `ohlcv_cache` cleanup.
5. Initialise the run semaphore: `asyncio.Semaphore(TESTER_MAX_CONCURRENT_RUNS)`.
6. Start FastAPI.

No background schedulers. All work is request-driven.

---

## 6. Backtest Engine

### 6.1 Candle replay loop with explicit causality ordering

The engine processes candles in strict temporal order with the following per-candle steps. **The ordering of step 1 before step 4 is the key rule that prevents look-ahead bias.**

```
engine.current_balance = run.initial_balance
engine.open_position   = None
engine.run_semaphore.acquire()                    # respect MAX_CONCURRENT_RUNS

candles = fetch_ohlcv_range(symbol, timeframe,
                            ts_from = date_from - lookback_days,
                            ts_to   = date_to)

# Skip the first 200 candles (warmup for indicators)
warmup_end = max(200, candles_needed_for_indicators)

For each candle N in candles[warmup_end : ]:

    candle_ts = candles[N].close_time

    # ── STEP 1: SL/TP check on existing position (uses candle N's high/low)
    if engine.open_position is not None:
        if engine.open_position.opened_at_candle == N:
            # Position was opened on this same candle in a prior iteration of
            # an earlier candle's logic — guarded against, but assert:
            assert False, "open_at_candle == N implies look-ahead; this is a bug"
        tp_sl_result = check_tp_sl(engine.open_position, candles[N])
        if tp_sl_result is not None:
            close_position_sim(engine, tp_sl_result.price, tp_sl_result.reason, candle_ts)
            # equity is recomputed in step 5 of this same candle

    # ── STEP 2: build AgentState from candles up to and including N
    candle_window = candles[N - window_size + 1 : N + 1]      # inclusive of N
    state = build_initial_state(
        strategy_config,
        candle_window,
        simulated_now = candle_ts,         # ← critical: used by guard cooldown
        sim_balance   = engine.current_balance,
        backtest_run_id = run.id,
    )

    # ── STEP 3: run the graph
    try:
        final_state = await graph.ainvoke(state)
    except Exception as e:
        engine.llm_failures += 1
        await write_failure_signal_log(run, candle_ts, e)
        check_failure_threshold(engine, run)
        continue

    # ── STEP 4: act on graph output, but DEFER fills to next candle's open
    action = (final_state.get('llm_signal') or {}).get('action')
    if final_state.get('gate_passed') and action in ('open_long', 'open_short'):
        # Mark intent — actual fill happens at candle N+1's open
        engine.pending_open = OpenIntent(
            side          = 'long' if action == 'open_long' else 'short',
            triggered_at  = candle_ts,
            size_resolved = final_state['resolved_size'],
            sl_price      = final_state['resolved_sl_price'],
            tp_price      = final_state['resolved_tp_price'],
        )
    elif final_state.get('gate_passed') and action in ('close_long', 'close_short'):
        if engine.open_position is not None:
            engine.pending_close = CloseIntent(triggered_at=candle_ts)

    # ── STEP 5: equity snapshot for candle N (per §6.5)
    mark_pnl = compute_mark_pnl(engine.open_position, candles[N].close) if engine.open_position else 0
    await append_equity_curve(run.id, candle_ts,
                              realized_balance = engine.current_balance,
                              mark_balance     = engine.current_balance + mark_pnl,
                              trade_pnl        = tp_sl_result.pnl if tp_sl_result else None)

    # ── STEP 6: process pending open/close at start of next iteration
    # (handled at top of loop iteration N+1, using candle[N+1].open as fill price)

engine.run_semaphore.release()
```

### 6.2 OHLCV fetching — new paginating module

The live `ai-signal-generator/app/data/ohlcv.py` does not pass `since` to ccxt because exchanges cap `limit` and a far-back `since` yields stale "current price". Backtesting requires the opposite: explicit pagination through historical windows.

A new file `strategy-tester/app/data/historical_ohlcv.py` implements this:

```python
"""
Historical OHLCV fetcher with pagination.
Designed for backtest preloading, not live data fetch.
"""
import asyncio
import logging
from datetime import datetime, timezone

import ccxt.async_support as ccxt_async

logger = logging.getLogger(__name__)

_TIMEFRAME_MS = {
    '1m':   60_000, '3m':   180_000,  '5m':   300_000,
    '15m':  900_000, '30m':  1_800_000,
    '1h':   3_600_000, '2h':   7_200_000, '4h':  14_400_000,
    '8h':  28_800_000, '1d':  86_400_000,
}


async def fetch_ohlcv_range(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    ts_from_ms: int,
    ts_to_ms: int,
    batch_limit: int = 1000,
    pause_between_batches: float = 0.2,
) -> list[dict]:
    """
    Fetch all candles in [ts_from_ms, ts_to_ms] inclusive by paginating
    forward with `since`. Returns a deduplicated, sorted list of candle dicts.
    """
    tf_ms = _TIMEFRAME_MS.get(timeframe)
    if tf_ms is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    cls = getattr(ccxt_async, exchange_id, None)
    if cls is None:
        raise ValueError(f"Unknown ccxt exchange: {exchange_id}")
    exchange = cls({'enableRateLimit': True})

    candles: list[dict] = []
    seen_ts: set[int] = set()
    since = ts_from_ms

    try:
        await exchange.load_markets()
        while since < ts_to_ms:
            try:
                batch = await exchange.fetch_ohlcv(
                    symbol, timeframe=timeframe,
                    since=since, limit=batch_limit,
                )
            except Exception as exc:
                logger.error("fetch_ohlcv_range batch failed at since=%d: %s", since, exc)
                raise

            if not batch:
                break

            new_count = 0
            for c in batch:
                ts = c[0]
                if ts in seen_ts or ts > ts_to_ms:
                    continue
                seen_ts.add(ts)
                candles.append({
                    'timestamp': ts,
                    'open': c[1], 'high': c[2], 'low': c[3],
                    'close': c[4], 'volume': c[5],
                })
                new_count += 1

            if new_count == 0:
                # exchange returned only data we've already seen; stop
                break

            # Advance past the last received candle
            since = batch[-1][0] + tf_ms

            # If batch returned fewer than limit, we've likely reached the end
            if len(batch) < batch_limit:
                break

            await asyncio.sleep(pause_between_batches)

    finally:
        try:
            await exchange.close()
        except Exception:
            pass

    candles.sort(key=lambda c: c['timestamp'])
    return candles
```

The backtest engine calls this once per run, then upserts results into `tester.ohlcv_cache`. Subsequent runs over the same range fetch nothing from ccxt — they read from cache only.

### 6.3 Causality rules (look-ahead bias prevention)

The engine enforces the following hard rules. Violations are programming errors and trigger assertions.

**Rule 1: A position cannot open and close on the same candle.**

When the graph signals `open_long` on candle N, the position is filled at **candle N+1's open price** (not candle N's close). The SL/TP check for that position begins at candle N+1.

The `OpenIntent` and `CloseIntent` pattern in the loop enforces this: intent is recorded on the candle the LLM decided, and the actual fill happens at the top of the next iteration.

```python
# At top of iteration for candle N+1:
if engine.pending_open is not None:
    fill_price = candles[N+1].open
    # Apply slippage
    if engine.pending_open.side == 'long':
        fill_price *= (1 + slippage_pct / 100)
    else:
        fill_price *= (1 - slippage_pct / 100)
    open_position_sim(engine, engine.pending_open, fill_price, candles[N+1].close_time)
    engine.pending_open = None
```

**Rule 2: SL/TP checks never see candle data from before position open.**

If `engine.open_position.opened_at_candle_index == M`, the SL/TP check is applied to candles M+1, M+2, … but not M itself.

**Rule 3: When SL and TP would both hit within the same candle, use the entry-distance heuristic.**

Whichever level is closer to the entry price (in absolute terms) is assumed to have been hit first. This is more defensible than "always SL first":

```python
def resolve_ambiguous_hit(position, candle):
    sl_dist = abs(position.entry_price - position.sl_price)
    tp_dist = abs(position.entry_price - position.tp_price)
    if sl_dist <= tp_dist:
        return ('sl_hit', position.sl_price)
    else:
        return ('tp_hit', position.tp_price)
```

If only one side is hit, that side wins unambiguously.

**Rule 4: Indicator values use only candles up to and including the decision candle.**

When `node_ingest_replay` builds `ohlcv_data`, `candle_window` slice is `candles[N - W + 1 : N + 1]` — inclusive of N but never including N+1 or later. The vendored `compute_indicators` then runs over this window.

### 6.4 Balance compounding (explicit)

The engine maintains a single `engine.current_balance` variable updated **only** when a trade closes:

```python
def close_position_sim(engine, close_price, reason, candle_ts):
    pos = engine.open_position
    if pos.side == 'long':
        gross_pnl = (close_price - pos.entry_price) * pos.size * pos.leverage
    else:
        gross_pnl = (pos.entry_price - close_price) * pos.size * pos.leverage
    fee_close = close_price * pos.size * engine.fee_pct / 100
    net_pnl   = gross_pnl - pos.fee_open - fee_close

    engine.current_balance += net_pnl                # ← compounding
    write_closed_position(...)
    engine.open_position = None
```

The next graph invocation injects this updated balance:

```python
state['sim_balance'] = engine.current_balance        # used by node_guard_sim
```

This means position sizing compounds naturally: a strategy returning +5% per trade grows the balance available for the next trade by 5%, and the next position is correspondingly larger.

### 6.5 Per-candle equity snapshots and drawdown

Every candle produces an `equity_curve` row, including candles where no trade closed. The `mark_balance` column captures unrealized P&L:

```python
def compute_mark_pnl(open_position, candle_close_price):
    if open_position is None:
        return 0
    if open_position.side == 'long':
        return (candle_close_price - open_position.entry_price) * open_position.size * open_position.leverage
    else:
        return (open_position.entry_price - candle_close_price) * open_position.size * open_position.leverage
```

Inserts are batched. The engine accumulates rows in memory and flushes every `TESTER_EQUITY_INSERT_BATCH` candles:

```python
async def flush_equity_buffer(pool, run_id, buffer):
    if not buffer:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO tester.equity_curve
               (backtest_run_id, candle_ts, realized_balance, mark_balance, trade_pnl, drawdown_pct)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            buffer,
        )
    buffer.clear()
```

**Max drawdown** is computed from the `mark_balance` series, not from closed trades:

```python
def compute_max_drawdown_pct(mark_balance_series):
    peak = mark_balance_series[0]
    max_dd = 0.0
    for balance in mark_balance_series:
        if balance > peak:
            peak = balance
        if peak > 0:
            dd = (peak - balance) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 4)
```

This captures intra-trade drawdowns that v1.0 would have missed entirely.

### 6.6 Simulated fill model (slippage and fees)

When a position opens on candle N+1 (filled at candle N+1's open):
- Fill price = `candles[N+1].open * (1 + slippage/100)` for long, `* (1 - slippage/100)` for short.
- `fee_open = fill_price * size * fee_pct / 100`.
- `engine.current_balance -= fee_open` (fee charged at fill, before position is in P&L).

When a position closes:
- `fee_close = close_price * size * fee_pct / 100`.
- `net_pnl = gross_pnl - fee_open - fee_close`.
- `engine.current_balance += net_pnl`.

Slippage is one-sided (entry only) or two-sided (entry + exit) — v1 uses two-sided to be conservative.

### 6.7 LLM failure handling

`node_analyze` raises on malformed LLM output, content filters, or network errors. The engine counts these and aborts the run if they exceed the configured threshold.

```python
def check_failure_threshold(engine, run):
    if engine.candles_processed < 100:
        return                                # too early to judge
    failure_rate = engine.llm_failures / engine.candles_processed
    if failure_rate > settings.tester_llm_failure_threshold:
        raise BacktestAbortedHighFailureRate(
            f"LLM failure rate {failure_rate:.1%} exceeds threshold "
            f"{settings.tester_llm_failure_threshold:.1%}"
        )
```

When raised, the engine sets `status = 'aborted_high_failure_rate'` and records `llm_failures`, `llm_failure_rate`, and `error_message` on the run. The UI surfaces this distinctly from `failed` (which is for engine errors, not LLM errors).

The threshold (default 5%) is configurable via env. A run with 5% LLM failures over 4,320 candles silently dropped ~216 decisions — making the remaining results unrepresentative.

### 6.8 Concurrency limit

The service holds a single `asyncio.Semaphore` initialised to `TESTER_MAX_CONCURRENT_RUNS` (default 1). Every backtest acquires before starting and releases on completion:

```python
async def run_backtest(run_id):
    async with engine.run_semaphore:
        await _execute_run(run_id)
```

If a run is submitted while the semaphore is exhausted, its status remains `pending` until a slot opens. The `GET /runs/{id}` response includes a queue position so the UI can show "Queued — 2 runs ahead".

For a single-user homelab, the default of 1 is safe and avoids rate-limit issues with LLM providers. Power users can set it to 2–3 if they confirm their LLM provider can handle the parallel load.

### 6.9 Aggregate metrics computation

On run completion, the engine computes and writes:

| Metric | Computation |
|--------|-------------|
| `win_rate` | `winning_trades / total_trades * 100` |
| `profit_factor` | `sum(winning_net_pnl) / abs(sum(losing_net_pnl))` |
| `max_drawdown_pct` | Max drawdown over `mark_balance` series (see §6.5) |
| `sharpe_approx` | `mean(trade_returns) / std(trade_returns) * sqrt(252)` |
| `avg_win` | `mean(net_pnl for winning trades)` |
| `avg_loss` | `mean(net_pnl for losing trades)` |
| `total_fees_paid` | `sum(fee_open + fee_close for all closed positions)` |
| `llm_failure_rate` | `llm_failures / total_signals` |
| `estimated_cost_usd` | See §12 |

---

## 7. Graph Node Variants

### 7.1 `node_ingest_replay`

Replaces `node_ingest`. Reads candle window from state, computes indicators, sets all unavailable historical inputs (sentiment, news, macro) to `None`.

```python
from app._vendored.indicators import compute_indicators
from app.graph.state import AgentState

def _pct_change(candles, candles_back):
    if len(candles) <= candles_back:
        return 0.0
    cur  = candles[-1]['close']
    past = candles[-candles_back - 1]['close']
    return round((cur - past) / past * 100, 2) if past else 0.0


async def node_ingest_replay(state: AgentState) -> AgentState:
    sc            = state['strategy_config']
    candle_window = state.get('replay_candle_window', [])
    enabled_inds  = sc.get('indicators') or ['RSI', 'MACD', 'EMA50', 'EMA200', 'BB', 'VWAP']
    timeframe     = state.get('cycle_interval', '1h')

    ohlcv_data           = None
    technical_indicators = None

    if sc.get('use_technical') and candle_window:
        current = candle_window[-1]
        candles_per_day = max(1, 86400 // _tf_seconds(timeframe))
        ohlcv_data = {
            'symbol':              f"{sc.get('base_asset', '')}/{sc.get('quote_asset', '')}",
            'timeframe':           timeframe,
            'candles':             candle_window,
            'current_price':       current['close'],
            'price_change_24h_pct': _pct_change(candle_window, candles_per_day),
            'price_change_7d_pct':  _pct_change(candle_window, candles_per_day * 7),
        }
        technical_indicators = compute_indicators(candle_window, enabled_inds)

    return {
        **state,
        'ohlcv_data':           ohlcv_data,
        'technical_indicators': technical_indicators,
        'sentiment_data':       {'fear_greed': None, 'funding_rate': None, 'open_interest': None},
        'news_data':            None,
        'market_context':       {'btc_dominance': None, 'macro': None},
        'data_fetch_errors':    [],
    }
```

### 7.2 `node_guard_sim` — substantive rewrite from `node_guard`

This node differs from `node_guard` in five concrete ways. Documenting them honestly:

1. **Balance source:** reads `state['sim_balance']` (a float) instead of HTTP call to `order-executor`.
2. **Cooldown check uses simulated time** — the v1.0-bug fix. The comparison reference is `state['simulated_now']` (which equals the current candle's close timestamp), not `NOW()`:

```python
# OLD (live):
WHERE triggered_at >= NOW() - timedelta(minutes=cooldown_minutes)

# NEW (sim):
WHERE backtest_run_id = $run_id
  AND triggered_at >= $simulated_now - INTERVAL 'X minutes'
  AND triggered_at <  $simulated_now
```

3. **Cooldown queries `tester.ai_signal_log`**, scoped to the current `backtest_run_id`. Different runs do not pollute each other's cooldown state.
4. **`pnl_today` is read from `state['sim_pnl_today']`** (engine-injected; computed from positions closed within the last 24 sim-hours), not from `public.strategies.pnl_today`.
5. **`partial_close` is rejected**, returning `gate_rejection_reason='partial_close_not_supported_in_sim'`. See §2 Non-Goals.

The rest of the logic — confidence threshold, action coherence, size resolution, SL/TP calculation — is byte-identical to the live node.

```python
async def node_guard_sim(state):
    # ... identical preamble ...

    if action == 'partial_close':
        return _reject(state, 'partial_close_not_supported_in_sim')

    # ... cooldown check using state['simulated_now'] and tester.ai_signal_log ...

    cooldown_key = _ACTION_COOLDOWN.get(action)
    if cooldown_key:
        cooldown_minutes = int(sc.get(cooldown_key) or 240)
        simulated_now    = state['simulated_now']
        run_id           = state['backtest_run_id']
        async with pool.acquire() as conn:
            last = await conn.fetchval(
                """
                SELECT triggered_at FROM tester.ai_signal_log
                WHERE backtest_run_id = $1
                  AND strategy_id     = $2
                  AND proposed_action = $3
                  AND gate_passed     = TRUE
                  AND triggered_at >= $4 - ($5 || ' minutes')::interval
                  AND triggered_at <  $4
                ORDER BY triggered_at DESC
                LIMIT 1
                """,
                run_id, state['strategy_id'], action,
                simulated_now, cooldown_minutes,
            )
        if last is not None:
            return _reject(state, 'cooldown_active')

    # ... size resolution using state['sim_balance'] ...

    if action in ('open_long', 'open_short'):
        usdt_balance  = float(state['sim_balance'])
        size_pct      = min(float(signal['size_pct']), float(rc.get('max_position_size_pct') or 5.0))
        usdt_alloc    = usdt_balance * size_pct / 100.0
        current_price = float((state.get('ohlcv_data') or {}).get('current_price') or 0)
        # ... rest identical to live node ...
```

### 7.3 `node_dispatch_sim`

Replaces `node_dispatch`. Writes to `tester.ai_signal_log` (not `public.ai_signal_log`) with `triggered_at` set to the simulated candle timestamp. Does not fire any HTTP webhook.

```python
async def node_dispatch_sim(state):
    pool         = get_pool()
    sc           = state['strategy_config']
    signal       = state.get('llm_signal') or {}
    action       = signal.get('action')
    confidence   = signal.get('confidence')
    reasoning    = signal.get('reasoning')
    simulated_now = state['simulated_now']                # ← candle timestamp
    run_id       = state['backtest_run_id']

    # Write tester.ai_signal_log row with triggered_at = simulated_now
    signal_log_id = None
    async with pool.acquire() as conn:
        signal_log_id = await conn.fetchval(
            """
            INSERT INTO tester.ai_signal_log (
                backtest_run_id, strategy_id, triggered_at, trigger_reason, cycle_interval,
                prompt_template, data_sources_used, context_tokens,
                proposed_action, confidence, reasoning,
                gate_passed, gate_rejection_reason, dry_run,
                llm_provider, llm_model
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            RETURNING id
            """,
            run_id, state['strategy_id'], simulated_now,
            state.get('trigger_reason', 'replay'),
            state.get('cycle_interval', '1h'),
            sc.get('template_id', 'trend_following'),
            _data_sources_used(sc),
            state.get('context_tokens'),
            action, confidence, reasoning,
            bool(state.get('gate_passed', False)),
            state.get('gate_rejection_reason'),
            True,                                          # always dry_run in sim
            sc.get('llm_provider', 'google'),
            sc.get('llm_model', 'gemini-2.0-flash'),
        )

    # Gate failed or non-actionable
    if not state.get('gate_passed') or action in ('hold', 'adjust_stops', 'partial_close'):
        return {**state, 'signal_log_id': signal_log_id, 'webhook_fired': False}

    # Action is open/close — engine reads this from state and handles the fill
    # (no HTTP call; no DB write here for orders — engine writes when fill executes)
    return {
        **state,
        'signal_log_id':  signal_log_id,
        'webhook_fired':  True,            # signals engine to process intent
        'sim_action':     action,
    }
```

The engine reads `final_state['webhook_fired']` and `final_state['sim_action']` to decide whether to set `pending_open` or `pending_close` (per §6.1).

---

## 8. REST API

Base path: `/api/tester/`

### 8.1 Strategies

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/strategies` | List all tester strategies |
| `POST` | `/strategies` | Create a new tester strategy |
| `GET` | `/strategies/{id}` | Get single strategy with latest run stats |
| `PUT` | `/strategies/{id}` | Update strategy config |
| `DELETE` | `/strategies/{id}` | Soft-delete (sets `is_deleted = true`) |
| `GET` | `/strategies/{id}/runs` | List all backtest runs for a strategy |

### 8.2 Backtest Runs

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/runs` | Submit a new backtest (may queue if at concurrency limit) |
| `GET` | `/runs/{id}` | Get run status, progress, and metrics |
| `POST` | `/runs/{id}/cancel` | Cancel a queued or running backtest |
| `DELETE` | `/runs/{id}` | Delete run and all derived data (cascade) |

`POST /runs` body:

```json
{
  "strategy_id": "tst_a3f2e8c14b9d",
  "date_from": "2024-12-01",
  "date_to": "2025-06-01",
  "initial_balance": 1000.0,
  "slippage_pct": 0.05,
  "fee_pct": 0.02,
  "llm_model_override": "gemini-2.5-flash"
}
```

`llm_model_override` is optional — if provided, overrides the strategy's configured model for this run only. Enables "same strategy, different model" comparisons.

`GET /runs/{id}` while queued:

```json
{
  "id": "...",
  "status": "pending",
  "queue_position": 2,
  "estimated_start": "in ~12 minutes (based on average run time)"
}
```

`GET /runs/{id}` while running:

```json
{
  "id": "...",
  "status": "running",
  "progress": {
    "candles_processed": 1240,
    "total_candles": 4320,
    "pct": 28.7,
    "llm_failures_so_far": 3
  },
  "started_at": "2026-06-10T10:00:00Z"
}
```

`GET /runs/{id}` aborted due to LLM failures:

```json
{
  "id": "...",
  "status": "aborted_high_failure_rate",
  "llm_failures": 47,
  "llm_failure_rate": 6.83,
  "candles_processed": 688,
  "error_message": "LLM failure rate 6.8% exceeds threshold 5.0%. Consider switching model or simplifying prompt.",
  "completed_at": "2026-06-10T10:04:12Z"
}
```

### 8.3 Cost estimation (NEW)

`POST /api/tester/estimate-cost` — returns measured token cost based on the actual loaded prompt template, not hardcoded estimates.

Request:

```json
{
  "strategy_id": "tst_a3f2e8c14b9d",
  "timeframe": "1h",
  "date_from": "2024-12-01",
  "date_to": "2025-06-01",
  "llm_model_override": "gemini-2.5-flash"
}
```

Implementation: the endpoint builds a single representative prompt using the strategy's actual `template_id` + `custom_instructions` + enabled data sources, measures the token count via `get_estimated_tokens()`, multiplies by total candles, and applies the model's pricing.

Response:

```json
{
  "total_candles": 4320,
  "measured_input_tokens_per_call": 1480,
  "estimated_output_tokens_per_call": 300,
  "model": "gemini-2.5-flash",
  "estimated_cost_low_usd": 1.92,
  "estimated_cost_high_usd": 2.88,
  "explanation": "Range reflects: ±20% input variance with position-open vs no-position prompts; output may grow with verbose reasoning."
}
```

This is shown to the user in the "Run Backtest" config panel before they commit. It replaces the static cost table in §12 as the authoritative number for any given strategy.

### 8.4 Results

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/runs/{id}/orders` | All simulated orders for a run, paginated |
| `GET` | `/runs/{id}/positions` | All simulated positions (open + closed) |
| `GET` | `/runs/{id}/equity-curve` | Per-candle mark-balance points for chart |
| `GET` | `/runs/{id}/signals` | All LLM signal log rows (including filtered ones) |

### 8.5 Migration

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/migrate/from-matp` | Import a MATP live strategy into tester |
| `POST` | `/migrate/to-matp/{id}` | Promote a tester strategy to MATP live |

`POST /migrate/from-matp` response — includes a flag indicating whether default AI config was used:

```json
{
  "new_tester_id": "tst_b8d1c2a3e4f5",
  "source_matp_id": "btc-trend-a3f2",
  "mode": "copy",
  "ai_config_imported": false,
  "ai_config_note": "Source strategy had no ai_strategy_config row; defaults applied. Review and adjust before running backtests."
}
```

When `ai_config_imported` is false, the UI shows a yellow banner on the strategy card: "AI config defaulted — review before backtesting."

---

## 9. Strategy Migration

### 9.1 Cross-schema copy

Both directions use SQL `INSERT ... SELECT` inside a single transaction. The transaction guarantees that if either the strategy row or the AI config row fails to copy, the entire migration is rolled back.

### 9.2 Handling missing source data

Not all MATP strategies have AI configuration — pure webhook (TradingView Pine) strategies have no row in `public.ai_strategy_config`. The `from-matp` migration handles this:

```sql
BEGIN;

-- 1. Copy strategy row (new id, blank account_id, source_matp_id set)
INSERT INTO tester.strategies (id, ..., source_matp_id, ...)
SELECT
    'tst_' || substr(md5(random()::text || clock_timestamp()::text), 1, 12) AS id,
    ..., s.id, ...
FROM public.strategies s
WHERE s.id = $source_matp_id
RETURNING id AS new_tester_id;

-- 2. Try to copy ai_strategy_config; if no row exists, insert defaults
INSERT INTO tester.ai_strategy_config (strategy_id, template_id, llm_provider, llm_model, ...)
SELECT $new_tester_id, asc.template_id, asc.llm_provider, asc.llm_model, ...
FROM public.ai_strategy_config asc
WHERE asc.strategy_id = $source_matp_id;

-- If the above inserted zero rows, insert defaults:
INSERT INTO tester.ai_strategy_config (strategy_id)
SELECT $new_tester_id
WHERE NOT EXISTS (
    SELECT 1 FROM tester.ai_strategy_config WHERE strategy_id = $new_tester_id
);
-- All other columns get their schema-defined defaults.

-- 3. Same pattern for ai_risk_config
INSERT INTO tester.ai_risk_config (strategy_id, ...)
SELECT $new_tester_id, ...
FROM public.ai_risk_config WHERE strategy_id = $source_matp_id;

INSERT INTO tester.ai_risk_config (strategy_id)
SELECT $new_tester_id
WHERE NOT EXISTS (
    SELECT 1 FROM tester.ai_risk_config WHERE strategy_id = $new_tester_id
);

COMMIT;
```

The API response includes `ai_config_imported: bool` so the caller knows which path was taken. The UI surfaces this prominently — a strategy with default AI config is unlikely to produce useful backtest results without manual review.

### 9.3 ID generation

To avoid collisions at scale, tester IDs use 12 hex characters of entropy (48 bits, ~2.8 × 10¹⁴ space):

```sql
'tst_' || substr(md5(random()::text || clock_timestamp()::text), 1, 12)
```

Example: `tst_a3f2e8c14b9d`. Collision probability at 1 million strategies is negligible (well under 10⁻⁶).

The same pattern applies to the API's strategy ID generator in `POST /strategies`.

---

## 10. UI Specification

(Unchanged from v1.0 — see original document for full screen layouts. UI uses the exact same design tokens as `matp-ui-v0.37.html` and the same component vocabulary as `dashboard-ui`.)

Additions in v1.1:

- **Cost estimate widget** in the Run Backtest config panel calls `POST /api/tester/estimate-cost` on parameter change and displays a live cost band: "Estimated cost: $1.92 – $2.88 (gemini-2.5-flash)".
- **Aborted run badge** — when a run has `status = 'aborted_high_failure_rate'`, the simulation screen shows a prominent yellow warning banner above the metrics: "Run aborted: LLM failed on 6.8% of candles. Results below are partial."
- **Queue position** appears on strategy cards if a queued run is waiting: "Queued (2 ahead)".
- **AI config defaulted banner** appears on imported strategies until the user opens and saves the AI config editor.

---

## 11. Docker Integration

### 11.1 `docker-compose.yml` additions

```yaml
  strategy-tester:
    build:
      context: .                                  # repo root context for vendored sync
      dockerfile: strategy-tester/Dockerfile
    ports:
      - "8006:8006"
    environment:
      DATABASE_URL: postgresql://matp:matp@postgres:5432/matp
      REDIS_URL: redis://redis:6379
      GEMINI_API_KEY: ${GEMINI_API_KEY}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      TESTER_DEFAULT_BALANCE: ${TESTER_DEFAULT_BALANCE:-1000.0}
      TESTER_DEFAULT_SLIPPAGE_PCT: ${TESTER_DEFAULT_SLIPPAGE_PCT:-0.05}
      TESTER_DEFAULT_FEE_PCT: ${TESTER_DEFAULT_FEE_PCT:-0.02}
      TESTER_MAX_CONCURRENT_RUNS: ${TESTER_MAX_CONCURRENT_RUNS:-1}
      TESTER_LLM_FAILURE_THRESHOLD: ${TESTER_LLM_FAILURE_THRESHOLD:-0.05}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks: [matp_net]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8006/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  tester-ui:
    build: ./tester-ui
    environment:
      VITE_API_BASE: /api/tester
    depends_on:
      strategy-tester:
        condition: service_healthy
    networks: [matp_net]
    restart: unless-stopped
```

### 11.2 Dockerfile for `strategy-tester`

```dockerfile
FROM python:3.12-slim

WORKDIR /build

# Copy entire repo context (needed for vendored sync verification)
COPY ai-signal-generator/ ./ai-signal-generator/
COPY strategy-tester/ ./strategy-tester/

# Verify vendored files match upstream (build fails on drift)
RUN cd strategy-tester && make check-vendored

# Install dependencies and finalise app dir
WORKDIR /app
RUN cp -r /build/strategy-tester/. /app/
RUN pip install --no-cache-dir -r requirements.txt
RUN rm -rf /build

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8006"]
```

### 11.3 Nginx additions

```nginx
location /api/tester/ {
    proxy_pass http://strategy-tester:8006/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}

location /tester/ {
    proxy_pass http://tester-ui:3001/;
    proxy_set_header Host $host;
}
```

---

## 12. LLM Cost Model

### 12.1 Why static estimates are unreliable

The cost table in v1.0 assumed ~920 input tokens per call. That estimate did not account for:

- The actual `system_prompt` content stored in `public.ai_prompt_templates` (can be 400 – 3,000+ tokens depending on template).
- Custom instructions added per strategy (`custom_instructions` field).
- Position-open prompts (~80 extra tokens for the active position block).
- Structured output schema overhead (varies by provider; ~50–150 tokens for Pydantic-derived schemas).
- News digest tokens when `use_news=true` (~200 tokens, though disabled in v1 sim).

For v1.1, the canonical cost source is **`POST /api/tester/estimate-cost`** (see §8.3), which builds the actual prompt and measures it. The static table below is for rough budgeting only.

### 12.2 Approximate cost table (technical-only baseline)

Assuming ~1,500 input + 300 output tokens per call (median observed in MATP production):

| Model | Cost / call | 1h × 1 mo (720) | 1h × 3 mo (2,160) | 1h × 6 mo (4,320) | 4h × 6 mo (1,080) |
|-------|-------------|----------------|-------------------|-------------------|-------------------|
| `gemini-2.0-flash`  | $0.000203 | $0.15 | $0.44 | **$0.88** | $0.22 |
| `gemini-2.5-flash`  | $0.000405 | $0.29 | $0.87 | **$1.75** | $0.44 |
| `gemini-2.5-pro`    | $0.004875 | $3.51 | $10.53 | **$21.06** | $5.26 |
| `gpt-4o-mini`       | $0.000405 | $0.29 | $0.87 | **$1.75** | $0.44 |
| `gpt-4o`            | $0.006750 | $4.86 | $14.58 | **$29.16** | $7.29 |
| `claude-haiku-4-5`  | $0.002400 | $1.73 | $5.18 | **$10.37** | $2.59 |
| `claude-sonnet-4-6` | $0.009000 | $6.48 | $19.44 | **$38.88** | $9.72 |

**Treat all figures as ±50%.** The `POST /api/tester/estimate-cost` endpoint returns the actual measured number for a given strategy.

### 12.3 Cost guidance

- **Exploratory runs:** `gemini-2.0-flash`. Total experimentation budget for testing a strategy's behaviour: ~$5 over dozens of runs.
- **Validation runs:** `gemini-2.5-flash` or `gpt-4o-mini`. Budget: ~$5–10 per serious candidate strategy.
- **Pre-live final validation:** Same model the strategy will run live with. Budget per validation: see table.
- **Avoid:** Running Claude Sonnet or GPT-4o for exploratory work — you'll burn $30+ per attempt before you know if the prompt design is sensible. Get the strategy logic right on Flash first.

### 12.4 Known cost-affecting factors not modelled

- **Retries** — provider-side retries for transient errors are billed.
- **Structured output reformulation** — if the LLM returns malformed JSON, `with_structured_output` may retry internally.
- **Concurrent runs** — at `TESTER_MAX_CONCURRENT_RUNS > 1`, total cost is the sum; rate-limit failures may inflate it further.

---

## 13. Implementation Order

### Phase 1 — Database schema and vendored sync setup

**Files to create:**
- `db/migrations/_archive/011_tester_schema.sql`
- `Makefile` (in repo root) with `sync-vendored`, `check-vendored`, `build` targets
- `strategy-tester/app/_vendored/.gitkeep`

**Acceptance:**
- `\dt tester.*` shows all 9 tables
- `make sync-vendored` populates `_vendored/` with checksums
- `make check-vendored` passes; modifying a vendored file fails the check

### Phase 2 — strategy-tester service skeleton with init hook

**Files to create:**
- `strategy-tester/Dockerfile` (with `RUN make check-vendored`)
- `strategy-tester/requirements.txt`
- `strategy-tester/app/config.py`
- `strategy-tester/app/database.py` (with `_init_conn` setting `search_path`)
- `strategy-tester/app/main.py` (health endpoint only)

**Acceptance:** `GET /health` returns OK. Pool reconnect (test by `pg_terminate_backend`) still uses correct `search_path` on next query.

### Phase 3 — Strategies CRUD with default AI config

**Files to create:**
- `strategy-tester/app/api/strategies.py`

CRUD endpoints. Strategy creation also writes `tester.ai_strategy_config` and `tester.ai_risk_config` with schema defaults.

### Phase 4 — Historical OHLCV fetcher

**Files to create:**
- `strategy-tester/app/data/historical_ohlcv.py`

**Acceptance:** Fetching 6 months of BTC/USDT 1h candles from Binance returns ~4,320 candles, deduplicated and sorted. Re-fetching reads from `tester.ohlcv_cache` with zero network calls.

### Phase 5 — Graph node variants

**Files to create:**
- `strategy-tester/app/engine/node_ingest_replay.py`
- `strategy-tester/app/engine/node_guard_sim.py`
- `strategy-tester/app/engine/node_dispatch_sim.py`
- `strategy-tester/app/graph/graph_sim.py`

**Acceptance:** Building the graph and invoking against a hardcoded 10-candle window produces a `tester.ai_signal_log` row with `triggered_at` matching the candle timestamp, not wall-clock time.

### Phase 6 — Backtest engine with full causality enforcement

**Files to create:**
- `strategy-tester/app/engine/backtest_engine.py`
- `strategy-tester/app/api/runs.py`

Implements the full loop from §6.1 including: open/close intent deferral, mark-balance equity snapshots, LLM failure tracking, concurrency semaphore.

**Acceptance:** A 1-week BTC-USDT 1h backtest completes in <5 min, produces equity_curve rows for every candle, and computes max_drawdown_pct that exceeds the largest single trade loss (proving mid-trade drawdowns are captured).

### Phase 7 — Cost estimation, results API, migration

**Files to create:**
- `strategy-tester/app/api/results.py`
- `strategy-tester/app/api/migrate.py`
- Cost estimation endpoint in `runs.py`

### Phase 8 — Tester UI

**Files to create:**
- `tester-ui/` (React/Vite app)
- Cost estimate widget, aborted run badge, queue position display

### Phase 9 — Nginx and compose wiring

**Files to modify:**
- `docker-compose.yml`
- `nginx/nginx.conf`

---

## 14. Database Migration SQL

Full content of `db/migrations/_archive/011_tester_schema.sql`:

```sql
-- ============================================================
-- Migration 011: Strategy Tester schema (v1.1)
-- Creates the tester schema and all tester-specific tables.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS tester;

-- ── tester.strategies ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.strategies (
    id                         VARCHAR(100) PRIMARY KEY,
    name                       VARCHAR(100) NOT NULL,
    class                      VARCHAR(100) NOT NULL DEFAULT 'webhook',
    symbol                     VARCHAR(50)  NOT NULL,
    interval                   VARCHAR(10)  NOT NULL DEFAULT '1h',
    platform                   VARCHAR(20)  NOT NULL DEFAULT 'auto',
    enabled                    BOOLEAN      NOT NULL DEFAULT TRUE,
    type                       VARCHAR(20)  NOT NULL DEFAULT 'internal',
    config_yaml                TEXT         NOT NULL DEFAULT '',
    config                     JSONB        NOT NULL DEFAULT '{}',
    webhook_secret             VARCHAR(255) NOT NULL DEFAULT encode(gen_random_bytes(16), 'hex'),
    webhook_enabled            BOOLEAN               DEFAULT FALSE,
    description                TEXT,
    platform_override          VARCHAR(20),
    max_daily_signals          INTEGER               DEFAULT 500,
    max_position_size          NUMERIC               DEFAULT 1.0,
    max_leverage               INTEGER               DEFAULT 10,
    max_daily_drawdown_percent NUMERIC               DEFAULT 20,
    capital_allocation_percent NUMERIC               DEFAULT 100,
    signals_today              INTEGER               DEFAULT 0,
    pnl_today                  NUMERIC               DEFAULT 0,
    pnl_total                  NUMERIC               DEFAULT 0,
    win_count                  INTEGER               DEFAULT 0,
    loss_count                 INTEGER               DEFAULT 0,
    last_signal_at             TIMESTAMPTZ,
    tags                       TEXT[]                DEFAULT '{}',
    account_id                 VARCHAR(100),
    pair_id                    INTEGER,
    allow_quote_variants       BOOLEAN      NOT NULL DEFAULT FALSE,
    allow_cross_charting       BOOLEAN      NOT NULL DEFAULT FALSE,
    default_leverage           INTEGER      NOT NULL DEFAULT 1,
    margin_mode                VARCHAR(10)  NOT NULL DEFAULT 'isolated',
    is_deleted                 BOOLEAN      NOT NULL DEFAULT FALSE,
    blofin_token               TEXT,
    source_matp_id             VARCHAR(100),
    created_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── tester.ai_strategy_config ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.ai_strategy_config (
    id                        BIGSERIAL PRIMARY KEY,
    strategy_id               VARCHAR(100) NOT NULL REFERENCES tester.strategies(id) ON DELETE CASCADE,
    template_id               VARCHAR(100) NOT NULL DEFAULT 'trend_following',
    llm_provider              VARCHAR(50)  NOT NULL DEFAULT 'google',
    llm_model                 VARCHAR(100) NOT NULL DEFAULT 'gemini-2.0-flash',
    use_technical             BOOLEAN      NOT NULL DEFAULT TRUE,
    use_fear_greed            BOOLEAN      NOT NULL DEFAULT FALSE,
    use_funding_rate          BOOLEAN      NOT NULL DEFAULT FALSE,
    use_open_interest         BOOLEAN      NOT NULL DEFAULT FALSE,
    use_news                  BOOLEAN      NOT NULL DEFAULT FALSE,
    use_btc_dominance         BOOLEAN      NOT NULL DEFAULT FALSE,
    use_macro                 BOOLEAN      NOT NULL DEFAULT FALSE,
    indicators                TEXT[]       NOT NULL DEFAULT '{RSI,MACD,EMA50,EMA200,BB,VWAP}',
    lookback_days             INTEGER      NOT NULL DEFAULT 90,
    confidence_threshold      NUMERIC      NOT NULL DEFAULT 0.72,
    cooldown_entry_minutes    INTEGER      NOT NULL DEFAULT 240,
    cooldown_increase_minutes INTEGER      NOT NULL DEFAULT 60,
    cooldown_stop_adj_minutes INTEGER      NOT NULL DEFAULT 30,
    interval_no_position      VARCHAR(10)  NOT NULL DEFAULT '4h',
    interval_position_open    VARCHAR(10)  NOT NULL DEFAULT '1h',
    interval_at_risk          VARCHAR(10)  NOT NULL DEFAULT '15m',
    at_risk_threshold_pct     NUMERIC      NOT NULL DEFAULT 3.0,
    dry_run                   BOOLEAN      NOT NULL DEFAULT TRUE,
    emergency_exit_pct        NUMERIC               DEFAULT 5.0,
    custom_instructions       TEXT,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id)
);

-- ── tester.ai_risk_config ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.ai_risk_config (
    id                     BIGSERIAL PRIMARY KEY,
    strategy_id            VARCHAR(100) NOT NULL REFERENCES tester.strategies(id) ON DELETE CASCADE,
    max_position_size_pct  NUMERIC      NOT NULL DEFAULT 5.0,
    max_daily_loss_pct     NUMERIC      NOT NULL DEFAULT 3.0,
    max_drawdown_pct       NUMERIC      NOT NULL DEFAULT 8.0,
    max_concurrent_trades  INTEGER      NOT NULL DEFAULT 1,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id)
);

-- ── tester.backtest_runs ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.backtest_runs (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id         VARCHAR(100) NOT NULL REFERENCES tester.strategies(id),
    timeframe           VARCHAR(10)  NOT NULL,
    date_from           DATE         NOT NULL,
    date_to             DATE         NOT NULL,
    lookback_days       INTEGER      NOT NULL DEFAULT 90,
    initial_balance     NUMERIC      NOT NULL DEFAULT 1000.0,
    slippage_pct        NUMERIC      NOT NULL DEFAULT 0.05,
    fee_pct             NUMERIC      NOT NULL DEFAULT 0.02,
    status              VARCHAR(40)  NOT NULL DEFAULT 'pending',
    candles_processed   INTEGER               DEFAULT 0,
    total_candles       INTEGER,
    total_signals       INTEGER,
    gate_passed         INTEGER,
    llm_failures        INTEGER               DEFAULT 0,
    llm_failure_rate    NUMERIC(5,2),
    total_trades        INTEGER,
    winning_trades      INTEGER,
    losing_trades       INTEGER,
    win_rate            NUMERIC(5,2),
    total_pnl           NUMERIC(18,8),
    total_pnl_pct       NUMERIC(8,4),
    profit_factor       NUMERIC(10,4),
    max_drawdown_pct    NUMERIC(8,4),
    sharpe_approx       NUMERIC(8,4),
    long_count          INTEGER,
    short_count         INTEGER,
    avg_win             NUMERIC(18,8),
    avg_loss            NUMERIC(18,8),
    largest_win         NUMERIC(18,8),
    largest_loss        NUMERIC(18,8),
    total_fees_paid     NUMERIC(18,8),
    llm_provider        VARCHAR(50),
    llm_model           VARCHAR(100),
    estimated_cost_usd  NUMERIC(10,6),
    actual_tokens_used  INTEGER,
    error_message       TEXT,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CHECK (status IN ('pending','running','completed','failed','cancelled','aborted_high_failure_rate'))
);

CREATE INDEX IF NOT EXISTS tester_runs_strategy_idx ON tester.backtest_runs (strategy_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tester_runs_status_idx   ON tester.backtest_runs (status);

-- ── tester.orders ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.orders (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    backtest_run_id   UUID        NOT NULL REFERENCES tester.backtest_runs(id) ON DELETE CASCADE,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    candle_timestamp  TIMESTAMPTZ NOT NULL,
    symbol            VARCHAR(50) NOT NULL,
    side              VARCHAR(10) NOT NULL,
    signal            VARCHAR(20) NOT NULL,
    order_type        VARCHAR(20) NOT NULL DEFAULT 'market',
    size              NUMERIC     NOT NULL,
    price             NUMERIC,
    leverage          INTEGER,
    margin_mode       VARCHAR(10),
    tp_price          NUMERIC,
    sl_price          NUMERIC,
    platform          VARCHAR(20) NOT NULL DEFAULT 'simulated',
    strategy_id       VARCHAR(100) NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'filled',
    actual_fill_price NUMERIC,
    pnl               NUMERIC,
    fee               NUMERIC,
    raw_webhook       JSONB        NOT NULL DEFAULT '{}',
    signal_source     VARCHAR(100) NOT NULL DEFAULT 'ai_signal_generator',
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tester_orders_run_idx      ON tester.orders (backtest_run_id);
CREATE INDEX IF NOT EXISTS tester_orders_strategy_idx ON tester.orders (strategy_id);

-- ── tester.strategy_positions ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.strategy_positions (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    backtest_run_id   UUID        NOT NULL REFERENCES tester.backtest_runs(id) ON DELETE CASCADE,
    strategy_id       VARCHAR(100) NOT NULL,
    exchange          VARCHAR(20)  NOT NULL DEFAULT 'simulated',
    symbol            VARCHAR(50)  NOT NULL,
    side              VARCHAR(10)  NOT NULL,
    entry_price       NUMERIC      NOT NULL,
    current_price     NUMERIC,
    closing_price     NUMERIC,
    size              NUMERIC      NOT NULL,
    leverage          INTEGER,
    margin_mode       VARCHAR(20),
    pnl_unrealized    NUMERIC,
    pnl_realized      NUMERIC      DEFAULT 0,
    fee_open          NUMERIC      DEFAULT 0,
    fee_close         NUMERIC      DEFAULT 0,
    status            VARCHAR(20)  DEFAULT 'open',
    opening_order_id  UUID REFERENCES tester.orders(id) ON DELETE SET NULL,
    closing_order_id  UUID REFERENCES tester.orders(id) ON DELETE SET NULL,
    close_reason      VARCHAR(50),
    opened_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_at         TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tester_pos_run_idx      ON tester.strategy_positions (backtest_run_id);
CREATE INDEX IF NOT EXISTS tester_pos_strategy_idx ON tester.strategy_positions (strategy_id, status);

-- ── tester.ai_signal_log ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.ai_signal_log (
    id                     BIGSERIAL PRIMARY KEY,
    backtest_run_id        UUID         REFERENCES tester.backtest_runs(id) ON DELETE CASCADE,
    strategy_id            VARCHAR(100) NOT NULL,
    triggered_at           TIMESTAMPTZ  NOT NULL,         -- candle close timestamp, NOT NOW()
    trigger_reason         VARCHAR(50),
    cycle_interval         VARCHAR(10),
    prompt_template        VARCHAR(100),
    data_sources_used      TEXT[]       DEFAULT '{}',
    context_tokens         INTEGER,
    proposed_action        VARCHAR(30),
    confidence             NUMERIC,
    reasoning              TEXT,
    gate_passed            BOOLEAN      NOT NULL DEFAULT FALSE,
    gate_rejection_reason  VARCHAR(50),
    dry_run                BOOLEAN      NOT NULL DEFAULT TRUE,
    llm_provider           VARCHAR(50),
    llm_model              VARCHAR(100),
    webhook_fired          BOOLEAN               DEFAULT FALSE,
    webhook_status         INTEGER,
    order_id               UUID REFERENCES tester.orders(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS tester_signal_log_run_idx      ON tester.ai_signal_log (backtest_run_id);
CREATE INDEX IF NOT EXISTS tester_signal_log_strategy_idx ON tester.ai_signal_log (strategy_id, triggered_at DESC);
-- Critical for cooldown checks scoped per run:
CREATE INDEX IF NOT EXISTS tester_signal_log_cooldown_idx
    ON tester.ai_signal_log (backtest_run_id, strategy_id, proposed_action, gate_passed, triggered_at DESC);

-- ── tester.ohlcv_cache ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tester.ohlcv_cache (
    id          BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(20)  NOT NULL,
    timeframe   VARCHAR(10)  NOT NULL,
    exchange    VARCHAR(30)  NOT NULL DEFAULT 'binance',
    candle_ts   TIMESTAMPTZ  NOT NULL,
    open        NUMERIC      NOT NULL,
    high        NUMERIC      NOT NULL,
    low         NUMERIC      NOT NULL,
    close       NUMERIC      NOT NULL,
    volume      NUMERIC      NOT NULL,
    fetched_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, timeframe, exchange, candle_ts)
);

CREATE INDEX IF NOT EXISTS tester_ohlcv_lookup_idx ON tester.ohlcv_cache (symbol, timeframe, exchange, candle_ts);

-- ── tester.equity_curve (per-candle, includes mark_balance) ──────────────────

CREATE TABLE IF NOT EXISTS tester.equity_curve (
    id               BIGSERIAL PRIMARY KEY,
    backtest_run_id  UUID        NOT NULL REFERENCES tester.backtest_runs(id) ON DELETE CASCADE,
    candle_ts        TIMESTAMPTZ NOT NULL,
    realized_balance NUMERIC     NOT NULL,
    mark_balance     NUMERIC     NOT NULL,
    trade_pnl        NUMERIC,
    drawdown_pct     NUMERIC,
    UNIQUE (backtest_run_id, candle_ts)
);

CREATE INDEX IF NOT EXISTS tester_equity_run_idx ON tester.equity_curve (backtest_run_id, candle_ts);

-- ── Triggers ──────────────────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tester_strategies_updated_at') THEN
        CREATE TRIGGER update_tester_strategies_updated_at
            BEFORE UPDATE ON tester.strategies
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tester_runs_updated_at') THEN
        CREATE TRIGGER update_tester_runs_updated_at
            BEFORE UPDATE ON tester.backtest_runs
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tester_positions_updated_at') THEN
        CREATE TRIGGER update_tester_positions_updated_at
            BEFORE UPDATE ON tester.strategy_positions
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tester_strategy_config_updated_at') THEN
        CREATE TRIGGER update_tester_strategy_config_updated_at
            BEFORE UPDATE ON tester.ai_strategy_config
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_tester_risk_config_updated_at') THEN
        CREATE TRIGGER update_tester_risk_config_updated_at
            BEFORE UPDATE ON tester.ai_risk_config
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
```

---

## 15. Verification Checklist

### Database
- [ ] `\dt tester.*` lists all tables
- [ ] Migration is idempotent
- [ ] All FKs to `tester.backtest_runs` use `ON DELETE CASCADE`; verified by attempting `DELETE FROM tester.backtest_runs WHERE id = $x` and confirming orders/positions/signals/equity rows are removed
- [ ] FKs to `tester.orders` from positions use `ON DELETE SET NULL` (not cascade — position should survive order deletion)

### Pool and search path
- [ ] `_init_conn` is called on every new connection; verified by `pg_terminate_backend()` and immediately re-querying — search_path is correct
- [ ] CI grep check fails the build when an `INSERT INTO strategies` (unqualified) appears in tester source

### Vendored sync
- [ ] `make sync-vendored` populates `_vendored/` and writes CHECKSUMS
- [ ] Modifying a vendored file fails `make check-vendored`
- [ ] Modifying an upstream file without re-syncing fails `make check-vendored` (because CHECKSUMS is now stale)
- [ ] Dockerfile fails to build when checksums don't match
- [ ] Import path rewrites in vendored files resolve correctly inside the tester container

### Engine causality
- [ ] A position cannot open and close on the same candle (assertion fires if it would)
- [ ] SL/TP check on candle N never uses position opened on candle N
- [ ] When both SL and TP would hit on the same candle, the closer level wins
- [ ] Indicators in `node_ingest_replay` are computed only from candles ≤ current decision candle

### Cooldown
- [ ] Two `open_long` signals separated by < cooldown_minutes (in simulated time) → second is rejected with `cooldown_active`
- [ ] Two runs over the same strategy don't share cooldown state (verified via `backtest_run_id` scope)
- [ ] `triggered_at` in `tester.ai_signal_log` matches candle timestamps, never wall-clock time

### Balance compounding
- [ ] After a +5% trade on initial $1000, next position sizes off $1050
- [ ] After several consecutive winners, `current_balance` grows compoundingly, not linearly

### Equity curve and drawdown
- [ ] Every candle in the run has an `equity_curve` row
- [ ] `mark_balance` reflects unrealized P&L mid-trade
- [ ] `max_drawdown_pct` exceeds the largest single closed trade's drawdown (proves intra-trade dd is captured)

### LLM failure handling
- [ ] Run with intentionally broken API key transitions to `aborted_high_failure_rate` after > 5% failure rate
- [ ] `llm_failures` and `llm_failure_rate` populated on aborted runs
- [ ] UI shows banner for aborted runs

### Concurrency
- [ ] `TESTER_MAX_CONCURRENT_RUNS=1`: submitting a second run while one is running puts it in `pending` with `queue_position: 1`
- [ ] Setting `=2` and submitting 3 runs queues only the third

### Cost estimation
- [ ] `POST /api/tester/estimate-cost` returns a range that varies with `template_id` and `custom_instructions` length
- [ ] Estimate is shown in the UI's Run Backtest panel before submission
- [ ] Actual `estimated_cost_usd` on completed run is within the estimate range ±30%

### Migration
- [ ] `from-matp` with an AI-configured source strategy → `ai_config_imported: true`
- [ ] `from-matp` with a webhook-only source strategy → `ai_config_imported: false`, defaults inserted
- [ ] UI yellow banner shown for defaulted strategies
- [ ] `to-matp` creates `public.strategies` row with `enabled = false`
- [ ] Both directions are transactional; partial failure leaves source unchanged

### Strategy IDs
- [ ] Generated tester IDs match pattern `tst_[a-f0-9]{12}`
- [ ] Inserting 10,000 strategies produces no collisions

### Partial close
- [ ] LLM signal with `action: 'partial_close'` is logged but never opens/closes a position in v1
- [ ] `gate_rejection_reason` = `partial_close_not_supported_in_sim`

### UI
- [ ] All MATP design tokens applied
- [ ] Cost estimate updates as user changes date range / model in Run Backtest panel
- [ ] Aborted run banner displays prominently
- [ ] Queue position visible on strategy cards
- [ ] AI config defaulted banner shown for imported strategies

### Docker
- [ ] `docker compose up -d strategy-tester` starts without errors
- [ ] Nginx routes `/api/tester/` and `/tester/` correctly
- [ ] Backtest run does not produce any writes to `public.*` tables (verified via `pg_stat_user_tables` snapshot)
- [ ] OHLCV cache survives container restart (data is in named volume `postgres_data`)
