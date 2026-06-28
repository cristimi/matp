# Cleanup: Drop Dead Columns + Remove Daily Signal Cap

**Date:** 2026-06-28  
**Branch:** main  
**Commits:** 00dd1bb (migration), + code+report commit (see git log)

## Summary

Two changes shipped together:

1. **Migration 030** — dropped 7 columns from `public.strategies` and 1 from `public.order_execution_log`.
2. **Daily signal cap retired** — the `signals_today` / `max_daily_signals` guard was silently broken (counter never reset, so "daily" was really "ever"). Removed entirely from order-listener, dashboard-api, and dashboard-ui.

`tester.*` schema is intentionally left untouched — separate later pass.

---

## Premises verified before any change

### 1. Migration 030 is free
```
027_drop_webhook_enabled.sql
028_exit_reason.sql
029_social_state_shadow.sql
_archive
README.md
```

### 2. Zero live code readers of dead columns (excluding db/migrations, tests, strategy-tester, tester-ui)

**blofin_token** — only in docs/MATP_STRATEGY_TESTER_SDD_v1.1.md (docs, not code) ✓  
**drawdown_anchor_pnl** — only in docs/ and .gemini/reports/ (docs, not code) ✓  
**platform_override** — only in docs/process/prompts/ and docs/MATP_STRATEGY_TESTER_SDD_v1.1.md (docs, not code) ✓  
**avg_fill_price** — empty result ✓  
**win_count / loss_count** — only in `dashboard-api/src/routes/stats.ts` as computed aliases (`COUNT(*) FILTER (WHERE pnl > 0) AS win_count`) from the `orders` table — never reads `strategies.win_count` ✓

### 3. _check_rate_limit defined once, never called
```
225:async def _check_rate_limit(pool, strategy_id: str, max_signals: int) -> bool:
```
No other hits.

---

## Phase A — Migration

### Migration file: `db/migrations/030_drop_dead_columns.sql`

Applied to live database:

```
BEGIN
ALTER TABLE
ALTER TABLE
COMMIT
NOTICE:  Migration 030 verified OK
DO
```

### `\d public.strategies` after migration (dead columns absent)

```
                                         Table "public.strategies"
        Column        |           Type           | Collation | Nullable |             Default
----------------------+--------------------------+-----------+----------+----------------------------------
 id                   | character varying(100)   |           | not null |
 name                 | character varying(100)   |           | not null |
 class                | character varying(100)   |           | not null |
 symbol               | character varying(50)    |           | not null |
 interval             | character varying(10)    |           | not null |
 platform             | character varying(20)    |           | not null | 'auto'::character varying
 enabled              | boolean                  |           | not null | true
 config_yaml          | text                     |           | not null |
 created_at           | timestamp with time zone |           | not null | now()
 updated_at           | timestamp with time zone |           | not null | now()
 webhook_secret       | character varying(255)   |           | not null |
 description          | text                     |           |          |
 max_leverage         | integer                  |           |          | 10
 pnl_today            | numeric                  |           |          | 0
 pnl_total            | numeric                  |           |          | 0
 last_signal_at       | timestamp with time zone |           |          |
 tags                 | text[]                   |           |          | '{}'::text[]
 type                 | character varying(20)    |           | not null | 'internal'::character varying
 pair_id              | integer                  |           |          |
 account_id           | character varying(100)   |           |          |
 allow_quote_variants | boolean                  |           | not null | false
 allow_cross_charting | boolean                  |           | not null | false
 default_leverage     | integer                  |           | not null | 1
 is_deleted           | boolean                  |           | not null | false
 config               | jsonb                    |           | not null | '{}'::jsonb
 margin_mode          | character varying(10)    |           | not null | 'isolated'::character varying
 strategy_source      | character varying(20)    |           | not null | 'tradingview'::character varying
 capital_allocation   | numeric                  |           | not null | 100
 margin_per_trade     | numeric                  |           | not null | 5
 max_drawdown_pct     | numeric                  |           | not null | 50
 initial_allocation   | numeric                  |           |          |
 allocation_peak      | numeric                  |           |          |
 local_signal_mode    | character varying(10)    |           | not null | 'off'::character varying
```

### `\d public.order_execution_log` after migration

