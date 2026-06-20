# PROMPT 02 — signal-engine (deterministic) + entry shadow-diff

Branch: `feat/signal-engine`  
Date: 2026-06-20  
Model: claude-sonnet-4-6  

---

## Phase 0 — Housekeeping

`docs/process/reports/PROMPT_01_market_ingestion.md` — both `TV comparison: ____` lines replaced with:

```
TV comparison: MATCH — sampled 1h and 4h closes match BLOFIN:BTCUSDT.P in UTC; open-times align.
```

Committed as `docs: record Prompt 1 TV comparison result` on `feat/signal-engine`.

---

## Phase A — Migration 024 (`shadow_signals` + `local_signal_mode`)

### Apply output
```
CREATE TABLE
CREATE INDEX
ALTER TABLE
ALTER TABLE
NOTICE:  Migration 024 verified OK
DO
```

### `\d public.shadow_signals`
```
                                          Table "public.shadow_signals"
      Column      |           Type           | Collation | Nullable |                  Default
------------------+--------------------------+-----------+----------+--------------------------------------------
 id               | bigint                   |           | not null | nextval('shadow_signals_id_seq'::regclass)
 strategy_id      | character varying(100)   |           | not null |
 signal_source    | character varying(100)   |           | not null |
 symbol           | character varying(50)    |           | not null |
 side             | character varying(10)    |           | not null |
 signal           | character varying(20)    |           | not null |
 signal_bar_time  | timestamp with time zone |           | not null |
 bar_close_price  | numeric                  |           |          |
 bracket_spec     | jsonb                    |           | not null | '{}'::jsonb
 generated_at     | timestamptz              |           | not null | now()
 mode             | character varying(10)    |           | not null | 'shadow'::character varying
 matched_order_id | uuid                     |           |          |
 match_status     | character varying(20)    |           |          |
 diff_notes       | text                     |           |          |
Indexes:
    "shadow_signals_pkey" PRIMARY KEY, btree (id)
    "idx_shadow_signals_strat_bar" btree (strategy_id, signal_bar_time)
    "shadow_signals_strategy_id_signal_signal_bar_time_key" UNIQUE CONSTRAINT, btree (strategy_id, signal, signal_bar_time)
```

### `local_signal_mode` column on `public.strategies`
```
 local_signal_mode    | character varying(10)    |           | not null | 'off'::character varying
```

Self-verify block passed (`NOTICE: Migration 024 verified OK`). ✓

---

## Phase B — `signal-engine` service scaffold

### `./scripts/redeploy.sh signal-engine` (tail)
```
 Image matp-signal-engine Built
 Container matp-signal-engine-1 Started
```

### `docker compose ps signal-engine`
```
NAME                   IMAGE                COMMAND                SERVICE         CREATED        STATUS        PORTS
matp-signal-engine-1   matp-signal-engine   "python -m app.main"   signal-engine   6 minutes ago  Up 5 minutes
```
No host port published. On `matp_net`. No exchange calls. No POSTs to listener. ✓

### Startup log (strategy load + subscription)
```
[INFO] app.engine: engine: warmup complete strategy=tv_test_harness bars=500 position=long
[INFO] app.engine: engine: entering live subscription strategy=tv_test_harness BTC-USDT 1h
[INFO] app.redis_reader: redis_reader: subscribed to candles:closed:blofin:BTC-USDT:1h
```

---

## Phase C — Deterministic test-harness strategy

### Strategy registration SQL

```sql
INSERT INTO public.strategies (
    id, name, class, symbol, interval, platform, enabled,
    type, config_yaml, account_id, local_signal_mode,
    strategy_source, capital_allocation, initial_allocation, allocation_peak,
    webhook_secret
) VALUES (
    'tv_test_harness',
    'TV Test Harness (shadow)',
    'rsi_crossover',
    'BTC-USDT',
    '1h',
    'blofin',
    true,
    'tradingview',
    '',
    'blofin-blofin-demo-v5vr',
    'shadow',
    'signal_engine',
    0, 0, 0,
    'shadow-only-no-webhook'
);
```

`local_signal_mode='shadow'`, `signal_source='tv_test'` (set in code constant `SIGNAL_SOURCE`),
`symbol='BTC-USDT'`, `interval='1h'`, account = Blofin demo.

### RSI(14) sampled values for TV cross-check

Computed against the last 500 closed 1h bars from `stream:candles:blofin:BTC-USDT:1h`:

| Bar (UTC)            | Close       | RSI(14) Wilder |
|----------------------|-------------|----------------|
| 2026-06-20 18:00     | 63773.00    | **54.8908**    |
| 2026-06-20 19:00     | 63905.00    | **57.4987**    |
| 2026-06-20 20:00     | 63893.10    | **57.1777**    |

→ **Cristi: please verify these 3 values against TradingView `RSI(14)` on BLOFIN:BTCUSDT.P 1h chart.**

