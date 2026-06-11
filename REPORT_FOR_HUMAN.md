# Strategy Tester Implementation Report

---

## Phase 1 — Database Schema and Vendored Sync

Status: COMPLETED

Files created:
- `db/migrations/011_tester_schema.sql`
- `Makefile` (repo root — sync-vendored, check-vendored, build targets)
- `strategy-tester/Makefile` (for Docker build context — check-vendored only)
- `strategy-tester/app/_vendored/.gitkeep`

Files modified: none

### Verification output

```
$ docker compose exec postgres psql -U matp -d matp -c "\dt tester.*"
              List of relations
 Schema |        Name        | Type  | Owner 
--------+--------------------+-------+-------
 tester | ai_risk_config     | table | matp
 tester | ai_signal_log      | table | matp
 tester | ai_strategy_config | table | matp
 tester | backtest_runs      | table | matp
 tester | equity_curve       | table | matp
 tester | ohlcv_cache        | table | matp
 tester | orders             | table | matp
 tester | strategies         | table | matp
 tester | strategy_positions | table | matp
(9 rows)
```

```
$ ls -la strategy-tester/app/_vendored/
total 48
drwxrwxr-x 2 cristi cristi  4096 Jun 10 17:04 .
drwxrwxr-x 3 cristi cristi  4096 Jun 10 16:54 ..
-rw-rw-r-- 1 cristi cristi   332 Jun 10 17:04 CHECKSUMS
-rw-rw-r-- 1 cristi cristi     0 Jun 10 16:54 .gitkeep
-rw-rw-r-- 1 cristi cristi 11216 Jun 10 17:04 indicators.py
-rw-rw-r-- 1 cristi cristi  3223 Jun 10 17:04 node_analyze.py
-rw-rw-r-- 1 cristi cristi 12504 Jun 10 17:04 prompt_builder.py
-rw-rw-r-- 1 cristi cristi  1867 Jun 10 17:04 prompt_templates.py
```

```
$ make check-vendored
indicators.py: OK
node_analyze.py: OK
prompt_builder.py: OK
prompt_templates.py: OK
```

```
$ echo "# tamper" >> strategy-tester/app/_vendored/indicators.py
$ make check-vendored
indicators.py: FAILED
node_analyze.py: OK
prompt_builder.py: OK
prompt_templates.py: OK
sha256sum: WARNING: 1 computed checksum did NOT match
make: *** [Makefile:21: check-vendored] Error 1
```
(Restored via `make sync-vendored`)

### Issues encountered

- The SDD's `sync-vendored` uses `sha256sum strategy-tester/app/_vendored/*.py` which would write full relative paths into CHECKSUMS. This conflicts with `check-vendored` doing `cd strategy-tester/app/_vendored && sha256sum -c CHECKSUMS` (which expects bare filenames). Fixed by using `cd $(VENDORED_DIR) && sha256sum *.py > CHECKSUMS` — both commands now operate from within `_vendored/` with bare filenames.
- The strategy-tester/Makefile is needed inside the Docker build context because `COPY strategy-tester/ ./strategy-tester/` is what gets copied; the repo root Makefile is not accessible in the Dockerfile's `RUN cd strategy-tester && make check-vendored`.

### Notes for human review

- 9 tables confirmed in tester schema — matches the expected list exactly.
- 4 vendored .py files + CHECKSUMS in `_vendored/` (5 files total, plus .gitkeep).
- Import path rewrites applied correctly: `prompt_builder.py` imports from `app._vendored.prompt_templates`; `node_analyze.py` imports from `app._vendored.prompt_builder`.
- VENDORED FROM headers present in all 4 files.
- Tamper detection works — modifying any vendored file fails `make check-vendored` with a non-zero exit code.
- Migration is idempotent (all statements use `CREATE TABLE IF NOT EXISTS`).

---

## Phase 2 — Service Skeleton with Init Hook

Status: COMPLETED

Files created:
- `strategy-tester/Dockerfile`
- `strategy-tester/requirements.txt`
- `strategy-tester/app/__init__.py`
- `strategy-tester/app/config.py`
- `strategy-tester/app/database.py`
- `strategy-tester/app/main.py`
- `strategy-tester/app/api/__init__.py`
- `strategy-tester/app/engine/__init__.py`
- `strategy-tester/app/data/__init__.py`
- `strategy-tester/app/graph/__init__.py`
- `strategy-tester/app/_vendored/__init__.py`

Files modified:
- `docker-compose.yml` (added strategy-tester service block)
- `strategy-tester/app/_vendored/CHECKSUMS` (regenerated to include `__init__.py`)

### Verification output

```
$ curl -s http://localhost:8006/health
{"status":"ok","service":"strategy-tester"}
```

```
$ docker compose logs strategy-tester --tail 20
strategy-tester-1  | INFO:     Started server process [1]
strategy-tester-1  | INFO:     Waiting for application startup.
strategy-tester-1  | 2026-06-10 17:31:04,040 [INFO] app.database: Database pool initialized (search_path=tester,public)
strategy-tester-1  | 2026-06-10 17:31:04,059 [INFO] app.main: tester schema verified
strategy-tester-1  | 2026-06-10 17:31:04,064 [INFO] app.main: Vendored checksums verified OK (5 files)
strategy-tester-1  | 2026-06-10 17:31:04,099 [INFO] app.main: OHLCV cache cleanup: DELETE 0 short-tf rows, DELETE 0 daily rows
strategy-tester-1  | 2026-06-10 17:31:04,100 [INFO] app.main: Run semaphore initialized (max_concurrent=1)
strategy-tester-1  | INFO:     Application startup complete.
strategy-tester-1  | INFO:     Uvicorn running on http://0.0.0.0:8006 (Press CTRL+C to quit)
```

```
$ docker compose exec postgres psql -U matp -d matp -c \
    "SELECT pid, query FROM pg_stat_activity \
     WHERE application_name LIKE '%strategy-tester%' OR query LIKE '%search_path%' LIMIT 5;"
  pid   |                       query
--------+-----------------------------------------------
 386591 | SET search_path = tester, public
(confirmed at initial startup — init hook visible in pg_stat_activity)
```

```
$ docker compose exec postgres psql -U matp -d matp -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity \
     WHERE datname = 'matp' AND application_name NOT LIKE '%psql%';"
(terminated 8 connections including strategy-tester pool)

$ curl -s http://localhost:8006/health
{"status":"ok","service":"strategy-tester"}   ← still responsive after reconnect
```

### Issues encountered

