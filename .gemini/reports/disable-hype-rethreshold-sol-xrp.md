# Disable HYPE strategy + re-threshold SOL/XRP (2026-07-19)

Follow-up to the honest portfolio analysis: HYPE AI Mean Reversion had the worst live
expectancy (−$9.68 over 6 trades, 33% win rate, highest leverage tier), and SOL/XRP had
produced **zero** entry proposals ever (SOL: 237 signals all "hold", XRP: 413 all "hold")
while burning LLM tokens every cycle.

## Changes applied (via dashboard API, which updates DB + reconciles schedulers)

1. **HYPE AI Mean Reversion (`hype-breakout-da2e`) disabled.**
   Pre-check confirmed it was flat (no open positions, no live orders).

   ```
   $ curl -X POST http://localhost/api/dashboard/strategies/hype-breakout-da2e/stop
   {"stopped":"hype-breakout-da2e","enabled":false,"legs_closed":0,"errors":[]}
   ```

2. **SOL (`sol-ai-6486`) and XRP (`xrp-ai-3844`) confidence_threshold 0.65 → 0.55**
   (API floor is 0.50), so the speculative band (0.50–0.65) of the model's own
   confidence scale can now pass the guard gate.

   ```
   $ curl -X PUT -d '{"confidence_threshold": 0.55}' .../api/ai/strategies/sol-ai-6486/config
   {..."confidence_threshold":0.55,"updated_at":"2026-07-19T15:07:03.005Z"...}
   $ curl -X PUT -d '{"confidence_threshold": 0.55}' .../api/ai/strategies/xrp-ai-3844/config
   {..."confidence_threshold":0.55,"updated_at":"2026-07-19T15:07:03.088Z"...}
   ```

## Verification

DB state after:

```
         id         | enabled | confidence_threshold
--------------------+---------+----------------------
 hype-breakout-da2e | f       |                0.590
 sol-ai-6486        | t       |                0.550
 xrp-ai-3844        | t       |                0.550
```

Signal-generator schedulers reacted live (no restart needed):

```
app.main: reconcile: stopped scheduler+watcher strategy=hype-breakout-da2e
app.main: reconcile: reloaded (interrupted) strategy=sol-ai-6486
app.scheduler: Scheduler strategy=sol-ai-6486 config reload — recomputing wake time, no immediate cycle
app.main: reconcile: reloaded (interrupted) strategy=xrp-ai-3844
app.scheduler: Scheduler strategy=xrp-ai-3844 config reload — recomputing wake time, no immediate cycle
```

## Honest caveat — threshold is probably not the binding constraint

Since the 2026-07-14 prompt softening (commit `a432082`), SOL produced 0 non-hold
proposals in 149 cycles and XRP 0 in 292. The guard threshold only gates proposals that
exist; the `trend_following` and `breakout` templates are strict multi-leg AND-gates with
multiple explicit "output hold" instructions, so the models rarely propose at all.
Lowering the threshold widens the gate but cannot force proposals through it. If SOL/XRP
are still at 0 proposals after ~2 weeks, the next lever is softening those two templates
(or per-strategy `custom_instructions`), not further threshold cuts.

No source files changed — DB config only; no redeploy required.