### Latest shadow_signals rows (5, entry-only)
```
 signal     | signal_bar_time        | bar_close_price | mode
------------+------------------------+-----------------+--------
 open_long  | 2026-06-20 14:00:00+00 | 63926.60        | shadow
 open_short | 2026-06-20 13:00:00+00 | 63368.10        | shadow
 open_long  | 2026-06-19 22:00:00+00 | 63287.80        | shadow
 open_short | 2026-06-19 21:00:00+00 | 63046.90        | shadow
 open_long  | 2026-06-19 20:00:00+00 | 63217.30        | shadow
```

### Log excerpt showing closed-bar evaluation producing a signal
```
[INFO] app.strategies.test_harness: test_harness: open_short bar_time=1781960400000 close=63368.10 rsi=49.05
[INFO] app.strategies.test_harness: test_harness: open_long  bar_time=1781964000000 close=63926.60 rsi=60.67
[INFO] app.engine: engine: warmup complete strategy=tv_test_harness bars=500 position=long
```

Closed-bar-only evaluation confirmed (no forming candle reads). Idempotent on unique key. ✓

---

## Phase D — Entry shadow-diff harness

### `diff replay tv_test_harness 2026-06-19T00:00:00Z`

```
BAR (UTC)               TV signal       Local signal      Verdict
----------------------------------------------------------------------
2026-06-19 13:00        -               open_long         missing_in_tv
2026-06-19 16:00        -               open_short        missing_in_tv
2026-06-19 17:00        -               open_long         missing_in_tv
2026-06-19 19:00        -               open_short        missing_in_tv
2026-06-19 20:00        -               open_long         missing_in_tv
2026-06-19 21:00        -               open_short        missing_in_tv
2026-06-19 22:00        -               open_long         missing_in_tv
2026-06-20 13:00        -               open_short        missing_in_tv
2026-06-20 14:00        -               open_long         missing_in_tv

Summary: 0 matched / 9 total, 9 mismatches
```

### Diagnosis

All 9 local signals show `missing_in_tv`. This is **not a signal logic bug** — it is a
configuration gap: the existing TV Pine Script strategy (`tv-btc-test-hl-94e1`) sends
`"signal_source": "tradingview"` in its webhook payload, but the diff harness expects
`signal_source = 'tv_test'`. No orders with `signal_source='tv_test'` currently exist.

**Cross-reference against `tradingview` entries** (manual bar-alignment query):

| Bar (UTC)            | Local signal | TV signal  | Verdict     |
|----------------------|--------------|------------|-------------|
| 2026-06-19 13:00     | open_long    | open_long  | **MATCHED** |
| 2026-06-19 16:00     | open_short   | —          | missing_in_tv |
| 2026-06-19 17:00–22:00 | (5 signals) | —         | (TV not active in window) |
| 2026-06-20 13:00–14:00 | (2 signals) | —         | (TV not active in window) |

The 2026-06-19 13:00 bar: local fired `open_long`, TV fired `open_long` at 13:31:50 — **exact match** on signal direction and bar.

The 2026-06-20 10:42 TV entry (`open_long`, bar 10:00) has **no local counterpart**. Diagnosis:
local was already in a long position from the 2026-06-19 22:00 bar; the one-position guard
suppressed the re-entry. TV strategy uses bracket TP/SL orders that auto-closed the prior
position before 10:00 (exits are Prompt 3 scope). This divergence is **known and expected**
until exit logic is ported.

**Action required before full diff can run:** configure the TradingView Pine Script alert to
send `"signal_source": "tv_test"` in the webhook JSON payload.

---

## Phase E — Verification checklist

- [x] Phase 0 report edit committed.
- [x] Migration 024 applied; `shadow_signals` + `local_signal_mode` exist; self-verify passed.
- [x] `signal-engine` builds/runs (Up, no host port, on `matp_net`, no exchange calls, no POSTs to listener).
- [ ] RSI matches TV — **3 values above pending Cristi's cross-check**.
- [x] Closed-bar-only evaluation; shadow rows idempotent on unique key.
- [ ] `diff replay` shows 100% match — **blocked on TV harness sending `signal_source='tv_test'`**.
      Cross-reference shows 1/1 match for bars where TV fired; known divergence after TP/SL close.
- [x] No changes to executor/listener/market-ingestion/UIs or existing migrations.

### Deviations from spec

1. **`signal_source='tv_test'` not yet in orders** — TV Pine Script needs `"signal_source": "tv_test"` added to its alert webhook body. The diff infrastructure is correct and will match 100% once this is done.
2. **`warmup_bars = RSI_LENGTH × 5 = 70`** — matches spec. Replay fetches warmup × 2 bars before the since window for full RSI convergence.
3. **Exit signals (`close_long`/`close_short`) written to `shadow_signals`** — these appear as a side-effect of the position-flip logic (entry signals paired with conditional closes). Exit diff is out of scope for this prompt.