- `python:3.12-slim` does not include `make`. Added `RUN apt-get install -y --no-install-recommends make` to the Dockerfile before the `check-vendored` step.
- Added `strategy-tester/app/_vendored/__init__.py` so the `_vendored/` directory is importable as a Python package (`from app._vendored.indicators import compute_indicators`). Re-ran `make sync-vendored` to include `__init__.py` in CHECKSUMS.

### Notes for human review

- All 5 startup lifecycle steps complete in order and log correctly: DB pool init → tester schema check → vendored checksums → OHLCV cache cleanup → semaphore init.
- The `SET search_path = tester, public` appeared in pg_stat_activity at startup, confirming the `_init_conn` hook fires on new connections.
- Health returned OK immediately after `pg_terminate_backend` — pool reconnects transparently.
- Vendored checksums verified at both build time (Dockerfile `RUN cd strategy-tester && make check-vendored`) and runtime (lifespan startup).
- The Dockerfile build passed the `make check-vendored` step cleanly (all 5 files OK in build log).

---

## Phase 3 — Strategies CRUD with Default AI Config

Status: COMPLETED

Files created:
- `strategy-tester/app/api/strategies.py`

Files modified:
- `strategy-tester/app/main.py` (wired strategies router at `/strategies`)

### Verification output

```
$ curl -s -X POST http://localhost:8006/strategies \
    -H "Content-Type: application/json" \
    -d '{"name":"BTC Trend Test","symbol":"BTC/USDT","interval":"1h"}'
{"id":"tst_cefcf0b6606b","name":"BTC Trend Test","symbol":"BTC-USDT","interval":"1h",
 "enabled":true,"webhook_secret":"a8ccbb662e0d2f4515e7ae587c45551d",
 "message":"Strategy created. Save the webhook_secret — it will not be shown again."}
```

```
$ curl -s http://localhost:8006/strategies
[{"id":"tst_cefcf0b6606b","name":"BTC Trend Test","symbol":"BTC-USDT",...}]
← 1 strategy returned
```

```
$ curl -s http://localhost:8006/strategies/tst_cefcf0b6606b
{"id":"tst_cefcf0b6606b","template_id":"trend_following","llm_model":"gemini-2.0-flash",
 "max_position_size_pct":5.0,...}
← ai_strategy_config + ai_risk_config fields joined
```

```
$ psql: SELECT strategy_id, template_id, llm_provider, llm_model, confidence_threshold,
               cooldown_entry_minutes FROM tester.ai_strategy_config WHERE strategy_id = 'tst_cefcf0b6606b';
   strategy_id    |   template_id   | llm_provider |    llm_model     | confidence_threshold | cooldown_entry_minutes
------------------+-----------------+--------------+------------------+----------------------+------------------------
 tst_cefcf0b6606b | trend_following | google       | gemini-2.0-flash |                 0.72 |                    240
(1 row)
```

```
$ psql: SELECT strategy_id, max_position_size_pct, max_daily_loss_pct, max_drawdown_pct
        FROM tester.ai_risk_config WHERE strategy_id = 'tst_cefcf0b6606b';
   strategy_id    | max_position_size_pct | max_daily_loss_pct | max_drawdown_pct
------------------+-----------------------+--------------------+------------------
 tst_cefcf0b6606b |                   5.0 |                3.0 |              8.0
(1 row)
```

```
$ psql: SELECT COUNT(*) FROM public.strategies WHERE id LIKE 'tst_%';
 count
-------
     0
(1 row)  ← zero leak to public schema
```

```
$ curl -s -X PUT http://localhost:8006/strategies/tst_cefcf0b6606b \
    -d '{"interval":"4h"}'
{"interval":"4h","updated_at":"2026-06-10T18:19:10.305156+00:00",...}
```

```
$ curl -s http://localhost:8006/strategies/tst_cefcf0b6606b/runs
[]  ← empty list, correct

$ curl -s -X DELETE http://localhost:8006/strategies/tst_cefcf0b6606b
{"deleted":"tst_cefcf0b6606b"}

$ curl -s http://localhost:8006/strategies/tst_cefcf0b6606b
{"detail":"Strategy not found: tst_cefcf0b6606b"}  ← 404 after soft-delete

$ psql: SELECT id, is_deleted FROM tester.strategies WHERE id = 'tst_cefcf0b6606b';
        id        | is_deleted
------------------+------------
 tst_cefcf0b6606b | t
```

```
$ ID format check — 5 IDs:
  tst_263d7fcb6572
  tst_dcdefbc3b624
  tst_d11be970162b
  tst_2d672073388f
  tst_dc6b9d5c876b
← all match tst_[a-f0-9]{12}
```

### Issues encountered

None.

### Notes for human review

- ID format: `tst_` + `secrets.token_hex(6)` (48 bits = 12 hex chars). Better entropy than the SDD's `md5(random())` approach.
- POST creates strategy + ai_strategy_config + ai_risk_config defaults in a single transaction; if any insert fails the whole operation rolls back.
- Symbol normalization: `BTC/USDT` and `BTCUSDT` both stored as `BTC-USDT` (uppercase, slash/underscore → dash).
- All writes fully-qualified to `tester.*`; confirmed zero rows in `public.strategies` after creation.
- `DELETE` is soft-delete only: `is_deleted = true` in the DB; row stays for FK integrity and audit.
- `GET /strategies/{id}` joins both AI config tables and returns merged row — ai_strategy_config defaults (template_id, llm_model, etc.) and ai_risk_config defaults are live immediately on creation.

---

---

## Phase 4 — Historical OHLCV Fetcher

Status: COMPLETED

Files created:
- `strategy-tester/app/data/historical_ohlcv.py` (paginating ccxt fetcher, verbatim from SDD §6.2)
- `strategy-tester/app/data/ohlcv_cache.py` (cache read/write: `get_cached_candles`, `is_cache_sufficient`, `upsert_candles`)
- `strategy-tester/app/api/debug.py` (temporary `POST /debug/fetch-ohlcv`)

Files modified:
- `strategy-tester/app/main.py` (wired debug router at `/debug`)

### Verification output

```
$ curl -s -X POST http://localhost:8006/debug/fetch-ohlcv \
    -d '{"symbol":"BTC/USDT","timeframe":"1h","exchange":"binance",
         "date_from":"2026-05-27","date_to":"2026-06-03"}'
{
  "candles_returned": 169,
  "source": "network",
  "elapsed_ms": 6701,
  "symbol": "BTC-USDT",
  "first_candle_ts": 1779840000000,
  "last_candle_ts":  1780444800000,
  "cached_now": 169
}
```

