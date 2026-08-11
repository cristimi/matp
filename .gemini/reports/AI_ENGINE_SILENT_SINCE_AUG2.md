# AI trading engine: why it looks dead since 2 August

Investigation only — **no code was changed**. Date: 2026-08-11.

## Short answer

The engine never stopped. It keeps waking up, fetching data and calling the LLM every
few minutes. What stopped on 2 August is the **writing of its diary** (`ai_signal_log`).
Every single write has failed since then, so every screen that reads that table shows
nothing after 2 August.

Trading itself carried on for another week (last AI order: 9 August). It has gone quiet
in the last two days for a *different* reason — see "Second problem".

## Evidence

### The log table stops dead on 2 August

```
           d            | count | fired
------------------------+-------+-------
 2026-07-31 00:00:00+00 |   103 |    11
 2026-08-01 00:00:00+00 |   285 |    18
 2026-08-02 00:00:00+00 |    96 |     3
(nothing after this)
```

Last row per strategy — all seven stop within the same minute:

```
 xrp-ai-3844                | 2026-08-02 12:00:47.707048+00
 ai-btc-6f8c                | 2026-08-02 12:00:47.673155+00
 sol-ai-6486                | 2026-08-02 12:00:19.682232+00
 eth-ai-34d2                | 2026-08-02 12:00:47.715501+00
 tao-ai-range-rotation-d257 | 2026-08-02 12:00:34.605128+00
 bnb-ai-scalper-edbb        | 2026-08-02 12:01:04.060056+00
```

### But orders kept being placed after that date

```
 2026-08-02 |     3
 2026-08-03 |    30
 2026-08-04 |     3
 2026-08-05 |    12
 2026-08-06 |     7
 2026-08-07 |     5
 2026-08-08 |     7
 2026-08-09 |     1
```

So the engine was alive and trading — just invisible.

### The error, in the live container

```
2026-08-11 08:20:19,134 [ERROR] app.graph.nodes.node_dispatch: Failed to write ai_signal_log:
    inconsistent types deduced for parameter $5
DETAIL:  text versus character varying
```

246 of these in the last 20 hours — i.e. **every** cycle.

### Root cause — commit `3024136`, 2 Aug 12:27 UTC

That commit ("change tracking: remember what a strategy's settings and prompt used to
be") added `prompt_version` to the INSERT in
`ai-signal-generator/app/graph/nodes/node_dispatch.py:204`:

```diff
-                          $17,...,$25::jsonb,$26::jsonb)
+                          $17,...,$25::jsonb,$26::jsonb,
+                          (SELECT version FROM ai_prompt_templates WHERE id = $5))
```

`$5` is now used in two places at once: as the `prompt_template` column (type
`character varying`) and inside `WHERE id = $5` (where Postgres resolves it as `text`).
Postgres refuses to guess one type for both. The first failing write is 12:27 — the
minute of the commit.

Reproduced by hand on the live database:

```
matp=# PREPARE t2 AS INSERT INTO ai_signal_log
       (strategy_id, trigger_reason, prompt_template, prompt_version)
       VALUES ($1,'x',$2,(SELECT version FROM ai_prompt_templates WHERE id = $2));
ERROR:  inconsistent types deduced for parameter $2
DETAIL:  text versus character varying
```

The write sits inside a `try/except` that only logs, so nothing crashed and nothing
alerted. Note the same commit's report was written and pushed without the insert ever
having been run against a real database.

### Knock-on damage

- Signal history / AI pages: empty since 2 Aug.
- `webhook_status`, `order_id` and outcome PnL are never linked back — the follow-up
  `UPDATE ... WHERE id = signal_log_id` gets `id = NULL` and updates nothing.
- Anything reading the log for decisions is now reading an empty table: the cooldown
  check in `node_guard.py:161` (cooldowns silently no longer apply), the scout/premium
  escalation in `node_analyze.py:41`, the duplicate-event guard in `event_watcher.py:71`,
  and the LLM health monitor (`llm_health_monitor.py:106`, currently reporting
  "0 strategies checked" every cycle).

## Second problem — why trading stopped on 9 August

This is separate from the logging bug and would not be fixed by fixing it.

**About 1 cycle in 5 gets no answer from any LLM at all.** In the last 20 hours:

```
    174 gate=False reason=hold_or_adjust
     47 gate=False reason=llm_failed        ← ~20%
     18 gate=False reason=no_range_llm_skipped
      7 gate=False reason=confidence_below_threshold
```

Three causes visible in the failures:

1. **Network/DNS on the host.** Repeated `Cannot connect to host
   generativelanguage.googleapis.com:443 [Timeout while contacting DNS servers]` and bare
   `TimeoutError` against groq, cerebras and zhipu. The host is heavily loaded —
   `load average: 9.84` with **0 GB of 2 GB RAM available**. This matches the known
   "host load slows HTTP" behaviour, but memory exhaustion is new and worse.
2. **Zhipu account is out of credit.** `429 code 1113 — 余额不足或无可用资源包,请充值`
   ("insufficient balance, please top up"). This kills the whole fallback chain for
   `bnb-ai-scalper-edbb`, `tao-ai-range-rotation-d257` and `hype-breakout-da2e`.
3. **Data feeds returning errors** (the LLM then runs on thin context):
   - `finnhub` economic calendar → **403 Forbidden**, 119 times in 20h (dead/expired key).
   - `coingecko` news → **401 Unauthorized** (dead/expired key).
   - `hyperliquid BTC/USDC:USDC 10m` OHLCV → **422**, 120 times in 20h. Hyperliquid has
     no `10m` candle; the BTC strategy is asking for a timeframe the venue does not offer.

## What I did not do

No code, config, database or container was modified, as instructed.

## Suggested order of repair (for approval)

1. Fix the `$5` reuse in `node_dispatch.py` — pass the template id as its own parameter
   (`$27`) or cast it (`$5::varchar`). One-line change, restores all history and the
   cooldown/health logic.
2. Top up or remove `zhipu` from the fallback chains of the three strategies using it.
3. Fix the BTC strategy's `10m` interval to a timeframe hyperliquid supports.
4. Renew or drop the finnhub and coingecko keys.
5. Look at host memory — 0 GB available is the likely source of the DNS/timeout failures.
