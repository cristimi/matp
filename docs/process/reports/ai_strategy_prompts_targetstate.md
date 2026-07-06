# Report — Target-State AI Strategy Prompts + Plumbing Spec (Design Only)

Date: 2026-07-06. Scope per review of Phase 0: all 9 `[REQUIRES PLUMBING]` fields from
`docs/design/ai_prompts/00_audit.md` §3.1, across all 7 live templates.

## What was delivered

All artifacts are **new files under `docs/` only**:

- **Phase 0** — `docs/design/ai_prompts/00_audit.md`: live template inventory (verbatim from
  the DB), the `[DELIVERED]` field whitelist from `builder.py`, and the desired-field
  (`[REQUIRES PLUMBING]`) catalog of 9 field-ids.
- **Phase 1** — one target-state `system_prompt` draft per template
  (`10_trend_following.md` … `16_geometric_range.md`). Every data reference carries an
  inline `[DELIVERED]` or `[REQUIRES PLUMBING: <field-id>]` tag; emit-action guidance stays
  inside the `LLMSignalOutput` Literal set; no draft restates the confidence scale or task
  instruction (`_render_task` appends both) — calibration nuance only; provider-agnostic;
  each structured per the 036 bar (validity gate → entry/exit logic → calibration).
- **Phase 2** — `docs/design/ai_prompts/20_plumbing_specs.md`: a build recipe per field-id
  (fetcher module + signature modeled on a named existing fetcher, `node_ingest.py` call
  site + toggle, `AgentState` key, `_render_*` section + slot in `build_prompt()` ordering,
  migration spec, effort/risk). Migration 045 specced (not applied) for 8 new toggles;
  `economic_calendar` reuses the existing unwired `use_economic_calendar` column;
  `allocation_context` deliberately NOT specced — flagged as dependent on ROADMAP Open
  Questions #1/#2 rather than pre-empting them.

## Artifact listing

```
$ ls docs/design/ai_prompts/
00_audit.md
10_trend_following.md
11_mean_reversion.md
12_breakout.md
13_scalper.md
14_conservative.md
15_range_rotation.md
16_geometric_range.md
20_plumbing_specs.md
```

## Verification 1 — sampled `[DELIVERED]` tags are rendered today

Six labels sampled across six different `_render_*` sections, grepped in
`ai-signal-generator/app/prompt/builder.py`:

```
$ grep -n "Position in Range\|RSI(14):\|Funding Rate:\|BTC Dominance:\|order_id=\|Volume (vs 20MA)" ai-signal-generator/app/prompt/builder.py
95:            f"RSI(14):          {ind['rsi_14']} — {ind.get('rsi_interpretation', '')}"
123:            f"Volume (vs 20MA):  {abs(vol_pct)}% {direction} average"
149:                f"  order_id={_v(o.get('order_id'))}  side={_v(o.get('side'))}  "
174:            body.append(f"Funding Rate:         {fr['rate']}% ({fr['interpretation']})")
227:                f"BTC Dominance:        {bd.get('btc_dominance', 'N/A')}% "
287:        f"Position in Range:    {_v(gd.get('position_in_range_pct'))}{position_suffix}",
```

## Verification 2 — `[REQUIRES PLUMBING]` fields are absent today

One pattern per field-id (covering all nine), grepped case-insensitively in **both**
`builder.py` and `node_ingest.py`; every pattern returns nothing:

```
$ for pat in "cvd" "orderbook\|order_book" "hvn\|volume_profile\|poc_price" "percentile" "liquidation" "calendar\|economic" "mtf\|swing_structure" "rsi_divergence\|momentum_divergence\|squeeze_flag" "funding_history\|funding_percentile"; do ... grep -in "$pat" ai-signal-generator/app/prompt/builder.py ai-signal-generator/app/graph/nodes/node_ingest.py ...; done
pattern: cvd                                 no matches
pattern: orderbook\|order_book               no matches
pattern: hvn\|volume_profile\|poc_price      no matches
pattern: percentile                          no matches
pattern: liquidation                         no matches
pattern: calendar\|economic                  no matches
pattern: mtf\|swing_structure                no matches
pattern: rsi_divergence\|momentum_divergence\|squeeze_flagno matches
pattern: funding_history\|funding_percentile no matches
```

(The one near-collision was checked deliberately: `builder.py` line 294 renders a geometry
"Divergence Rate:" label, so the momentum field was named `momentum_divergence` /
`rsi_divergence` and the verification grep targets those exact names, which have no hits.)

## Verification 3 — tag coverage and tag→spec mapping

Every `[REQUIRES PLUMBING: …]` tag across the seven drafts, aggregated; exactly the nine
field-ids specced in `20_plumbing_specs.md`, no strays:

```
$ grep -rhoE "REQUIRES PLUMBING: [a-z_]+" docs/design/ai_prompts/1[0-6]_*.md | sort | uniq -c | sort -rn
     24 REQUIRES PLUMBING: volume_profile_hvn_lvn
     16 REQUIRES PLUMBING: mtf_structure
     13 REQUIRES PLUMBING: orderbook_depth
     12 REQUIRES PLUMBING: cvd_delta
      9 REQUIRES PLUMBING: funding_history
      8 REQUIRES PLUMBING: volatility_regime
      7 REQUIRES PLUMBING: momentum_divergence
      7 REQUIRES PLUMBING: economic_calendar
      3 REQUIRES PLUMBING: liquidation_data
```