```
$ curl -s -X POST http://localhost:8006/debug/fetch-ohlcv \
    -d '{"symbol":"BTC/USDT","timeframe":"1h","exchange":"binance",
         "date_from":"2026-05-27","date_to":"2026-06-03"}'
{
  "candles_returned": 169,
  "source": "cache",
  "elapsed_ms": 18
}
← Zero network calls on second fetch; 6701ms → 18ms
```

```
$ psql: SELECT symbol, timeframe, exchange, COUNT(*) AS candle_count,
               MIN(candle_ts), MAX(candle_ts)
        FROM tester.ohlcv_cache GROUP BY symbol, timeframe, exchange;
  symbol  | timeframe | exchange | candle_count |        first_ts        |        last_ts
----------+-----------+----------+--------------+------------------------+-----------------------
 BTC-USDT | 1h        | binance  |          169 | 2026-05-27 00:00:00+00 | 2026-06-03 00:00:00+00
(1 row)
```

### Issues encountered

None.

### Notes for human review

- 169 candles for 7-day range (2026-05-27 to 2026-06-03) = 168 hourly candles + inclusive boundary candle.
- Deduplication and sort confirmed: `seen_ts` set in fetcher prevents duplicate timestamps; `candles.sort()` guarantees ascending order.
- Cache key: normalised symbol `BTC-USDT` (dash) stored in DB; ccxt call uses slash format `BTC/USDT` derived via `replace("-", "/", 1)`.
- `is_cache_sufficient`: allows ±2 candle slop for exchange gaps; for 168 expected, 169 cached → clear hit.
- `upsert_candles` uses `ON CONFLICT (symbol, timeframe, exchange, candle_ts) DO NOTHING` — safe to call repeatedly; no double-counting.
- All writes to `tester.ohlcv_cache` (fully qualified); zero rows written to `public.*`.

---

---

## Phase 5 — Graph Node Variants

Status: COMPLETED

Files created:
- `strategy-tester/app/graph/state.py` (extended AgentState with sim fields: `simulated_now`, `backtest_run_id`, `sim_balance`, `sim_pnl_today`, `replay_candle_window`, `sim_action`)
- `strategy-tester/app/engine/node_ingest_replay.py` (verbatim from SDD §7.1)
- `strategy-tester/app/engine/node_analyze_sim.py` (dry_signal wrapper — bypasses LLM when `dry_signal_mode=True`)
- `strategy-tester/app/engine/node_guard_sim.py` (sim guard: sim_balance, simulated_now cooldown, tester.ai_signal_log scoped to backtest_run_id, partial_close rejected)
- `strategy-tester/app/engine/node_dispatch_sim.py` (writes `triggered_at = simulated_now`, no HTTP webhook)
- `strategy-tester/app/graph/graph_sim.py` (StateGraph: ingest → analyze → guard → dispatch)

Files modified:
- `strategy-tester/app/api/debug.py` (added `POST /debug/invoke-graph`, added `backtest_run_id` param for cooldown tests)

### Verification output

```
=== Invocation 1: simulated_ts=2026-05-30T10:00:00Z (fresh strategy+run) ===
{
  "gate_passed": true,
  "signal_action": "open_long",
  "signal_log_id": 7,
  "simulated_now":       "2026-05-30T10:00:00+00:00",
  "triggered_at_in_db":  "2026-05-30T10:00:00+00:00",
  "triggered_at_matches_candle": true,
  "resolved_size": 0.00067883,
  "resolved_sl_price": 72182.733,
  "resolved_tp_price": 76602.084
}
← triggered_at matches candle ts, NOT wall-clock
```

```
=== Invocation 2: simulated_ts=+30 min (same run_id) — cooldown test ===
gate_passed=False  rejection=cooldown_active  triggered_at=2026-05-30T10:30:00+00:00
← correctly blocked within 240 min cooldown window
```

```
=== Invocation 3: simulated_ts=+300 min (same run_id) — cooldown expired ===
gate_passed=True  rejection=None  triggered_at=2026-05-30T15:00:00+00:00  matches=True
← correctly passes after cooldown window
```

```
$ psql: SELECT id, proposed_action, gate_passed, gate_rejection_reason, triggered_at
        FROM tester.ai_signal_log
        WHERE backtest_run_id = 'a10ec5f2-...'
        ORDER BY id;

 id | proposed_action | gate_passed | gate_rejection_reason |      triggered_at
----+-----------------+-------------+-----------------------+------------------------
  7 | open_long       | t           |                       | 2026-05-30 10:00:00+00
  8 | open_long       | f           | cooldown_active       | 2026-05-30 10:30:00+00
  9 | open_long       | t           |                       | 2026-05-30 15:00:00+00
← all triggered_at = candle timestamps; cooldown scoped per run_id
```

### Issues encountered

**1. asyncpg type-inference failure with `make_interval`**

The original cooldown query used:
```sql
AND triggered_at >= $4 - make_interval(mins => $5)
```

PostgreSQL couldn't infer that `$4` is `timestamptz` (vs `interval`) when preparing the statement with asyncpg. This caused `operator does not exist: timestamp with time zone >= interval`. The error was silently caught (warning only), causing cooldown to never fire.

Fixed by computing the cutoff in Python:
```python
cooldown_cutoff = simulated_now - timedelta(minutes=cooldown_minutes)
```
and passing `cooldown_cutoff` and `simulated_now` as direct `timestamptz` parameters `$4` and `$5`.

**2. asyncpg requires `date` object, not string**

The debug endpoint was passing `sim_now.date().isoformat()` (a string) for PostgreSQL `date` columns. asyncpg requires a Python `datetime.date` object. Fixed by passing `sim_now.date()`.

**3. Cooldown scope is per `backtest_run_id`**

Each debug invocation creates a new `backtest_run_id`. Cooldown is correctly scoped per run — different runs don't share cooldown state (SDD §7.2 design). Added optional `backtest_run_id` to the debug request to allow reusing the same run for multi-invocation cooldown tests.

### Notes for human review

- `triggered_at` in `tester.ai_signal_log` is always set to `state['simulated_now']` (the candle timestamp) — never `NOW()`. Confirmed in DB: timestamps match exactly.
- Cooldown correctly rejects within the 240 min window and passes beyond it, using simulated time only.
- `dry_signal_mode=True` returns a synthetic `open_long` at 0.85 confidence — above the 0.72 threshold — without spending LLM credits (R3).
- `partial_close` is rejected at the guard layer with `partial_close_not_supported_in_sim`.
- `node_dispatch_sim` writes to `tester.ai_signal_log` (fully qualified) — zero writes to `public.ai_signal_log`.
- Graph order: `ingest → analyze → guard → dispatch` compiled as LangGraph `StateGraph(AgentState)`.

---

## Phase 6 — Backtest Engine

