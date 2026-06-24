# HYPE Breakout da2e — No Indicators Investigation

**Date:** 2026-06-24  
**Strategy:** `hype-breakout-da2e` (HYPE Breakout, `HYPE-USDT`, Blofin demo)

## Symptom

Every scheduled cycle for `hype-breakout-da2e` produces a hold with reasoning like:

> *"No specific price, volume, or volatility data (such as Bollinger Bands or ATR) was
> provided for HYPE-USDT to confirm a breakout or volume expansion. Maintaining hold."*

`ai_signal_log` entries (IDs 62, 64, 65, 67, 71) all show
`data_sources_used = {technical,fear_greed,funding_rate,open_interest,news}` yet the LLM
never receives actual indicator values. Gate has never passed.

## Root Cause

`node_ingest.py` resolves the data exchange with:

```python
raw_exchange = sc.get('platform') or sc.get('exchange', 'binance')
exchange_id  = _EXCHANGE_MAP.get(raw_exchange, 'binance')
```

`_EXCHANGE_MAP = {'blofin': 'blofin', 'hyperliquid': 'hyperliquid'}`

The strategy row in `strategies` has `platform = 'auto'`. The value `'auto'` is truthy so
the `or` short-circuits — `raw_exchange = 'auto'`. But `'auto'` is not in `_EXCHANGE_MAP`,
so the `.get()` falls back to `'binance'`.

**Result: all OHLCV and sentiment fetches target Binance, but HYPE/USDT does not exist on
Binance.** Every fetch fails:

```
fetch_ohlcv error [binance HYPE/USDT 2h]: binance does not have market symbol HYPE/USDT
fetch_funding_rate error [binance HYPE/USDT]: binance does not have market symbol HYPE/USDT
fetch_open_interest error [binance HYPE/USDT]: binance does not have market symbol HYPE/USDT
```

Because `ohlcv_data` is `None`, `compute_indicators()` is never called and
`technical_indicators` stays `None`. The LLM runs with no price, volume, or indicator data.

The configured `account_id` is `blofin-blofin-demo-v5vr` — Blofin — and HYPE/USDT **is**
available on Blofin. The mismatch is purely in the `platform` field: `'auto'` instead of
`'blofin'`.

## DB Evidence

```
SELECT a.use_technical, a.indicators, a.interval_no_position,
       s.platform, s.account_id, s.symbol
FROM ai_strategy_config a
JOIN strategies s ON s.id = a.strategy_id
WHERE a.strategy_id = 'hype-breakout-da2e';

 use_technical |           indicators            | interval_no_position | platform |       account_id        |  symbol
---------------+---------------------------------+----------------------+----------+-------------------------+-----------
 t             | {RSI,MACD,EMA50,EMA200,BB,VWAP} | 2h                   | auto     | blofin-blofin-demo-v5vr | HYPE-USDT
```

## Fix Required

Update `strategies.platform` from `'auto'` to `'blofin'` for `hype-breakout-da2e`.
No code changes needed — `_EXCHANGE_MAP` already maps `'blofin'` correctly.

```sql
UPDATE strategies SET platform = 'blofin' WHERE id = 'hype-breakout-da2e';
```
