# Exchange Resolution from `account_id` — Fix Report

**Date:** 2026-06-24  
**Service:** `ai-signal-generator`  
**Root cause fixed:** `platform`-based exchange routing silently defaulted to `binance` for strategies with `platform = 'auto'`, causing all OHLCV/funding/OI fetches to fail on blofin symbols.

---

## What changed at each site

### Site 1 — `app/database.py` (new shared resolver)

Added `resolve_exchange_id(conn, account_id) -> str`:

```python
async def resolve_exchange_id(conn, account_id: str) -> str:
    if not account_id:
        raise ValueError("resolve_exchange_id: account_id is missing or empty")
    row = await conn.fetchrow(
        "SELECT exchange FROM exchange_accounts WHERE id = $1", account_id,
    )
    if row is None:
        raise ValueError(
            f"resolve_exchange_id: no exchange_accounts row for account_id={account_id!r}"
        )
    exchange = row["exchange"]
    if not exchange:
        raise ValueError(
            f"resolve_exchange_id: exchange is null/empty for account_id={account_id!r}"
        )
    return exchange
```

Never silently defaults. Raises `ValueError` on any missing data.

### Site 2 — `app/scheduler.py` (`_build_initial_state`)

Inside the open `conn` block, after confirming strategy exists:

```python
exchange_id = await resolve_exchange_id(conn, strategy["account_id"])
```

Then after building `sc`:
```python
sc['exchange_id'] = exchange_id
```

On `ValueError`, the exception propagates to `_trigger_cycle`'s `except Exception` handler — logs the error, does **not** invoke the graph.

### Site 3 — `app/main.py` (`/internal/trigger`)

Same pattern, inside the open `conn` block:

```python
try:
    exchange_id = await resolve_exchange_id(conn, strategy["account_id"])
except ValueError as exc:
    raise HTTPException(status_code=422, detail=str(exc))
sc['exchange_id'] = exchange_id
```

### Site 4 — `app/graph/nodes/node_ingest.py`

Removed `_EXCHANGE_MAP` dict and the `platform`-based two-line lookup:

```python
# BEFORE:
_EXCHANGE_MAP = {'blofin': 'blofin', 'hyperliquid': 'hyperliquid'}
raw_exchange = sc.get('platform') or sc.get('exchange', 'binance')
exchange_id  = _EXCHANGE_MAP.get(raw_exchange, 'binance')

# AFTER:
exchange_id = sc['exchange_id']
```

### Site 5 — `app/event_watcher.py` (`_check_volume_spike`, `_check_funding_spike`)

Changed both SELECTs from `platform` to `account_id`, replaced fallback defaults with resolver:

```python
# SELECT now: "SELECT symbol, account_id FROM strategies WHERE id = $1"

try:
    async with db_pool.acquire() as conn:
        exchange_id = await resolve_exchange_id(conn, row['account_id'])
except ValueError as exc:
    logger.warning("...: exchange resolution failed strategy=%s: %s", strategy_id, exc)
    return False  # best-effort gate — skip check, don't crash watcher
```

Removed `_EXCHANGE_MAP` dict from `event_watcher.py`.

### Site 6 — `app/data/ohlcv.py` (new `resolve_ccxt_symbol`)

Added symbol resolver that handles perpetual futures on blofin/hyperliquid:

```python
def resolve_ccxt_symbol(exchange, symbol: str) -> str:
    if symbol in exchange.markets:
        return symbol
    parts = symbol.split('/')
    if len(parts) == 2:
        base, quote = parts
        linear = f"{base}/{quote}:{quote}"
        if linear in exchange.markets:
            return linear
        candidates = sorted(
            s for s, m in exchange.markets.items()
            if m.get('base') == base and m.get('type') in ('swap', 'future')
        )
        if candidates:
            return candidates[0]
    raise ValueError(f"{exchange.id} does not have market symbol {symbol!r}")
```

Called in `fetch_ohlcv`, `fetch_funding_rate`, and `fetch_open_interest` immediately after `load_markets()`. Handles:
- blofin: `HYPE/USDT` → `HYPE/USDT:USDT` (linear perp, same settle)
- hyperliquid: `BTC/USDT` → `BTC/USDC:USDC` (swap found by base search)