```
                                          Table "public.order_execution_log"
      Column       |           Type           | Collation | Nullable |                     Default
-------------------+--------------------------+-----------+----------+-------------------------------------------------
 id                | bigint                   |           | not null | nextval('order_execution_log_id_seq'::regclass)
 signal_log_id     | bigint                   |           |          |
 account_id        | character varying(100)   |           |          |
 exchange          | character varying(30)    |           | not null |
 exchange_order_id | character varying(100)   |           |          |
 client_order_id   | character varying(100)   |           | not null |
 symbol            | character varying(20)    |           | not null |
 side              | character varying(10)    |           | not null |
 order_type        | character varying(20)    |           | not null |
 requested_size    | numeric                  |           | not null |
 requested_price   | numeric                  |           |          |
 status            | character varying(20)    |           | not null |
 cumulative_filled | numeric                  |           |          | 0
 exchange_fee      | numeric                  |           |          | 0
 error_message     | text                     |           |          |
 placed_at         | timestamp with time zone |           |          |
 filled_at         | timestamp with time zone |           |          |
 created_at        | timestamp with time zone |           | not null | now()
 updated_at        | timestamp with time zone |           | not null | now()
```

---

## Phase B — Code removals

### Changes summary

**order-listener/app/webhook_handler.py:**
- Removed `date` from `from datetime import` (only used by the deleted helper)
- Deleted `_check_rate_limit` helper (defined but never called)
- Deleted "Guard 1: Daily signal cap" block (the `signals_today >= max_daily` check that raised HTTP 429)
- Changed post-signal UPDATE from `SET signals_today = signals_today + 1, last_signal_at = NOW()` to `SET last_signal_at = NOW()` (preserving the timestamp)

**dashboard-api/src/routes/strategies.ts:**
- Removed `max_daily_signals` from POST `/strategies` destructuring and INSERT
- Removed `max_daily_signals` from PUT `/strategies/:id` destructuring and UPDATE (parameter renumbering: $9–$14 → $9–$13)
- Deleted the `POST /strategies/:id/max-daily-signals` route entirely

**dashboard-ui/src/pages/Strategies.tsx:**
- Removed `max_daily_signals?` and `signals_today` from the `Strategy` interface
- Removed `max_daily_signals: '500'` from `TV_FORM_DEFAULTS`
- Removed `max_daily_signals` initialization from `handleEdit` form state
- Removed "Max Daily Signals" `<FieldRow>` from the add form (TradingView tail)
- Removed "Daily Signals" `<FieldRow>` from the edit form (TradingView branch)
- Removed `max_daily_signals: parseInt(...)` from both submit payloads (add + edit)

### Build verification

```
dashboard-api:  tsc  →  exit 0  (no output = no errors)
dashboard-ui:   tsc && vite build  →  ✓ built in 1m 2s  (exit 0)
order-listener: python3 ast.parse  →  syntax OK
```

---

## Phase C — Deploy + verify

### Redeploy output (all three services)

```
✓ order-listener redeployed.
✓ dashboard-api redeployed.
✓ dashboard-ui redeployed.   live asset: index-Co-kfOzQ.js
```

### order-listener running container — cap guard gone, last_signal_at preserved

```
docker compose exec -T order-listener grep -rn \
  "signals_today\|max_daily_signals\|_check_rate_limit\|last_signal_at" \
  /app/app/webhook_handler.py

958:                    "UPDATE strategies SET last_signal_at = NOW() WHERE id = $1",
962:            logger.warning(f"Failed to update last_signal_at for {strategy['id']}: {e}")
```

Only `last_signal_at` remains — no cap, no counter.

### dashboard-api running container — no signals_today / max_daily_signals in dist

```
docker compose exec -T dashboard-api grep -rn "signals_today\|max_daily_signals" /app/dist/
(none)
```

### dashboard-ui running container — no max_daily_signals in served bundle

```
docker compose exec -T dashboard-ui grep -rn "max_daily_signals\|signals_today" /usr/share/nginx/html/
(none)
```

### Dedicated endpoint now returns 404

```
curl -s -o /dev/null -w "%{http_code}" -X POST \
  http://localhost/api/dashboard/strategies/test/max-daily-signals
404
```

### API still serves strategies correctly

```
curl -sf http://localhost/api/dashboard/strategies | head -c 100
[{"id":"hype-breakout-da2e","name":"HYPE Breakout","class":"webhook","symbol":"HYPE-USDT",...
```

### Health checks

```
curl -sf http://localhost:8001/health
{"status":"ok","service":"order-listener"}
```

dashboard-api: port 8003 not host-bound (internal only); confirmed healthy via `docker compose ps` (status: Up, healthy) and nginx proxy response above.

---

## What stays (preserved as required)

- `last_signal_at` — still updated on every webhook ✓
- `pnl_today`, `pnl_total` ✓
- `config` (jsonb) ✓
- `initial_allocation`, `capital_allocation`, `allocation_peak` ✓
- `strategy_positions.current_price` — out of scope, not touched ✓
- `tester.*` schema — not touched (separate later pass) ✓
