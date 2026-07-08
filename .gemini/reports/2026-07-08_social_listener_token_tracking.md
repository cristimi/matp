# Social Listener — LLM Token-Spend Tracking (migration 049)

**Date:** 2026-07-08
**Branch:** main
**Status:** DONE — deployed and verified end-to-end

---

## Background

Asked whether we could trace social-listener's LLM spend. Answer at the time was no: token
tracking (migration 047) only covers `ai_signal_log` / ai-signal-generator. social-listener's
`extractor.py` called `.with_structured_output(SocialExtraction)` **without** `include_raw=True`,
so LangChain discarded `usage_metadata` and it was unrecoverable after the fact. Every Telegram
message triggers an LLM call (`extractor_model` defaults to `claude-sonnet-4-6`), so this was
real, invisible spend.

Mirrored the pattern already used in `ai-signal-generator/app/graph/nodes/node_analyze.py`.

---

## Changes

- **`db/migrations/049_social_signal_log_token_usage.sql`** — adds `input_tokens`,
  `output_tokens`, `total_tokens` to `social_signal_log` (identical shape to migration 047 on
  `ai_signal_log`).
- **`social-listener/app/extractor.py`** — `_get_llm()` now wraps with
  `with_structured_output(SocialExtraction, include_raw=True)`. `extract()` pulls
  `resp["raw"].usage_metadata` for token counts and `resp["parsed"]` for the structured result;
  if parsing fails, tokens are still kept (the LLM call still cost money) and the record falls
  back to a non-actionable placeholder, same as the API-exception path.
- **`social-listener/app/db.py`** — `insert_signal()` persists the three new columns
  (`rec.get(...)`, `NULL` for historical/failed rows).

---

## Verification

Migration:
```
$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/049_social_signal_log_token_usage.sql
BEGIN
ALTER TABLE
COMMIT
NOTICE:  Migration 049 verified OK: token-usage columns present on social_signal_log
DO
```

Redeploy — clean startup, no errors:
```
$ ./scripts/redeploy.sh social-listener
...
✓ social-listener redeployed.
```
```
2026-07-08 17:01:41,780 INFO app.db DB pool initialized
2026-07-08 17:01:42,148 INFO social-listener Telegram connected as 8833405539
2026-07-08 17:01:42,196 INFO social-listener Backfilling last 50 messages from AstronomerZero
2026-07-08 17:01:43,442 INFO social-listener Backfill complete (50 messages)
2026-07-08 17:01:43,442 INFO social-listener Listening for new messages...
```

Direct `extract()` call against the live Anthropic API confirms real usage_metadata comes back:
```
$ docker compose exec -T social-listener python3 -c "... extract('BTC longs opened here, entry 65000...') ..."
{'is_actionable': True, 'action_type': 'OPEN', 'asset': 'BTC', 'direction': 'LONG',
 'confidence': 0.97, 'input_tokens': 1215, 'output_tokens': 185, 'total_tokens': 1400}
```

Full pipeline (`main.handle()` → `db.insert_signal`) with a synthetic message, confirming tokens
land in the DB, then cleaned up:
```
2026-07-08 17:03:24,550 INFO social-listener msg 999999901 [ACTIONABLE] OPEN BTC ref=65000.0 conf=0.97
2026-07-08 17:03:24,625 INFO social-listener BRAIN msg 999999901 LONG->LONG none [skipped/no_state_change]

 channel_msg_id | is_actionable | action_type | input_tokens | output_tokens | total_tokens
----------------+---------------+-------------+--------------+---------------+--------------
      999999901 | t             | OPEN        |         1215 |           186 |         1401
(1 row)

$ docker compose exec -T postgres psql -U matp -d matp -c "DELETE FROM social_shadow_orders WHERE channel_msg_id = 999999901; DELETE FROM social_signal_log WHERE channel_msg_id = 999999901;"
DELETE 1
DELETE 1
```
(Test row didn't advance `social_position_state` — decision was `skipped/no_state_change`, so
no cleanup needed there.)

## Definition of Done

- [x] Migration 049 applied and self-verified.
- [x] `extract()` captures real provider-reported token usage via `include_raw=True`.
- [x] Tokens persisted even when structured parsing fails (cost was still incurred).
- [x] `db.insert_signal()` writes the new columns.
- [x] Verified against the live Anthropic API and the full `handle()` pipeline, not just unit-level.
- [x] Deployed via `./scripts/redeploy.sh social-listener`; container healthy, no errors.

## Not done (deferred, per user request — "we will look into UI later on")

- No dashboard-api/dashboard-ui surfacing yet. `dashboard-api/src/routes/ai.ts`'s token rollup
  still queries `ai_signal_log` only. UI presentation (separate "Social Listener" page vs. a tab)
  was discussed but explicitly deferred.