Per-draft tag counts (every draft carries both tag kinds; no untagged data references were
written — tagging was applied at each reference, not once per field):

```
docs/design/ai_prompts/10_trend_following.md  DELIVERED=12  PLUMBING=18
docs/design/ai_prompts/11_mean_reversion.md   DELIVERED=15  PLUMBING=12
docs/design/ai_prompts/12_breakout.md         DELIVERED=10  PLUMBING=18
docs/design/ai_prompts/13_scalper.md          DELIVERED=9   PLUMBING=14
docs/design/ai_prompts/14_conservative.md     DELIVERED=17  PLUMBING=8
docs/design/ai_prompts/15_range_rotation.md   DELIVERED=15  PLUMBING=16
docs/design/ai_prompts/16_geometric_range.md  DELIVERED=36  PLUMBING=13
```

## Verification 4 — migration number re-confirmed at spec-writing time

```
$ ls db/migrations
022_reconcile_divergence.sql
023_dynamic_allocation.sql
024_shadow_signals.sql
025_social_signal_log.sql
026_pnl_realized_nullable.sql
027_drop_webhook_enabled.sql
028_exit_reason.sql
029_social_state_shadow.sql
030_drop_dead_columns.sql
031_drop_current_price.sql
032_stop_reason.sql
033_strategy_source_social_internal.sql
034_backfill_close_orders.sql
035_use_geometry_flag.sql
036_geometric_range_template.sql
037_candle_close_buffer.sql
038_geometric_range_limit_orders.sql
039_add_entry_trigger.sql
040_push_subscriptions.sql
041_notification_log.sql
042_shadow_fired_at.sql
043_orders_signal_fee.sql
044_ai_signal_log_geometry_data.sql
_archive
README.md
```

Highest is 044 → next free number is **045**, as expected. No `045_*.sql` file was created —
the SQL is specced inside `20_plumbing_specs.md` §10 only.

## Confirmation — nothing in the running system changed

Only new `docs/` files were added across the whole task. Working tree is clean and there is
no pending diff against `origin/main` in any source directory:

```
$ git status --short
--- git status exit: clean if empty ---
$ git diff --stat origin/main -- ai-signal-generator/ db/ dashboard-api/ dashboard-ui/ docker-compose.yml nginx/
--- diff vs origin for source dirs: empty = unchanged ---
```

Files touched by this task's commits (subjects + paths only — all under
`docs/design/ai_prompts/`):

```
$ git log -3 --format='commit-subject: %s' --name-only
commit-subject: docs(design): Phase 2 — plumbing specs for the 9 [REQUIRES PLUMBING] fields

docs/design/ai_prompts/20_plumbing_specs.md
commit-subject: docs(design): Phase 1 — target-state AI strategy prompts for all 7 templates

docs/design/ai_prompts/10_trend_following.md
docs/design/ai_prompts/11_mean_reversion.md
docs/design/ai_prompts/12_breakout.md
docs/design/ai_prompts/13_scalper.md
docs/design/ai_prompts/14_conservative.md
docs/design/ai_prompts/15_range_rotation.md
docs/design/ai_prompts/16_geometric_range.md
commit-subject: docs(design): Phase 0 audit — live AI prompt templates, delivered vs desired fields

docs/design/ai_prompts/00_audit.md
```

Live `ai_prompt_templates` untouched — still 7 rows, all `created_at` timestamps predate
this task (2026-06-08 … 2026-07-01), and the drafted prompts were **not** applied:

```
$ docker compose exec postgres psql -U matp -d matp -c "SELECT id, created_at, length(system_prompt) AS prompt_len FROM ai_prompt_templates ORDER BY id;"
       id        |          created_at           | prompt_len
-----------------+-------------------------------+------------
 breakout        | 2026-06-08 20:00:12.217763+00 |        401
 conservative    | 2026-06-08 20:00:12.217763+00 |        437
 geometric_range | 2026-07-01 18:54:08.424001+00 |       5337
 mean_reversion  | 2026-06-08 20:00:12.217763+00 |        397
 range_rotation  | 2026-06-09 20:31:58.132871+00 |       1913
 scalper         | 2026-06-08 20:00:12.217763+00 |        393
 trend_following | 2026-06-08 20:00:12.217763+00 |        386
(7 rows)
```

## Report checklist

- [x] No commit hashes referenced anywhere in the design docs or this report.
- [x] Live DB, `builder.py`, `node_ingest.py`, `state.py`, schema, and all migrations
      unchanged — only new `docs/` files added (evidence above).
- [x] Drafted prompts NOT applied to `ai_prompt_templates` (evidence above); each draft
      carries a DO-NOT-APPLY banner explaining why.
- [x] Every data reference in every drafted prompt carries a `[DELIVERED]` or
      `[REQUIRES PLUMBING: <field-id>]` tag; every field-id maps to a Phase-2 spec entry
      (Verification 3).
- [x] Emit-action guidance stays within the `LLMSignalOutput` Literal set. Note recorded:
      `_render_task` mentions an "increase" action for open positions, but the Literal set
      contains no such action — the drafts therefore never suggest it (pre-existing
      discrepancy in the live scaffold, out of scope here, worth a ROADMAP look).
- [x] `use_economic_calendar` reused (no migration for it); migration 045 specced for the
      other 8 toggles, forward-only, self-verifying per the 035/036 pattern, not applied.
- [x] `allocation_context` not specced; dependency on ROADMAP #1/#2 flagged instead.
