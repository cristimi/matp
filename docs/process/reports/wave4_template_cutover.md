# Wave 4 Report — Template Cutover (all 7 target-state prompts live)

**Date:** 2026-07-07
**Scope:** `db/migrations/046` (template text), `ai_strategy_config` toggle enablement for
the two live strategies (data, not schema), this report. **Zero code changes** — the Wave
1–3 plumbing is exercised as-is; no service was rebuilt or redeployed (templates and
strategy config are read fresh from the DB every cycle, verified below).
**Design authority:** `docs/design/ai_prompts/10_*.md–16_*.md` (Phase-1 target-state
prompts), `20_plumbing_specs.md` §11, and the docs' own cutover instruction: the drafts
"become applicable only after the corresponding entries in `20_plumbing_specs.md` are
built, at which point the inline tags are stripped" — all nine entries were built in
Waves 1–3.

Every claim below is backed by pasted command output from the live stack.

---

## 1. Mechanism — migration 046, generated deterministically

The 7 `system_prompt` bodies were extracted from the design docs' "Draft `system_prompt`"
sections by a generator script (tag-strip regex `` ?`\[(?:DELIVERED|REQUIRES PLUMBING:[^\]]*)\]` ``,
refusing to emit if any tag survives), dollar-quoted into UPDATEs, and pinned with md5
checksums in the migration's self-verifying `DO $$` block. Generator output:

```
trend_following   3354 chars  md5=ad14bdabc169e31c9294539c9c540682
mean_reversion    3569 chars  md5=44883392549927260afd68ce9067ae9a
breakout          3688 chars  md5=11451d2dafed7f7be503c494a5e0137d
scalper           3484 chars  md5=072e07c32d153a48a99d47f025ac1581
conservative      3695 chars  md5=d557f866f33ffe5344acf723f72ab633
range_rotation    3496 chars  md5=5be666524490c9040011fc0395373d72
geometric_range   7324 chars  md5=04c2e637c45ac20b3e40f9daad0f976e
```

Extraction safety: every design doc was checked to contain exactly one `## Draft
\`system_prompt\`` heading with nothing after the draft body, and the emitted SQL greps
clean for both tag forms (`grep -c` → `0`).

## 2. Revert path — in-DB snapshot before applying

```
$ docker compose exec postgres psql -U matp -d matp -c "CREATE TABLE ai_prompt_templates_pre_wave4 AS SELECT * FROM ai_prompt_templates;"
SELECT 7
 count | sum
-------+------
     7 | 9264
(1 row)
```

Instant revert: `UPDATE ai_prompt_templates p SET system_prompt = b.system_prompt FROM
ai_prompt_templates_pre_wave4 b WHERE b.id = p.id;` (old text also lives in migrations
006/010/036 in git). Drop the snapshot table once the new prompts have proven out.

## 3. Migration applied and md5-verified

```
$ docker compose exec -T postgres psql -U matp -d matp -v ON_ERROR_STOP=1 < db/migrations/046_ai_prompt_templates_target_state.sql
BEGIN
UPDATE 1
UPDATE 1
UPDATE 1
UPDATE 1
UPDATE 1
UPDATE 1
UPDATE 1
COMMIT
NOTICE:  Migration 046 verified OK: 7 target-state prompts installed (md5-pinned)
DO

$ docker compose exec postgres psql -U matp -d matp -c "SELECT id, length(system_prompt) AS len FROM ai_prompt_templates ORDER BY id;"
       id        | len
-----------------+------
 breakout        | 3688
 conservative    | 3695
 geometric_range | 7324
 mean_reversion  | 3569
 range_rotation  | 3496
 scalper         | 3484
 trend_following | 3354
(7 rows)
```

(Previous lengths for contrast: five seed templates at ~390–440 chars, range_rotation
1913, geometric_range 5337.)

## 4. Toggle enablement — Wave 4 un-inerts the fields for the live strategies

