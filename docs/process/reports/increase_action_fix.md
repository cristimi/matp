# Fix — `_render_task` `increase` Action / Output-Schema Mismatch

**Date:** 2026-07-07
**Backlog origin:** found 2026-07-06 in the target-state prompt design audit
(`00_audit.md` §2.10); flagged again by the 2026-07-07 engine audit as the likely source
of the ~5–7%/day `llm_failed` rate (23 in the trailing 7 days).

## The bug

With a position open, `_render_task` instructed: *'If the position is showing strong
continuation: output "increase" (only if within size limits).'* — but
`LLMSignalOutput.action` (`node_analyze.py`) has no `increase` member:

```python
action: Literal[
    'open_long', 'open_short', 'close_long', 'close_short',
    'hold', 'partial_close', 'adjust_stops',
    'place_limit_long', 'place_limit_short', 'cancel_order', 'amend_order',
]
```

A model that followed the instruction failed structured-output validation →
`node_analyze` error path → `llm_signal=None` → gate reason `llm_failed`, wasting the
cycle exactly when it matters (position open).

## The fix

The backlog's designated small safe fix: the instruction line deleted from
`_render_task` (`ai-signal-generator/app/prompt/builder.py`). Scaling-in support
(adding `increase` to the Literal + guard sizing + dispatch) remains deliberately
unbuilt — it's a feature, not this bug.

## Verification (pasted)

Redeployed via `./scripts/redeploy.sh ai-signal-generator`; health
`{"status":"ok","service":"ai-signal-generator"}`. The word is gone from the built image:

```
$ docker compose exec ai-signal-generator grep -c "increase" /app/app/prompt/builder.py
0
```

Position-open task section rendered in the running container — every offered action is
schema-valid:

```
YOUR TASK:
Evaluate whether the original thesis for this long position is still valid.
Consider all new data since the position was opened.
If the thesis is intact: output "hold" or "adjust_stops" with updated levels.
If the thesis is weakening: output "partial_close".
If the thesis is invalidated or a new risk is present: output "close_long".
...
contains increase: False
```

ROADMAP: backlog bullet removed; fixed-bugs table row added.

**Watch item:** `llm_failed` count over the next week should drop toward zero on
position-open days (`SELECT ... FILTER (WHERE gate_rejection_reason='llm_failed')` by
day — was 2/5/1/5/6/4 across 07-01→07-06).
