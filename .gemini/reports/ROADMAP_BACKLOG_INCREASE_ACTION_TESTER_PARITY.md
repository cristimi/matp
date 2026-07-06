# Report — ROADMAP backlog: increase-action discrepancy + tester toggle-parity gap

Date: 2026-07-06. User-requested follow-up to the target-state AI prompt design task
(`docs/process/reports/ai_strategy_prompts_targetstate.md`): record two findings from that
task in `docs/ROADMAP.md` → Deferred Backlog.

## Change

Two new bullets in `docs/ROADMAP.md` Deferred Backlog:

1. **`_render_task` offers an `increase` action the output schema rejects** — placed after
   the "Minimum order value guard" bullet. `builder.py::_render_task` instructs the model to
   output `"increase"` for strong continuation, but `LLMSignalOutput.action`'s Literal in
   `node_analyze.py` has no `increase` member, so a compliant model fails structured-output
   validation and the cycle is wasted (`llm_signal = None`). Suggested small fix: delete the
   line; alternative: add the action + guard/dispatch support.
2. **`tester.ai_strategy_config` toggle parity** — placed directly after the existing
   `tester.*` schema-cleanup bullet, into which it should be folded. The tester copy lacks
   `use_geometry` and `use_economic_calendar`; the specced-but-unapplied migration 045
   (`docs/design/ai_prompts/20_plumbing_specs.md`) would widen the gap by 8 more toggles.

## Verification (pasted output)

The `increase` instruction exists in the prompt scaffold, and the action is absent from the
schema module:

```
$ grep -n '"increase"' ai-signal-generator/app/prompt/builder.py
326:            'If the position is showing strong continuation: output "increase" (only if within size limits).',
$ grep -n "increase" ai-signal-generator/app/graph/nodes/node_analyze.py || echo "no 'increase' in node_analyze.py (Literal set lacks it)"
no 'increase' in node_analyze.py (Literal set lacks it)
```

Tester column gap (from the live DB, captured 2026-07-06 during the design task —
`tester.ai_strategy_config` has 26 columns and its `use_%` toggles are only):

```
$ docker compose exec postgres psql -U matp -d matp -At -c "SELECT column_name FROM information_schema.columns WHERE table_schema='tester' AND table_name='ai_strategy_config' AND column_name LIKE 'use_%';"
use_btc_dominance
use_fear_greed
use_funding_rate
use_macro
use_news
use_open_interest
use_technical
```

(`public.ai_strategy_config` additionally has `use_geometry` and `use_economic_calendar` —
see `docs/design/ai_prompts/00_audit.md` §2.11.)

Only `docs/ROADMAP.md` changed:

```
$ git diff --stat docs/ROADMAP.md
 docs/ROADMAP.md | 17 +++++++++++++++++
 1 file changed, 17 insertions(+)
```

No code, schema, or DB change accompanies this edit.
