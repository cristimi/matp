# Fix: geometry never appears in the AI Signal Log data-source chips

## Symptom

`_data_sources_used(sc)` in `ai-signal-generator/app/graph/nodes/node_dispatch.py` builds
the `data_sources_used` array stored on each `ai_signal_log` row, which the dashboard's
AI Signal Log chip row renders verbatim. The function had cases for `technical`,
`fear_greed`, `funding_rate`, `open_interest`, `news`, `btc_dominance`, `macro` — but no
`geometry` case. So a geometry chip could never appear, even for strategies with
`use_geometry = true` (e.g. `hype-breakout-da2e`, template `geometric_range`), even when a
`GEOMETRIC PATTERN` section rendered in the prompt. Display-only gap; signal behavior was
unaffected.

## Change

`ai-signal-generator/app/graph/nodes/node_dispatch.py`, `_data_sources_used`: added a
`geometry` case gated on `sc.get('use_geometry')`, placed after the `technical` line.

```python
def _data_sources_used(sc: dict) -> list[str]:
    sources = []
    if sc.get('use_technical'):    sources.append('technical')
    if sc.get('use_geometry'):     sources.append('geometry')
    if sc.get('use_fear_greed'):   sources.append('fear_greed')
    if sc.get('use_funding_rate'): sources.append('funding_rate')
    if sc.get('use_open_interest'): sources.append('open_interest')
    if sc.get('use_news'):         sources.append('news')
    if sc.get('use_btc_dominance'): sources.append('btc_dominance')
    if sc.get('use_macro'):        sources.append('macro')
    return sources
```

No UI edit was needed — `dashboard-ui/src/pages/AiSignalLog.tsx` renders the
`data_sources_used` array generically (uppercases each string, no per-key label map).

Deployed with `./scripts/redeploy.sh ai-signal-generator` (rebuild + force-recreate).

## Verification

**1. Source in the running container reflects the change:**

```
$ docker compose exec ai-signal-generator grep -n "use_geometry\|use_technical" app/graph/nodes/node_dispatch.py
14:    if sc.get('use_technical'):    sources.append('technical')
15:    if sc.get('use_geometry'):     sources.append('geometry')
```

**2. End-to-end.** The redeploy's container recreation triggered the scheduler's normal
startup-immediate-cycle for both configured strategies, giving a real cycle for
`hype-breakout-da2e` (`use_geometry = true`) without needing to wait for the next
scheduled run:

```
2026-07-04 17:33:11,473 [INFO] app.scheduler: Triggering cycle strategy=hype-breakout-da2e reason=startup
2026-07-04 17:36:36,226 [INFO] app.graph.nodes.node_dispatch: strategy=hype-breakout-da2e action=hold gate=False reason=hold_or_adjust — no webhook
```

Newest `ai_signal_log` row for that strategy after the cycle:

```
$ docker compose exec postgres psql -U matp -d matp -c "SELECT triggered_at, trigger_reason, data_sources_used FROM ai_signal_log WHERE strategy_id = 'hype-breakout-da2e' ORDER BY triggered_at DESC LIMIT 1;"

         triggered_at         | trigger_reason |                        data_sources_used
------------------------------+----------------+-----------------------------------------------------------------
 2026-07-04 17:33:11.47326+00 | startup        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
(1 row)
```

`geometry` is now present in `data_sources_used`, confirming the chip will render.

Unrelated, pre-existing noise observed during this same cycle (not touched, out of scope
for this fix): `order-listener` returned `502 Bad Gateway` on
`GET /strategies/hype-breakout-da2e/orders`, logged by `node_ingest` as "Open orders fetch
failed."