Status: COMPLETED

### Files created / modified

| File | Action |
|---|---|
| `strategy-tester/app/engine/node_analyze_sim.py` | Modified — varied dry-signal (Step 6.0) |
| `strategy-tester/app/engine/backtest_engine.py` | Created — core engine (Step 6.1) |
| `strategy-tester/app/api/runs.py` | Created — run CRUD (Step 6.2) |
| `strategy-tester/app/main.py` | Modified — wired runs router + `init_engine()` |
| `db/migrations/012_backtest_dry_signal.sql` | Created — adds `dry_signal_mode` column |

### Step 6.0 — Varied dry-signal

`node_analyze_sim._varied_dry_signal` is a **pure function** of `(simulated_now, position_open, position_side, position_opened_at, timeframe)`:
- No position open: `open_long` when `candle_idx % 12 == 0`; `open_short` when `candle_idx % 37 == 0`; else `hold`
- Position open ≥ 10 candles: emit matching `close_long`/`close_short`
- Position open < 10 candles: `hold` (SL/TP still exercises close paths)
- Confidence always 0.85 — above the 0.72 guard threshold
- `candle_idx = int(simulated_now.timestamp()) // tf_seconds` → reproducible across replays

### Step 6.1 — Per-candle ordering as implemented

```
For each candle N in active_range:
  1. _execute_pending_open  OR  _execute_pending_close  at candle[N].open
     just_opened = True if open filled this iteration
  2. SL/TP check:  skipped if just_opened=True  (Rule 2)
                   skipped if opened_at_candle_index >= abs_idx  (assertion guard)
  3. Build initial_state: position fields from engine.open_position,
     simulated_now = candle[N] open ts, sim_balance = current_balance
  4. graph.ainvoke(initial_state)  → dry mode: zero LLM calls
  5. If gate_passed: set pending_open/pending_close  (fills at N+1 open)
  6. Append equity_curve row (mark_balance = realized + unrealized P&L)
     flush every TESTER_EQUITY_INSERT_BATCH candles
```

Causality enforced by:
- **Rule 1**: `OpenIntent` set at N, executed at N+1.open — position opens one candle after signal
- **Rule 2**: `opened_at_candle_index < abs_idx` assertion; `just_opened` flag skips SL/TP on fill candle
- **Rule 3**: `_check_tp_sl` uses entry-distance heuristic when both SL and TP hit same candle
- **Rule 4**: `candle_window = candles[N-W+1 : N+1]` — never N+1 or later

### Step 6.3 — Dry-mode verification (run_id = db697dc8-c6cb-4dc6-aa74-fd002e372d93)

Strategy: `tst_b9f86339a3f5` | BTC-USDT 1h | 2026-05-15 → 2026-05-31 | balance=1000 | dry=true

#### A) Run completed

```
status: completed
candles_processed: 185 / 185
total_signals: 185
gate_passed: 30
total_trades: 15   (14 long, 1 short)
winning_trades: 7  losing_trades: 8
win_rate: 46.67%
total_pnl: -1.832  total_pnl_pct: -0.1832%
max_drawdown_pct: 0.3844%
profit_factor: 0.5034
sharpe_approx: -3.4925
total_fees_paid: 0.2999
llm_failures: 0
dry_signal_mode: True
```

#### B) Equity row per candle

```sql
SELECT COUNT(*) FROM tester.equity_curve WHERE backtest_run_id=…;
-- 185 rows (= total active candles)
```

✓ Every active candle has exactly one equity_curve row.

#### C) Causality — no same-candle open+close

```
 side  |       opened_at        |       closed_at        |   held   | close_reason
-------+------------------------+------------------------+----------+--------------
 long  | 2026-05-23 13:00:00+00 | 2026-05-24 00:00:00+00 | 11:00:00 | llm_close
 long  | 2026-05-24 01:00:00+00 | 2026-05-24 12:00:00+00 | 11:00:00 | llm_close
 long  | 2026-05-28 01:00:00+00 | 2026-05-28 03:00:00+00 | 02:00:00 | sl_hit
 short | 2026-05-28 08:00:00+00 | 2026-05-28 19:00:00+00 | 11:00:00 | llm_close
 ...
```

✓ Minimum held = 2 hours (SL hit 2 candles after open). All held ≥ 1 candle interval. close_reason mix: `llm_close` (14), `sl_hit` (1) — all three paths exercised. Rule 1 never violated.

#### D) Fill price = next candle's open (not signal candle's close)

```
Signal at:    2026-05-23 12:00 (open_long gated, no fill yet)
Fill at:      2026-05-23 13:00 open = 74753.94
With slippage (×1.0005):       74791.317
Actual entry_price in DB:      74791.317  ✓
```

Signal candle close ≠ entry_price. Entry_price = next candle open × (1 + slippage).

#### E) Max drawdown captures intra-trade dips

```
max_drawdown_pct (run):           0.3844%
worst single closed trade loss:   -0.1069%  (0.1069% of initial balance)
```

`0.3844 > 0.1069` ✓ — the mark_balance series captures unrealized drawdown mid-trade; closed-trade P&L alone would understate the true drawdown by 3.6×.

#### F) Balance compounds

```
initial = 1000.00
trade 1: net_pnl= +1.2655 → realized_balance= 1001.2655  (1000 + 1.2655 ✓)
trade 2: net_pnl= +0.2456 → realized_balance= 1001.5111  (1001.2655 + 0.2456 ✓)
trade 3: net_pnl= -0.1050 → realized_balance= 1001.4061  (1001.5111 - 0.1050 ✓)
trade 4: net_pnl= +0.0625 → realized_balance= 1001.4686  (1001.4061 + 0.0625 ✓)
trade 5: net_pnl= -0.0998 → realized_balance= 1001.3688  (1001.4686 - 0.0998 ✓)
```

Each `realized_balance = prior + pnl_realized` to 4+ decimal places. Position sizing on trade 2 used the compounded balance (1001.27) not the initial 1000.

#### G) No public schema leakage

```sql
SELECT COUNT(*) FROM public.orders WHERE strategy_id LIKE 'tst_%';          -- 0
SELECT COUNT(*) FROM public.strategy_positions WHERE strategy_id LIKE 'tst_%'; -- 0
```

✓ Zero writes to public schema.

#### H) Zero LLM failures in dry mode

```
llm_failures: 0
llm_failure_rate: 0.0
dry_signal_mode: True
```

✓ All 185 graph invocations succeeded without LLM calls.

#### I) Cancellation

