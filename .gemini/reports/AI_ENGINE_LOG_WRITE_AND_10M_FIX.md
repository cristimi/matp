# Fix: AI signal log writes, and the 10m candle nobody sells

Follow-up to `AI_ENGINE_SILENT_SINCE_AUG2.md`. Two of the five problems found there are
now fixed and verified live. Date: 2026-08-11.

## 1. Every `ai_signal_log` write failed since 2 August

### Cause

Commit `3024136` added `prompt_version` to the INSERT in
`ai-signal-generator/app/graph/nodes/node_dispatch.py` and reused `$5` for it:

```sql
(SELECT version FROM ai_prompt_templates WHERE id = $5)
```

`$5` is already the `prompt_template` column (`character varying`). Inside `WHERE id = $5`
Postgres resolves the parameter as `text`. It will not deduce two types for one
parameter, so the statement never even prepared:

```
ERROR:  inconsistent types deduced for parameter $5
DETAIL:  text versus character varying
```

The write sits in a `try/except` that only logs, so nothing crashed and nothing alerted.

### Fix

The template id is now passed a second time as its own parameter `$27`, with a comment
saying why the repetition is deliberate:

```diff
-                          (SELECT version FROM ai_prompt_templates WHERE id = $5))
+                          -- $27 repeats $5 on purpose: reusing $5 here makes Postgres
+                          -- deduce it as both varchar (the column) and text (the =),
+                          -- which it refuses to do — every insert then fails.
+                          (SELECT version FROM ai_prompt_templates WHERE id = $27))
...
                 regime_snapshot_json,
+                sc.get('template_id', 'trend_following'),
```

Checked against the live database before deploying — the old form errors, the new one
prepares:

```
matp=# PREPARE t3 AS INSERT INTO ai_signal_log (... prompt_version)
       VALUES ($1,...,$26::jsonb,(SELECT version FROM ai_prompt_templates WHERE id = $27))
       RETURNING id;
PREPARE
```

## 2. `10m` is not a candle any exchange sells

### Cause

A strategy's `cycle_interval` is a *polling cadence* that `node_ingest.py:130` also passes
straight through as the OHLCV candle timeframe. `10m` is offered in the UI
(`Strategies.tsx:1684`) and is used by `ai-btc-6f8c`, `eth-ai-34d2` and
`hype-breakout-da2e` — but no venue lists a 10-minute candle:

```
hyperliquid ['1m','3m','5m','15m','30m','1h','2h','4h','8h','12h','1d', ...]
blofin      ['1m','3m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d', ...]
binance     ['1m','3m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d', ...]
```

Hyperliquid answered every request with 422 — 120 times in 20 hours, all
`BTC/USDC:USDC 10m`. `fetch_ohlcv` returned `None`, so the BTC strategy was asking the LLM
for a decision with **no price data at all**. `10m` was also missing from
`_TIMEFRAME_SECONDS`, so the candle-close and lookback maths silently fell back to 3600s.

### Fix

New `resolve_timeframe()` in `app/data/ohlcv.py`, called right after symbol resolution.
It keeps the requested timeframe when the venue has it, otherwise rounds **down** to the
nearest one the venue actually lists (10m → 5m). Down and not up on purpose: a 5m candle
has always closed by the next 10m wake, while a 15m one would be handed back unchanged on
every other cycle. `10m`, `6h` and `12h` were added to `_TIMEFRAME_SECONDS`.

`scheduling.py` is untouched — the wake cadence stays 10m, as configured. Only the candle
request changes.

## Tests

Six new tests in `tests/test_ohlcv.py` cover pass-through, 10m→5m, a sparse venue, an
unknown string, a venue with nothing shorter, and the full `fetch_ohlcv` path asserting
the venue is asked for `5m`. Full suite, run inside the container:

```
$ docker compose exec -T ai-signal-generator python -m pytest /app/tests_new -q
........................................................................ [ 55%]
.........................................................                [100%]
129 passed, 2 warnings in 15.72s
```

## Verified on the running stack

Deployed with `./scripts/redeploy.sh ai-signal-generator` at 08:48 UTC.

All seven strategies writing again, `prompt_version` populated — the first rows since
2 August:

```
        strategy_id         |         triggered_at          | cycle_interval | prompt_version | proposed_action  | llm_tier
----------------------------+-------------------------------+----------------+----------------+------------------+----------
 ai-btc-6f8c                | 2026-08-11 08:50:15.910395+00 | 10m            |              1 | hold             | scout
 sol-ai-6486                | 2026-08-11 09:00:19.215969+00 | 1h             |              1 | hold             | premium
 xrp-ai-3844                | 2026-08-11 09:00:32.459519+00 | 30m            |              1 | hold             | premium
 eth-ai-34d2                | 2026-08-11 09:00:19.1946+00   | 1h             |              1 | place_limit_long | fallback
 ai-btc-6f8c                | 2026-08-11 09:00:35.957001+00 | 10m            |              1 | hold             | scout
 bnb-ai-scalper-edbb        | 2026-08-11 09:00:30.436405+00 | 1h             |              1 |                  |
 tao-ai-range-rotation-d257 | 2026-08-11 09:00:10.478729+00 | 1h             |              1 |                  |
 ai-btc-6f8c                | 2026-08-11 09:10:11.609415+00 | 10m            |              1 | hold             | scout
```

Error counts in the 30 minutes after the deploy, versus 246 and 120 in the 20 hours
before it:

```
write failures (30m):   0
10m fetch errors (30m): 0
resolve_timeframe (30m):
  08:50:55 resolve_timeframe: hyperliquid has no 10m candle — using 5m
  09:01:20 resolve_timeframe: hyperliquid has no 10m candle — using 5m
  09:10:11 resolve_timeframe: hyperliquid has no 10m candle — using 5m
```

The two rows with an empty `proposed_action` are `llm_failed` cycles — that is problem 2
from the earlier report (zhipu out of credit), untouched here and still open.

## Still open — from the earlier report

3. **Zhipu account out of credit** — `429 code 1113 余额不足`. Kills the whole fallback
   chain for `bnb-ai-scalper-edbb`, `tao-ai-range-rotation-d257`, `hype-breakout-da2e`.
4. **Finnhub key 403, CoinGecko key 401** — both feeds dead.
5. **Host memory** — 0 GB of 2 GB available, load ~10; the likely source of the DNS and
   connection timeouts hitting every LLM provider.
