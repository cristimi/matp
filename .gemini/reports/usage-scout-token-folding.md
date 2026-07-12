# /usage — fold scout token columns into the spend sums

Closes the open question from
`docs/process/reports/2026-07-12-llm-fallback-tiering.md`: the dashboard
`/api/ai/usage` endpoint ignored `scout_*_tokens` (migration 053), so
scout-escalated cycles under-reported spend by the scout's tokens.

## Change (`dashboard-api/src/routes/ai.ts`, GET /usage)

- **total** and **per_strategy**: `input/output/total_tokens` now sum main
  columns + scout columns; a separate `scout_total_tokens` field shows the
  scout share.
- **per_model**: unchanged (main columns only), deliberately — the log row
  records only the DECIDING model (`llm_provider`/`llm_model`), not which
  scout model spent the scout tokens, so folding them there would
  misattribute scout spend to the premium model. Called out in the response
  `note`.

## Verification (live, via nginx)

Baseline → insert one synthetic `scout_escalated` row (main 1000/100/1100,
scout 500/50/550) → re-query → delete the row → re-query:

```
── baseline (from=2026-07-12):
total: {'tracked_calls': 112, 'llm_calls': 112, 'input_tokens': 302327, 'output_tokens': 112710, 'total_tokens': 415037, 'scout_total_tokens': 0}
bnb: [{'strategy_id': 'bnb-ai-scalper-edbb', 'tracked_calls': 26, 'input_tokens': 75405, 'output_tokens': 13722, 'total_tokens': 89127, 'scout_total_tokens': 0}]

── with synthetic row (expect total +1650, scout_total_tokens 550):
total: {'tracked_calls': 113, 'llm_calls': 113, 'input_tokens': 303827, 'output_tokens': 112860, 'total_tokens': 416687, 'scout_total_tokens': 550}
bnb: [{'strategy_id': 'bnb-ai-scalper-edbb', 'tracked_calls': 27, 'input_tokens': 76905, 'output_tokens': 13872, 'total_tokens': 90777, 'scout_total_tokens': 550}]
note: actuals from provider usage_metadata; rows before 2026-07-07 (migration 047) have no actuals; total/per_strategy include scout tokens (migration 053), per_model is deciding-model spend only
DELETE 1
── after cleanup:
total: {'tracked_calls': 112, 'llm_calls': 112, 'input_tokens': 302327, 'output_tokens': 112710, 'total_tokens': 415037, 'scout_total_tokens': 0}
```

Deltas are exact: input +1500 (1000 main + 500 scout), output +150,
total +1650, `scout_total_tokens` 550 in both `total` and the strategy's
`per_strategy` row. Synthetic row deleted; DB back to pre-test state.

Deployed with `./scripts/redeploy.sh dashboard-api` (tsc clean before build);
verified against the running container, not host build output.