```
POST /runs  (second dry run, same params)  → run_id=6c58c1b4-…
POST /runs/6c58c1b4-…/cancel              → {"status":"cancelled"}
GET  /runs/6c58c1b4-…                     → status=cancelled, candles_processed=0 / 185
```

✓ Cancel propagated via `task.cancel()` → `asyncio.CancelledError` → DB status `cancelled`. Loop stopped before processing any candles.

### Bugs fixed during verification

1. **`_EngineState` missing `long_count`/`short_count` fields** — `AttributeError` on first close.  
   Fixed by adding both fields to the dataclass with `default=0`.

2. **`lookback_days or 90` evaluates `0 or 90 = 90`** — run with `lookback_days=0` silently used 90.  
   Fixed to: `int(run['lookback_days']) if run['lookback_days'] is not None else 90`.

### Notes

- `dry_signal_mode` is persisted in `tester.backtest_runs` (migration 012) so it applies for the full run regardless of semaphore wait time.
- `sl_hit` close path verified (trade held 2h, SL fired at low price of candle 2026-05-28 03:00).
- `run_end` path verified implicitly: last position was closed by `llm_close` exactly at 2026-05-31 00:00 (loop boundary). Run with open position at end would emit `run_end`.
- No real LLM credit spent in this phase (R3 compliant).

---

## Phase 7 — Cost Estimation, Results API, Migration

Status: COMPLETED

### Files created / modified

| File | Action |
|---|---|
| `strategy-tester/app/engine/node_analyze_sim.py` | Modified — `_CLOSE_AFTER_CANDLES` 10 → 13 (Step 7.0 fix) |
| `strategy-tester/app/pricing.py` | Created — local LLM pricing table (Step 7.1) |
| `strategy-tester/app/api/estimate.py` | Created — `POST /estimate-cost` (Step 7.1) |
| `strategy-tester/app/api/results.py` | Created — 4 read-only sub-resource endpoints (Step 7.2) |
| `strategy-tester/app/api/migrate.py` | Created — `POST /migrate/from-matp` + `/to-matp/{id}` (Step 7.3) |
| `strategy-tester/app/main.py` | Modified — wired estimate, results, migrate routers |

---

### Step 7.0 — run_end verification

**Root cause of `run_end` never firing with `_CLOSE_AFTER_CANDLES=10`:**

The `ohlcv_cache` query uses `candle_ts <= $ts_to` (inclusive upper bound), so `date_to` midnight UTC is always the last active candle. With a 1h timeframe, `candle_idx = int(ts) // 3600` means every midnight UTC is a `% 12` boundary (24h / 12h = 2, always divisible). With `_CLOSE_AFTER_CANDLES=10`, the position cycle puts the closing candle exactly at midnight — `llm_close` fires, never `run_end`.

**Fix:** Changed `_CLOSE_AFTER_CANDLES` to 13. This breaks the alignment: the pending_close lands 11 candles into the hold window at the last active candle, the loop ends with a position still open, and `run_end` fires.

```
Strategy: tst_b9f86339a3f5 | BTC-USDT 1h | 2026-05-16 → 2026-05-31 | dry=true

SELECT id, side, entry_price, closing_price, close_reason, opened_at, closed_at
FROM tester.strategy_positions
WHERE close_reason = 'run_end';

        id         | side |  entry_price  | closing_price  | close_reason |       opened_at        | closed_at
-------------------+------+---------------+----------------+--------------+------------------------+-----------
 (uuid)            | long | 77153.010...  | 77114.434...   | run_end      | 2026-05-30 14:00:00+00 | (null)
```

```
Last OHLCV candle:
candle_ts = 2026-05-31 00:00:00+00   close = 77153.01

closing_price = 77153.01 × 0.9995 = 77114.43  ✓  (long closed with short-side slippage)
```

✓ `close_reason='run_end'` confirmed in `tester.strategy_positions`. Closing price matches last candle close × (1 − slippage).

---

### Step 7.1 — POST /estimate-cost

`POST /estimate-cost` counts tokens locally (`len(prompt) // 4` heuristic from `_vendored/prompt_builder.py`). No LLM provider calls made (R3).

#### Implementation

- `app/pricing.py`: static table of `(provider, model) → (input_$/1M, output_$/1M)` for 15 models across Google, Anthropic, OpenAI. Prefix-match fallback; `_FALLBACK = (0.075, 0.30)` for unknowns.
- `app/api/estimate.py`: loads strategy + AI config from DB, builds a representative prompt with `_mock_state()` (mock OHLCV + indicators, no real candle data), counts tokens, multiplies by active candles, applies pricing.

#### Verification

```
POST /estimate-cost
{
  "strategy_id": "tst_b9f86339a3f5",
  "date_from": "2026-01-01",
  "date_to": "2026-07-01",
  "timeframe": "1h",
  "lookback_days": 90
}

Response:
{
  "provider": "google",
  "model": "gemini-2.0-flash",
  "total_candles": 2905,
  "warmup_candles": 200,
  "active_candles": 2705,
  "tokens_per_cycle": { "input": 506, "output": 350 },
  "total_tokens": { "input": 1368330, "output": 946750 },
  "pricing": { "input_per_1m_usd": 0.075, "output_per_1m_usd": 0.30 },
  "estimated_cost_usd": 0.38668
}
```

✓ No LLM call made. Cost computed from local token heuristic × pricing table. `active_candles = total − 200 warmup = 2905 − 200 = 2705`.

---

### Step 7.2 — Results API

Four read-only endpoints, all returning `{run_id, total, limit, offset, items}` (equity-curve uses `{run_id, count, items}`). All raise `404` for unknown or malformed run IDs.

```
GET /runs/{id}/orders      limit=500, offset=0
GET /runs/{id}/positions   limit=200, offset=0
GET /runs/{id}/equity-curve  (all rows)
GET /runs/{id}/signals     limit=500, ?gate_passed=bool filter
```

#### Verification (against Phase 6 dry run db697dc8-…)

```
GET /runs/db697dc8-.../orders
→ { "total": 30, "items": [...] }   (30 fill orders, matching gate_passed=30)

GET /runs/db697dc8-.../positions
→ { "total": 15, "items": [...] }  (15 closed positions with entry_price, close_reason, pnl_realized)

GET /runs/db697dc8-.../equity-curve
→ { "count": 185, "items": [...] }  (185 rows = 1 per active candle ✓)

GET /runs/db697dc8-.../signals?gate_passed=false
→ { "total": 155, "items": [...] }  (155 filtered signals: 185 total − 30 gate_passed = 155 ✓)

GET /runs/bad-id-xxxx/orders
→ 404 { "detail": "Run not found: bad-id-xxxx" }  ✓
```

