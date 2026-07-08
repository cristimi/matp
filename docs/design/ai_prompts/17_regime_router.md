# Prompt — `regime_router` (applied)

> **Status: APPLIED** in `db/migrations/048_limit_orders_expansion.sql` — unlike the
> `1x_*.md` target-state drafts, every field this prompt consumes was already plumbed
> (Waves 1–3), so the migration text is the source of truth. This doc records the
> design rationale; if the prompt changes, change it via a new migration and update here.

**Template id:** `regime_router` · **Name:** Regime Router
**Data sources consumed:** `mtf_structure`, `volatility_regime`, `momentum_divergence`,
`volume_profile_hvn_lvn`, `orderbook_depth`, `cvd`, `funding_history`,
`economic_calendar`, `geometry`, plus the resting-order context (`use_limit_orders`).

---

## What it is

A meta-strategy ("8th template") that classifies the market regime each cycle —
TRENDING / RANGING / COMPRESSED / EXTENDED / UNCLEAR — and then applies **only** that
regime's playbook, condensed from the specialist templates. UNCLEAR ⇒ hold.

## Design decisions

1. **Not a concatenation.** The 7 specialist prompts total ~28k chars and contradict
   each other by design (mean-reversion fades what trend-following chases). The router
   is written fresh: a classification gate (Phase 0) + four condensed playbooks.
2. **Hold-bias is load-bearing.** The known failure mode of a multi-playbook prompt is
   rationale-shopping — some playbook always loosely fits, so the model drifts toward
   trading every cycle. Countermeasures baked into the text: a regime needs ≥2
   independent confirmations; competing-regime evidence forces UNCLEAR rather than a
   confidence discount; the calibration section says explicitly that most cycles hold.
3. **Hybrid order policy.** Resting limits are allowed only in the fade playbooks
   (RANGING, EXTENDED) and only when the OPEN ORDERS section is present. Momentum
   playbooks (TRENDING, COMPRESSED) are market-entry only: a limit placed beyond price
   in the break direction fills immediately as a taker order — an unconfirmed entry —
   and passive limits suffer maximal adverse selection on breakouts.
4. **Attribution.** The reasoning field must open by naming the chosen regime and its
   confirmations, so post-hoc analysis can separate classification errors from
   playbook-execution errors.
5. **Position stickiness.** An open position is managed under the playbook that opened
   it; a regime flip against the position is thesis invalidation (close), never a
   hand-off to another playbook.
6. **geometric_range's full order-management mode is not folded in.** Its
   stateful boundary-order workflow (re-fit amendments, apex handling, breakout
   override sequencing) stays a specialist. The router's RANGING playbook borrows only
   the simple edge-resting rules.

## Related change (same migration)

Migration 048 also added `ai_strategy_config.use_limit_orders` (default false), which
grants the `place_limit_long/short` / `amend_order` / `cancel_order` action set to
non-geometry strategies: `node_ingest.py` fetches open orders and `builder.py` renders
the OPEN ORDERS section when `use_geometry OR use_limit_orders`. On the back of it,
`mean_reversion` (new PHASE 4) and `range_rotation` (new PHASE 2B) gained resting-limit
execution blocks, conditional on the OPEN ORDERS section being present — with the
toggle off, those templates read exactly as before (honest-absence pattern, as in 046).
See the addenda in `11_mean_reversion.md` / `15_range_rotation.md`.