---

## Grep sweep output (source tree)

```
$ grep -rn "_EXCHANGE_MAP\|or 'binance'\|or 'blofin'\|get('platform')\|\.platform" ai-signal-generator/app --include=*.py | grep -v test

ai-signal-generator/app/main.py:159:    s.platform, s.default_leverage, s.margin_mode, s.pnl_today, s.enabled,
```

The only surviving `platform` reference is `s.platform` in the `main.py` SQL SELECT — it's a passthrough column for display, not exchange routing.

## In-container deployed-code grep

```
$ docker compose exec -T ai-signal-generator grep -rn "_EXCHANGE_MAP\|or 'binance'\|or 'blofin'\|get('platform')\|\.platform" app/

app/main.py:159:                s.platform, ...   (SELECT column passthrough — non-routing)

$ docker compose exec -T ai-signal-generator grep -n "resolve_ccxt_symbol\|resolve_exchange_id" app/data/ohlcv.py app/data/sentiment.py app/database.py

app/data/ohlcv.py:31:def resolve_ccxt_symbol(exchange, symbol: str) -> str:
app/data/ohlcv.py:75:        symbol = resolve_ccxt_symbol(exchange, symbol)
app/data/sentiment.py:12:from app.data.ohlcv import resolve_ccxt_symbol
app/data/sentiment.py:55:        symbol = resolve_ccxt_symbol(exchange, symbol)
app/data/sentiment.py:86:        symbol = resolve_ccxt_symbol(exchange, symbol)
app/database.py:26:async def resolve_exchange_id(conn, account_id: str) -> str:
```

---

## Live cycle logs

### Startup cycle (18:02:37 UTC)

```
2026-06-24 18:02:12 [INFO] Scheduler started strategy=hype-breakout-da2e
2026-06-24 18:02:37 [INFO] Triggering cycle strategy=hype-breakout-da2e reason=startup
2026-06-24 18:03:31 [WARNING] fetch_open_interest error [blofin HYPE/USDT:USDT]: blofin fetchOpenInterest() is not supported yet
2026-06-24 18:03:47 [INFO] node_dispatch: strategy=hype-breakout-da2e action=None gate=False reason=llm_failed
```

- Exchange: **blofin** (not binance) ✅
- Symbol: **HYPE/USDT:USDT** (not HYPE/USDT) ✅
- No `does not have market symbol` error ✅
- `fetchOpenInterest() is not supported yet` — blofin API limitation, not our bug

### manual_verify cycle (18:17:23 UTC)

Same pattern. No market symbol errors. Only OI unsupported warning.

---

## `ai_signal_log` evidence

```sql
SELECT triggered_at, trigger_reason, data_sources_used, context_tokens, gate_rejection_reason
FROM ai_signal_log WHERE strategy_id = 'hype-breakout-da2e'
ORDER BY triggered_at DESC LIMIT 2;

         triggered_at          | trigger_reason |                data_sources_used                | context_tokens | gate_rejection_reason
-------------------------------+----------------+--------------------------------------------------+----------------+----------------------
 2026-06-24 18:17:23+00        | manual_verify  | {technical,fear_greed,funding_rate,...,news}     |                | llm_failed
 2026-06-24 18:02:37+00        | startup        | {technical,fear_greed,funding_rate,...,news}     |                | llm_failed
```

`data_sources_used` includes `technical` — OHLCV data reached the graph and indicators were computed. **Previously every cycle had `data_fetch_errors` for all fields due to binance having no HYPE/USDT.**

`llm_failed` / `context_tokens = NULL` is a transient Gemini 503 ("high demand") unrelated to this fix. The data pipeline is fixed.

---

## Notes

- `platform = 'auto'` on `hype-breakout-da2e` is now irrelevant for data routing. The account (`blofin-blofin-demo-v5vr`) resolves to `blofin` via `exchange_accounts.exchange`. No row update needed.
- `fetchOpenInterest` on blofin is not supported in ccxt — `open_interest` will remain `None` for blofin strategies until ccxt adds support.