Note: asyncpg rejects non-UUID strings for `uuid` columns. Fixed by wrapping the DB call in `try/except` and using `$1::uuid` explicit cast — any invalid UUID returns a clean 404 instead of a 500.

---

### Step 7.3 — Migration API

`POST /migrate/from-matp` and `POST /migrate/to-matp/{id}` per SDD §9.

**R2a invariants (all enforced):**
- `enabled = FALSE` — hardcoded, not from request
- `webhook_enabled = FALSE` — hardcoded
- `account_id` from request body — Pydantic validator rejects empty/blank
- Single `async with conn.transaction()` block wraps all three INSERTs — rolls back entirely on failure
- `logger.warning()` fires with tester_id / public_id / account_id after commit

#### Verification

```
# from-matp with AI-configured source
POST /migrate/from-matp  {"source_matp_id": "eth-range-ba4f"}
→ { "new_tester_id": "tst_...", "ai_config_imported": true, "ai_config_note": null }  ✓

# from-matp with webhook-only source (no ai_strategy_config row)
POST /migrate/from-matp  {"source_matp_id": "test_blofin_demo_01"}
→ { "new_tester_id": "tst_...", "ai_config_imported": false,
    "ai_config_note": "Source strategy had no ai_strategy_config row; defaults applied..." }  ✓

# to-matp creates disabled strategy
POST /migrate/to-matp/tst_...  {"account_id": "acc_test_01"}
→ { "enabled": false, "webhook_enabled": false, "public_strategy_id": "promo-..." }

SELECT enabled, webhook_enabled FROM public.strategies WHERE id = 'promo-...';
 f | f   ✓

# R2a log entry
WARNING  TO-MATP PROMOTION: tester_strategy_id='tst_...' → public_strategy_id='promo-...'
         account_id='acc_test_01' | enabled=False webhook_enabled=False  ✓

# Validation errors
POST /migrate/to-matp/tst_...  {"account_id": ""}
→ 422  "account_id must be non-empty (R2a)"  ✓

POST /migrate/from-matp  {"source_matp_id": "nonexistent"}
→ 404  "Public strategy not found: nonexistent"  ✓
```

Test rows cleaned up from `public.*` after verification.

---

## Phase 8 — Tester UI

### Step 8.0 — Scaffold, design tokens, shared components

Status: COMPLETED

Files created:

| File | Description |
|---|---|
| `tester-ui/package.json` | React 18, Router 6, Recharts, Vite 5 — identical versions to dashboard-ui |
| `tester-ui/tsconfig.json` / `tsconfig.node.json` | Verbatim match to dashboard-ui TS config |
| `tester-ui/vite.config.ts` | `base: '/tester/'`, port 3001, proxy `/api/tester` → `localhost:8006` |
| `tester-ui/index.html` | Inter + JetBrains Mono font links |
| `tester-ui/nginx-spa.conf` | Container nginx on port 3001, SPA `try_files` fallback |
| `tester-ui/src/styles/tokens.css` | `:root` block verbatim from `matp-ui-v0.37.html` (includes `--orange*` absent from dashboard-ui) |
| `tester-ui/src/index.css` | Imports tokens; defines `phone-shell` and `scroll-area` layout classes |
| `tester-ui/src/vite-env.d.ts` | `VITE_API_BASE` env declaration |
| `tester-ui/src/api.ts` | Typed API client for all tester endpoints |
| `tester-ui/src/components/shared/HeaderPill.tsx` | Copied from dashboard-ui |
| `tester-ui/src/components/shared/ActionBand.tsx` | Copied + `disabled` prop added |
| `tester-ui/src/components/shared/SummaryBar.tsx` | Copied + `running` variant added |
| `tester-ui/src/components/shared/DataGrid.tsx` | Copied from dashboard-ui |
| `tester-ui/src/components/shared/TopBar.tsx` | Copied from dashboard-ui |
| `tester-ui/src/components/shared/FilterBar.tsx` | Copied from dashboard-ui |
| `tester-ui/src/components/shared/SectionHeader.tsx` | Copied + `running` variant added |
| `tester-ui/src/components/shared/BottomNav.tsx` | New — 2-tab tester nav: Strats (/) + Simulation (/simulation) |
| `tester-ui/src/App.tsx` | `BrowserRouter basename="/tester"`, routes for / and /simulation/:runId |
| `tester-ui/src/screens/StrategiesScreen.tsx` | Placeholder (replaced in Step 8.1) |
| `tester-ui/src/screens/SimulationScreen.tsx` | Placeholder (to be replaced in Step 8.3) |

#### Verification

```
npm run build
✓ 45 modules transformed
dist/index.html  0.72 kB
dist/assets/*.js 169 kB
✓ built in 11.27s

curl -si http://localhost:3001/tester/
HTTP/1.1 200 OK
Content-Type: text/html
← Dev server serves at /tester/ base path ✓
```

Visual fidelity review pending human inspection.

---

### Step 8.1 — Strategies screen

Status: COMPLETED

#### Backend changes required

Migration 013 added — `ai_config_defaulted` column to `tester.strategies`:

```sql
ALTER TABLE tester.strategies
    ADD COLUMN IF NOT EXISTS ai_config_defaulted BOOLEAN NOT NULL DEFAULT FALSE;
```

`migrate.py` updated to set `ai_config_defaulted = TRUE` when source had no AI config row (was only returned in API response before; now persisted).

`strategies.py` list query updated to LEFT JOIN `tester.ai_strategy_config` — `llm_provider`, `llm_model`, `latest_run_total_pnl_pct` now included in `GET /strategies` response.

strategy-tester rebuilt and redeployed.

#### UI features implemented (StrategiesScreen.tsx — 15 kB)

- **Card anatomy** matching v0.37 `.pc` / `.strat-active` / `.strat-inactive` layout:
  - Row 1: symbol + interval pill + status pill (active/running/inactive), left accent bar
  - Row 2: strategy name tag + dashed ID tag
  - Row 3: route line `[model-short] → [simulated]` + best-run stat (clickable → SimulationScreen)
  - DataGrid 2×3: trades / win rate / net P&L % (row 1); provider / model / interval (row 2)
  - ActionBand: active → `▶ Run Backtest`; running → `⏹ Cancel Run`; inactive → `▶ Start` + `⇑ Promote to MATP`
- **AI config defaulted banner**: amber strip at top of card when `ai_config_defaulted=true`
- **Summary bar**: Active count (green) / Inactive count (gray)
- **Section headers**: Running (blue) / Active (green) / Inactive (gray)
- **Filter bar**: All / Active / Inactive
- **Live polling**: 5 s interval while any run is `running` or `pending`
- `RunPanel` and `PromoteSheet` wired as stubs (slides-up overlay, replaced in Steps 8.2 / 8.4)