Both live strategies run `geometric_range`, whose consumption table (design doc 16) lists
`volume_profile_hvn_lvn`, `orderbook_depth`, `cvd_delta`, `mtf_structure`,
`economic_calendar`. The four available ones were enabled; `use_economic_calendar` stays
**false** per the ROADMAP blocker (Finnhub calendar is paid-tier — "Wave 4 must not assume
this field is available"):

```
$ docker compose exec postgres psql -U matp -d matp -c "UPDATE ai_strategy_config SET use_mtf_structure=true, use_orderbook=true, use_volume_profile=true, use_cvd=true WHERE strategy_id IN ('ai-btc-6f8c','hype-breakout-da2e') RETURNING strategy_id, use_mtf_structure, use_orderbook, use_volume_profile, use_cvd, use_economic_calendar;"
    strategy_id     | use_mtf_structure | use_orderbook | use_volume_profile | use_cvd | use_economic_calendar
--------------------+-------------------+---------------+--------------------+---------+-----------------------
 hype-breakout-da2e | t                 | t             | t                  | t       | f
 ai-btc-6f8c        | t                 | t             | t                  | t       | f
(2 rows)

UPDATE 2
```

`use_momentum_divergence` / `use_volatility_regime` / `use_funding_history` /
`use_liquidations` stay false on both — they are not in `geometric_range`'s consumption
table (token budget is spent per the Phase-1 tables, not globally). The other five
templates have **no strategies attached**; their toggles become relevant when a strategy
adopts them (dashboard toggle exposure remains a ROADMAP item — DB-settable until then).

## 5. End-to-end verification — both live strategies, real pipeline, no LLM call

Assembled in-container exactly as the scheduler does (same SQL, real exchange resolution,
real `node_ingest`, DB-loaded template), stopping before the LLM call:

```
===== ai-btc-6f8c (hyperliquid BTC-USDT) =====
data_fetch_errors: []
estimated_tokens:  2620
  section 'MULTI-TIMEFRAME STRUCTURE:'           present=True
  section 'VOLUME PROFILE (lookback window):'    present=True
  section 'ORDER BOOK:'                          present=True
  section 'ORDER FLOW (CVD):'                    present=False
  section 'LIQUIDATIONS:'                        present=False
  new template phrase 'Confluence upgrade'         present=True
  new template phrase 'Book check before resting'  present=True
  new template phrase 'Order-flow tiebreak'        present=True
  new template phrase 'Pattern-vs-trend conflict'  present=True
  retained 036 phrase 'never leave a fade resting into an apex' present=True
  stray tag present=False

===== hype-breakout-da2e (blofin HYPE-USDT) =====
data_fetch_errors: []
estimated_tokens:  2942
  section 'MULTI-TIMEFRAME STRUCTURE:'           present=True
  section 'VOLUME PROFILE (lookback window):'    present=True
  section 'ORDER BOOK:'                          present=True
  section 'ORDER FLOW (CVD):'                    present=True
  section 'LIQUIDATIONS:'                        present=False
  new template phrase 'Confluence upgrade'         present=True
  new template phrase 'Book check before resting'  present=True
  new template phrase 'Order-flow tiebreak'        present=True
  new template phrase 'Pattern-vs-trend conflict'  present=True
  retained 036 phrase 'never leave a fade resting into an apex' present=True
  stray tag present=False
```

Expected absences behave exactly per design:

- `ORDER FLOW (CVD)` absent on **ai-btc (hyperliquid)** — the known ccxt user-fills-only
  gap (Wave-3 report); present with real numbers on **hype-breakout (blofin)**. The new
  prompt's calibration rule covers this: "Breakout trades without order-flow confirmation
  available … cap at 0.75."
- The rendered `SCHEDULED EVENTS (next …h):` header is **not** in either prompt (the raw
  phrase "SCHEDULED EVENTS" matches only inside the template's own event-guard rule —
  verified by line-level grep of the dumped prompt). With no section, the event guard
  no-ops, which is the honest state while the provider is blocked.
- `LIQUIDATIONS:` absent — documented no-op, not referenced by `geometric_range`.
- `data_fetch_errors: []` on both — the two per-exchange gaps (hyperliquid CVD, blofin
  open-interest) degrade inside their fetchers to `None`, per the established pattern.

Data excerpt from the assembled ai-btc prompt (real cycle-quality data feeding the new
rules — note the live resting order the prompt is managing):

```
MULTI-TIMEFRAME STRUCTURE:
  1h: sideways  — price above EMA50, EMA50 above EMA200; swings mixed
  4h: uptrend   — price above EMA50, EMA50 below EMA200; swings mixed
  1d: downtrend — price below EMA50, EMA50 below EMA200; swings LH/LL

VOLUME PROFILE (lookback window):
POC (Point of Control): 63024.51
Value Area High:        76040.63
Value Area Low:         58518.93
HVN Levels:             77542.49, 80546.21
LVN Levels:             65527.61, 68531.33, 70033.19

GEOMETRIC PATTERN:
Detected Shape:       Broadening
Fit Quality:          strong
Upper Boundary:       64752.11039
Lower Boundary:       60506.403863
...
OPEN ORDERS (this strategy's resting limit orders):
  order_id=56095673578  side=sell  price=64752.0  size=0.0077  status=resting

ORDER BOOK:
Bid Depth (±1% / ±2%):  $1,102,174 / $1,102,174
Ask Depth (±1% / ±2%):  $8,360,293 / $8,360,293
Depth Imbalance (1% bid/ask): 0.132 (asks heavier)
Largest Bid Wall:       $235,191 @ 63208.0
Largest Ask Wall:       $1,540,362 @ 63229.0
```

Token budget: 2620 / 2942 estimated tokens (vs ~2000 pre-cutover with the three Wave-1
sections during the reverted live test) — within the +40–60-line envelope §11 predicted.

## 6. Activation — no redeploy needed

`app/prompt/templates.py::load_template` SELECTs from `ai_prompt_templates` on every
`build_prompt` call, and the scheduler re-reads `strategies` + `ai_strategy_config` fresh
at the start of every cycle (`scheduler.py::_build_initial_state`). Both live strategies
therefore pick up the new prompt and the enabled sections on their next scheduled cycle
automatically. No container was rebuilt, restarted, or recreated this wave.

## 7. Untouched surfaces / follow-ups

- No service code changed; executor / listener / dashboard-api / dashboard-ui /
  strategy-tester untouched. The wave's commits contain only `db/migrations/046_*.sql`
  and this report.
- **Follow-ups now live on the ROADMAP:** dashboard toggle exposure (new toggles are
  DB-settable only), tester `ai_strategy_config` parity, `economic_calendar` provider
  access, `liquidation_data` source, and the pre-existing `_render_task` `increase`
  schema discrepancy (unchanged by this wave — the cutover touched template text, not
  `_render_task`).
- Watch item: first few live cycles of both strategies — the new prompts are stricter
  about when to place (confluence/book checks), so a drop in placement frequency is
  expected behavior, not a regression. The `ai_prompt_templates_pre_wave4` snapshot
  table is the one-command revert if live behavior degrades.
