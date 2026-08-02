# Strategy change tracking

Before this, every settings table stored only the current value and an `updated_at`
clock. Change a prompt, a model or a threshold and the previous value was gone. The
one audit table that existed (`ai_risk_config_audit`) covered a single field and had
zero rows in it.

## What changed

**`db/migrations/075_change_tracking.sql`** (applied to the live DB)

- `strategy_change_log` — one row per changed field: entity, strategy, field,
  old value, new value, who, when.
- `ai_prompt_template_versions` — full snapshot of every version of a prompt's text.
- `ai_prompt_templates` gained `version` and `updated_at`; `ai_signal_log` gained
  `prompt_version`.
- Row-level triggers on `strategies`, `ai_strategy_config`, `ai_risk_config` and
  `ai_prompt_templates`.

Triggers rather than API-level diffs because config here is edited from three
places: the dashboard, hand-run psql, and migrations (every prompt template edit so
far arrived as a migration). Only a trigger catches all three.

Deliberately not logged: `pnl_today`, `pnl_total`, `last_signal_at`,
`capital_allocation`, `allocation_peak` — runtime counters that move on every fill
and would bury the settings changes. `webhook_secret` is logged as a rotation event
with both values masked.

**`dashboard-api`**

- `db.ts` — `queryAsActor()`: runs a write in a transaction with a transaction-local
  `matp.actor`, so the trigger records who made the change instead of `system`. It
  has to be transaction-local, otherwise the actor would leak onto the next request
  that reuses the pooled connection.
- `GET /strategies/:id/changes` — the strategy's own settings history merged with the
  edit history of every prompt template it has run on.
- `GET /ai/templates/:id/versions` and `/versions/:version` — the recorded wordings,
  each returned together with the previous version's text.
- The hand-rolled `ai_risk_config_audit` insert was removed; the trigger now covers
  every column of that table instead of the one field the old block diffed.

**`ai-signal-generator`** — each cycle records the prompt version it ran on, via a
subquery in the existing `ai_signal_log` insert (no new parameters).

**`dashboard-ui`** — a "What was changed" card on the strategy history page, dashed
yellow markers on the money-over-time chart at each behaviour change, and a viewer
that shows the full prompt text of a version with a toggle to the previous one.

## Verification

Triggers, run inside a rolled-back transaction against live data:

```
$ docker compose exec -T postgres psql -U matp -d matp
BEGIN;
SET LOCAL matp.actor = 'trigger-test';
UPDATE strategies SET pnl_today = pnl_today + 1 WHERE id='ai-btc-6f8c';
UPDATE strategies SET max_leverage = max_leverage WHERE id='ai-btc-6f8c';
UPDATE ai_strategy_config SET confidence_threshold = confidence_threshold + 0.01,
       llm_model='test-model' WHERE strategy_id='ai-btc-6f8c';
UPDATE ai_prompt_templates SET system_prompt = system_prompt || E'\n-- test line'
       WHERE id='range_rotation';

     entity      | strategy_id |  template_id   |      field_name      |         old          |       new       |  changed_by
-----------------+-------------+----------------+----------------------+----------------------+-----------------+--------------
 ai_config       | ai-btc-6f8c |                | llm_model            | llama-3.3-70b-versat | test-model      | trigger-test
 ai_config       | ai-btc-6f8c |                | confidence_threshold | 0.590                | 0.600           | trigger-test
 prompt_template |             | range_rotation | system_prompt        | v1 · 5265 chars      | v2 · 5278 chars | trigger-test

  template_id   | version | length |                                      note
----------------+---------+--------+--------------------------------------------------------------------------------
 range_rotation |       1 |   5265 | baseline captured by migration 075 — text before this point was never recorded
 range_rotation |       2 |   5278 |
ROLLBACK
```

The pnl update wrote nothing (ignored field) and the no-op update wrote nothing
(value identical) — both correct.

Strategy creation, auto-stop and secret rotation, also rolled back:

```
  entity  |   strategy_id   | action |   field_name   | old_value |    new_value
----------+-----------------+--------+----------------+-----------+-----------------
 strategy | zz-trigger-test | create | (created)      |           | ZZ Trigger Test
 strategy | zz-trigger-test | update | enabled        | true      | false
 strategy | zz-trigger-test | update | stop_reason    |           | max drawdown
 strategy | zz-trigger-test | update | webhook_secret | (hidden)  | (rotated)
```

The secret value never reaches the log.

End-to-end through the real API — a live edit and its revert, showing the actor is
attached:

```
$ curl -s -X PUT http://localhost/api/dashboard/ai/strategies/ai-btc-6f8c/config \
       -d '{"confidence_threshold":0.61}'
$ curl -s -X PUT http://localhost/api/dashboard/ai/strategies/ai-btc-6f8c/config \
       -d '{"confidence_threshold":0.59}'      # reverted to its original value

 id |      field_name      | old_value | new_value | changed_by
----+----------------------+-----------+-----------+------------
  4 | confidence_threshold | 0.590     | 0.610     | dashboard
  5 | confidence_threshold | 0.610     | 0.590     | dashboard
```

Prompt version endpoints:

```
$ curl -s http://localhost/api/dashboard/ai/templates/range_rotation/versions
[{"version": 1, "name": "Range Rotation", "captured_at": "2026-06-09T20:31:58.132Z",
  "note": "baseline captured by migration 075 — text before this point was never recorded",
  "chars": 5265}]

$ curl -s http://localhost/api/dashboard/ai/templates/range_rotation/versions/1
v 1 chars 5265 prev None
You are a quantitative crypto analyst specializing in range-trading strategies on perpetual futures...
```

New `ai_signal_log` insert shape, run with the real column list and rolled back:

```
  id  | prompt_template | prompt_version
------+-----------------+----------------
 5990 | range_rotation  |              1
ROLLBACK
```

Type checks clean (`npx tsc --noEmit`, exit 0, both services). Deploy:

```
matp-dashboard-api-1         Up 5 seconds (health: starting)   ✓ dashboard-api redeployed.
matp-ai-signal-generator-1   Up 6 seconds (health: starting)   ✓ ai-signal-generator redeployed.
matp-dashboard-ui-1          Up 6 seconds                      ✓ dashboard-ui redeployed.

$ docker compose exec -T dashboard-ui grep -rl 'What was changed' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-uLAc7j9S.js
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-uLAc7j9S.js
```

## Limits

- History starts now. Nothing that happened before this migration was ever recorded
  and cannot be recovered — including the prompt text as it was before today.
- `changed_by` is `system` for anything that does not pass through the two dashboard
  write routes (listener auto-stops, psql, migrations). That is accurate, not a gap.
- `db/init.sql` was NOT regenerated. It is already behind by several earlier
  migrations (e.g. 074's `social_position_state` reshape is missing from it), so
  regenerating would fold unrelated drift into this change. Flagged for the operator
  rather than done silently.