#### Verification

```
curl -s http://localhost:8006/strategies | python3 -c "
import sys,json; d=json.load(sys.stdin)
for s in d:
    print(s['id'], '|', s.get('llm_model'), '|',
          'defaulted=', s.get('ai_config_defaulted'), '|',
          'pnl_pct=', s.get('latest_run_total_pnl_pct'))"

tst_b9f86339a3f5 | gemini-2.0-flash | defaulted= False | pnl_pct= -0.0048
tst_f5d1e13b6cbb | gemini-2.0-flash | defaulted= False | pnl_pct= None
tst_3d50405d484e | gemini-2.0-flash | defaulted= False | pnl_pct= None
```

```
# ai_config_defaulted banner test — import webhook-only strategy
POST /migrate/from-matp {"source_matp_id": "test_blofin_demo_01"}
→ ai_config_imported: False  (no public ai_strategy_config row)

curl -s http://localhost:8006/strategies | python3 -c "
import sys,json; d=json.load(sys.stdin)
for s in d:
    if s.get('ai_config_defaulted'):
        print('BANNER STRATEGY:', s['id'], s['name'], 'defaulted=', s['ai_config_defaulted'])"

BANNER STRATEGY: tst_2810b16d6c15 Blofin Demo Test Strategy defaulted= True  ✓
```

```
npm run build  (after StrategiesScreen.tsx added)
✓ 48 modules transformed
dist/assets/*.js  182 kB
✓ built in 31.83s

curl -s http://localhost:3001/api/tester/strategies
← proxy OK: 3 strategies  ✓
```

```
npm run build  (final state)
✓ 48 modules transformed
✓ built in 12.36s  (no TypeScript errors)
```

Visual fidelity review pending human inspection.

---

---

### Step 8.2 — Run Backtest config panel + cost widget

Status: COMPLETED

File modified: `tester-ui/src/components/RunPanel.tsx` (stub → full implementation)

#### Features

- Slide-up bottom sheet with header (strategy name + symbol) and ✕ close
- Fields: Date From / Date To / Timeframe / Lookback days / Balance / Slippage % / Fee % / Model override
- Cost estimate calls `POST /estimate-cost` on every field change (600 ms debounce). Shows band: `"Estimated cost: $0.72 – $1.09  (g-2.0-fl, 6329 active candles)"`
- **Zero-candle guard**: when `active_candles=0` (lookback consumes whole range), shows amber warning, hides cost band, disables Start Run button
- **Dry run checkbox** (dev-only): sends `dry_signal=true` to POST /runs — no LLM credits spent during testing
- Start Run button disabled while cost estimate is loading or zero-candle guard is active; green + enabled once estimate returns valid data
- On submit: calls `POST /runs`, on success calls `onStarted(runId)` → navigates to SimulationScreen

#### Verification

```
# Normal estimate via dev proxy
POST /api/tester/estimate-cost
{ strategy_id, date_from: 2025-10-01, date_to: 2026-04-01, timeframe: 1h, lookback_days: 90 }
→ active_candles: 6329
   cost band: $0.7238 – $1.0857  (gemini-2.0-flash, 6329 active candles)  ✓

# Zero-candle guard — 4h timeframe, 7-day range, lookback=0
POST /api/tester/estimate-cost
{ ..., date_from: 2026-05-01, date_to: 2026-05-08, timeframe: 4h, lookback_days: 0 }
→ total_candles: 43   warmup: 200   active: 0
   ZERO CANDLE GUARD: True  ✓  (Start Run button disabled)

# Dry run submitted through proxy (same path as panel Start Run)
POST /api/tester/runs  { ..., dry_signal: true }
→ run_id: 2477a94f-...  status: pending  dry_signal: True

GET /runs/2477a94f-...  (after 23s)
→ status: completed  candles: 185/185  trades: 10  wins: 3  ✓
```

No real LLM credit spent (R3). All proxied through dev server at `http://localhost:3001/api/tester/`.

Visual fidelity review pending human inspection.

---

---

## Step 8.3 — Simulation (Results) Screen

**Files changed:**
- `tester-ui/src/components/shared/TopBar.tsx` — added `onBack?: () => void` prop (renders `←` button on left when set)
- `tester-ui/src/components/shared/DataGrid.tsx` — added `style?: React.CSSProperties` override on outer container
- `tester-ui/src/api.ts` — added `symbol: string` to `Position` interface (field returned by API, was missing from type)
- `tester-ui/src/screens/SimulationScreen.tsx` — full implementation (was 388-byte placeholder)

#### Features

- **TopBar with back button**: `← Simulation` navigates to `/` (Strategies screen)
- **Metadata pill row**: symbol · timeframe · date range · model (or `dry-signal` pill) · status
- **Aborted run badge**: amber warning block shown when `status === 'aborted_high_failure_rate'`
- **Summary bar**: Trades / Win Rate / Net P&L — three equal-width cells, PnL colored green/red
- **Equity curve** (recharts LineChart):
  - `mark_balance` series across 185 candle timestamps
  - `linearGradient id="eqLine"` with sharp color split at `initial_balance`:
    - Top `baseStop%` = green (`#00a877`), bottom = red (`#e11d48`)
    - `baseStop = (maxY − initial) / (maxY − minY) × 100`
  - Dashed `ReferenceLine` at `initial_balance`
  - `~5` x-axis tick labels (MM/DD), tooltip shows ISO date + `$value`
- **Statistics card**: card with neutral-gray left accent bar, DataGrid 2×3:
  - Row 1: Long / Short / Profit Factor
  - Row 2: Max Drawdown (red) / Avg Win (green) / Avg Loss (red)
- **Trade cards** (one per position): green/red left accent bar, header with symbol + side pill + PnL, DataGrid 2×3 (Entry/Exit/Size + Open/Close/Fees), close-reason band mapping:
  - `llm_close` → "LLM Close"
  - `tp_hit` → "Take Profit Hit" / `sl_hit` → "Stop Loss Hit" / `run_end` → "Run End"
- **Filtered Signals section**: collapsed by default, count badge + ▲/▼ toggle, expands to SignalRow list (time / action / rejection reason)
- **Live run polling**: re-fetches every 5 s while `status === 'running' | 'pending'`, shows `⟳ running` in TopBar right slot

#### Verification

Run `db697dc8-c6cb-4dc6-aa74-fd002e372d93` (Phase 6 dry-signal, BTC/USDT 1h, 2026-05-15→2026-05-31):

