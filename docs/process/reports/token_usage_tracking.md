# Actual LLM Token-Spend Tracking (migration 047)

**Date:** 2026-07-07
**Trigger:** $25 of Gemini prepay consumed 2026-06-25 → 2026-07-07 with no way to
attribute it — `context_tokens` was a chars/4 *input estimate* and output/thinking
tokens were never recorded anywhere.

## What was built

- **Migration 047** (applied, self-verified): `ai_signal_log` gains `input_tokens`,
  `output_tokens`, `total_tokens` — provider-reported actuals; NULL on historical rows
  and on calls that die before a response (429s etc.).
- **`node_analyze`**: `with_structured_output(..., include_raw=True)` — the plain
  structured wrapper returns only the parsed object and silently discards
  `usage_metadata`. Usage is captured into state (`llm_usage`) **including on
  structured-output parse failures** (those tokens are spent too — previously they were
  both unaccounted *and* the source of `llm_failed`); `node_dispatch` writes the three
  columns with every signal row.
- **`GET /api/ai/usage?from=YYYY-MM-DD`** (dashboard-api): totals + per-strategy +
  per-model rollups, with `tracked_calls` vs `llm_calls` so the pre-047 blind spot is
  explicit. Per-strategy token sums also added to `/strategies/:id/signals/stats`.
- **UI**: AI Signal Log detail view shows `Tokens (actual): total (in X / out Y)` per
  signal, next to the old context estimate.

## Verification (pasted)

Manual dry-run cycle (forced `dry_run` by the trigger endpoint) + the deploy's startup
cycle, read back from the DB:

```
    triggered_at     | strategy_id | proposed_action | est  | input_tokens | output_tokens | total_tokens
---------------------+-------------+-----------------+------+--------------+---------------+--------------
 2026-07-07 16:34:45 | sol-ai-6486 | hold            | 1502 |         1657 |          1285 |         2942
 2026-07-07 16:27:38 | sol-ai-6486 | hold            | 1739 |         1947 |           894 |         2841
```

`GET /api/ai/usage?from=2026-07-07` (live):

```
total:        tracked_calls 4, input 7,644, output 4,722, total 12,366
per_strategy: sol-ai 5,783 · xrp-ai 3,969 · hype-breakout 2,614 · ai-btc 0 (range-skip, no LLM call)
per_model:    google/gemini-2.5-flash — all of it
```

UI bundle serves the new field (`index-BueziBul.js` contains "Tokens (actual)");
health OK on ai-signal-generator (collector 16/16 streams re-attached) and dashboard-api.

## What the first actuals already reveal

**Output (thinking) tokens ≈ 40–60% of total spend per call** — 894–1,784 output tokens
against ~1,700–2,200 input. The old input-only estimate missed roughly half the bill,
and output bills at ~8× the flash input rate. Extrapolating: each call costs ~3–5×
what input-only math suggested, which goes a long way toward explaining $25/12 days
once probes and earlier activity are added. With this tracking live, the next
provider-side anomaly is attributable in one query.

Follow-up candidates (not built): explicit thinking budget on flash calls (biggest
lever now that thinking is measurably the cost majority), probe throttling, and a UI
usage rollup card. Ask before scheduling.

## Addendum (same day) — usage rollup exposed in the UI + label refresh

- **AI Signal Log page** now shows a "Tokens (30d)" strip under the filters:
  total (in/out, call count) plus one pill per strategy with spend, auto-refreshing
  every 60s from `/api/ai/usage`, with an explicit "actuals since 2026-07-07 — earlier
  calls untracked" note. Hidden entirely while there are no tracked calls.
- **Strategy-modal data-source labels** refreshed to match reality post-Phase-2:
  "Liquidations (stream aggregate)" (was "no source yet") and
  "Economic Calendar (provider paid-tier — dormant)" (was "needs API key" — the key
  exists; the endpoint is paid-tier). The per-template auto-preset map
  (`TEMPLATE_DATA_SOURCES`) needed no change — consumption sets are unchanged and
  `scalper` already presets `use_liquidations`, which now points at a live field.

Verified in the served bundle:

```
$ docker compose exec -T dashboard-ui grep -rlo "Tokens (30d)" /usr/share/nginx/html
/usr/share/nginx/html/assets/index-FzT8KY5x.js
$ docker compose exec -T dashboard-ui grep -rlo "Liquidations (stream aggregate)" /usr/share/nginx/html
/usr/share/nginx/html/assets/index-FzT8KY5x.js
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-FzT8KY5x.js
```