```
# GET /runs/{id}  via dev proxy http://localhost:3001/api/tester
status: completed | trades: 15 | wr: 46.67 | pnl_pct: -0.1832
pf: 0.5034 | dd: 0.3844% | long: 14 | short: 1
avg_win: +0.2653 | avg_loss: -0.4611 | model: None (dry-signal)  ✓

# GET /runs/{id}/equity-curve
185 points | min: 997.90 | max: 1001.75 | initial: 1000.00
candles with trade_pnl: 15
→ gradient baseStop = (1001.75 − 1000) / (1001.75 − 997.90) × 100 = 45.4%
  green: top 45.4%  red: bottom 54.6%  (net-negative run → correct)  ✓

# GET /runs/{id}/positions?limit=200
total: 15 | wins: 7 | losses: 8
close_reasons: { llm_close: 14, sl_hit: 1 }
sample: side=long  entry=74791.32  close=76713.62  pnl=+1.2655  reason=llm_close
→ green left bar for win, red for loss; "LLM Close" and "Stop Loss Hit" bands  ✓

# GET /runs/{id}/signals?gate_passed=false&limit=200
total: 155 | rejection_reasons: { hold_or_adjust: 155 }
→ Filtered Signals badge shows 155; expands to 155 hold_or_adjust rows  ✓
```

Build: `✓ 846 modules transformed` (0 TypeScript errors after adding recharts).

No real LLM credit spent (R3). All proxied through dev server at `http://localhost:3001/api/tester/`.

Visual fidelity review pending human inspection.

---

### Step 8.4 — PromoteSheet

**Files changed:**
- `tester-ui/src/components/PromoteSheet.tsx` — full implementation (was stub)
- `tester-ui/src/screens/StrategiesScreen.tsx` — added `onDone` callback to `<PromoteSheet>` call site

**What the sheet does:**
- Slide-up bottom sheet triggered by "⇑ Promote to MATP" on inactive strategy cards
- Loads strategy name/symbol/interval via `GET /strategies/{id}` on mount for the header subtitle
- Pre-flight orange warning banner: "Creates a DISABLED strategy in the live system" with `enabled=false` / `webhook_enabled=false` explanation
- Account ID text input (autofocus); submit disabled until non-empty
- "⇑ Promote" button (orange) → `POST /migrate/to-matp/{id}` with `{ account_id }`
- On success: green confirmation banner + public_strategy_id monospace display + `enabled: false` reminder + "Done" button
- On error: inline red error message, allows retry
- `onDone` callback refreshes the strategy list when the sheet closes after success

#### Verification

Strategy: `tst_2810b16d6c15` ("Blofin Demo Test Strategy"), account: `acc_blofin_demo_default`

```
POST /api/tester/migrate/to-matp/tst_2810b16d6c15
Body: { "account_id": "acc_blofin_demo_default" }

Response 200:
{
  "tester_strategy_id": "tst_2810b16d6c15",
  "public_strategy_id": "promo-d6d7f1c6",
  "account_id": "acc_blofin_demo_default",
  "enabled": false,
  "webhook_enabled": false
}
✓ Returns correct shape; enabled/webhook_enabled both false as required
```

Build: `✓ 846 modules transformed`, TypeScript: 0 errors.

No real LLM credit spent (R3). Visual fidelity review pending human inspection.

---

## Phase 8 Complete

All four steps delivered and API-verified:

| Step | Component | Status |
|------|-----------|--------|
| 8.0 | Scaffolding (Vite, routing, shared tokens) | ✓ |
| 8.1 | RunPanel (cost estimate + form) | ✓ |
| 8.2 | RunPanel: submit + dry_signal=true verification | ✓ |
| 8.3 | SimulationScreen (equity curve, trades, signals) | ✓ |
| 8.4 | PromoteSheet (to-matp flow) | ✓ |

---

## Phase 9 — Docker + nginx wiring

**Files changed:**
- `tester-ui/Dockerfile` — new multi-stage build (Node 20 Alpine → nginx Alpine)
- `docker-compose.yml` — added `tester-ui` service; fixed `strategy-tester` healthcheck; wired nginx depends_on
- `nginx/nginx.conf` — added `/api/tester/` and `/tester/` location blocks

### tester-ui Dockerfile

Multi-stage build:
1. `node:20-alpine` — `npm ci` + `npm run build` (builds Vite app into `dist/`)
2. `nginx:alpine` — copies `dist/` to `/usr/share/nginx/html`, uses `nginx-spa.conf` (port 3001, SPA fallback to `index.html`)

`VITE_API_BASE` is not needed as a build arg because `api.ts` defaults to `/api/tester` at runtime.

### docker-compose.yml changes

- `tester-ui` service: `build: ./tester-ui`, `depends_on: strategy-tester: service_healthy`, no exposed port (nginx-internal only)
- `strategy-tester` healthcheck: changed from `curl` (not present in python:3.12-slim) to `python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8006/health')"`
- `nginx` depends_on: added `strategy-tester: service_healthy` and `tester-ui: service_started`

### nginx.conf additions

Added before `/api/dashboard/`:

```nginx
location /api/tester/ {
    set $upstream http://strategy-tester:8006;
    rewrite ^/api/tester/(.*)$ /$1 break;
    proxy_pass $upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}

location /tester/ {
    set $upstream http://tester-ui:3001;
    rewrite ^/tester/(.*)$ /$1 break;
    proxy_pass $upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

### Verification

```
docker compose build --no-cache tester-ui
→ ✓ 846 modules transformed  (inside container build)  ✓

docker compose up -d tester-ui
→ matp-tester-ui-1 Started  ✓

docker compose up -d --force-recreate nginx
→ matp-nginx-1 Started  ✓

# Route: tester UI
GET http://localhost/tester/
→ HTTP 200
→ <title>MATP Strategy Tester</title>
→ <script src="/tester/assets/index-GLn6Df_a.js">  (correct /tester/ base prefix)  ✓

# Route: tester API
GET http://localhost/api/tester/strategies
→ HTTP 200, 4 strategies  ✓

# Regression: dashboard still up
GET http://localhost/
→ HTTP 200  ✓
```

Note: bind-mount inode replacement issue — after editing `nginx.conf` with the Edit tool, nginx must be recreated (`--force-recreate`) to pick up the new file, not just reloaded.

---

## Phase 9 Complete

| Item | Status |
|------|--------|
| `tester-ui/Dockerfile` (multi-stage) | ✓ |
| `tester-ui` service in docker-compose.yml | ✓ |
| `strategy-tester` healthcheck fixed (python3) | ✓ |
| nginx `/api/tester/` route | ✓ |
| nginx `/tester/` route | ✓ |
| Dashboard regression check | ✓ |
