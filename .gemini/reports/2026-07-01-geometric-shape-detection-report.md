# Geometric Shape Detection — Implementation Report
**Date:** 2026-07-01  
**Branch:** main  
**Services changed:** `ai-signal-generator` (Phases 1–3); `dashboard-api` / `dashboard-ui` (Phase 4 deferred)

---

## Phase 1 — Swing detection + shape classification module

### Files created
- `ai-signal-generator/app/data/geometry.py`
- `ai-signal-generator/tests/__init__.py`
- `ai-signal-generator/tests/test_geometry.py`

### Thresholds chosen (documented in module docstring)
| Constant | Value | Rationale |
|---|---|---|
| `SWING_WINDOW` | 3 bars | Symmetric fractal window; 3+1+3=7 bars detects clear local extremes without being too coarse |
| `FLAT_THR_PCT` | 0.05% per bar | $100 price must move $0.05/bar before boundary is called "trending" |
| `PARALLEL_THR_PCT` | 0.04% per bar | Difference in slope %-rates; lines diverging by <0.04%/bar are called parallel |
| `CONV_THR_PCT` | 0.01% per bar | Any convergence >1% of price per 100 bars counts as a converging shape |
| `TOUCH_TOL_PCT` | 0.60% | Swing point within 0.6% of trendline value counts as a confirmed touch |
| `STRONG_R2` | 0.70 | Standard "good fit" threshold; both trendlines must exceed this |
| `MIN_R2_PATTERN` | 0.30 | Below this the trendline is noise; forces `no_pattern` rather than spurious classification |

### Full test run output (inside container: Python 3.12.13, pytest 9.1.1)
```
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0 -- /usr/local/bin/python
cachedir: .pytest_cache
rootdir: /tmp
plugins: anyio-4.14.0, langsmith-0.9.1
collecting ... collected 14 items

../tmp/test_geometry.py::test_horizontal_channel PASSED                  [  7%]
../tmp/test_geometry.py::test_ascending_channel PASSED                   [ 14%]
../tmp/test_geometry.py::test_descending_channel PASSED                  [ 21%]
../tmp/test_geometry.py::test_ascending_triangle PASSED                  [ 28%]
../tmp/test_geometry.py::test_descending_triangle PASSED                 [ 35%]
../tmp/test_geometry.py::test_rising_wedge PASSED                        [ 42%]
../tmp/test_geometry.py::test_falling_wedge PASSED                       [ 50%]
../tmp/test_geometry.py::test_no_pattern_diverging PASSED                [ 57%]
../tmp/test_geometry.py::test_position_in_range PASSED                   [ 64%]
../tmp/test_geometry.py::test_too_few_candles PASSED                     [ 71%]
../tmp/test_geometry.py::test_insufficient_swings PASSED                 [ 78%]
../tmp/test_geometry.py::test_empty_candles PASSED                       [ 85%]
../tmp/test_geometry.py::test_output_keys_present PASSED                 [ 92%]
../tmp/test_geometry.py::test_fit_quality_values PASSED                  [100%]

============================== 14 passed in 1.36s ==============================
```

### Module location in container
```
$ docker compose exec ai-signal-generator find /app -name "geometry.py"
/app/app/data/geometry.py
```

---

## Phase 2 — Wire into the ingest pipeline

### Migration 035
```sql
-- db/migrations/035_use_geometry_flag.sql
ALTER TABLE public.ai_strategy_config
    ADD COLUMN IF NOT EXISTS use_geometry boolean DEFAULT false NOT NULL;
```

**Migration output:**
```
BEGIN
ALTER TABLE
COMMIT
psql: NOTICE:  Migration 035 verified OK: use_geometry column present, default=false
DO
```

### Files changed
- `ai-signal-generator/app/graph/state.py` — added `geometry_data: Optional[dict]`
- `ai-signal-generator/app/graph/nodes/node_ingest.py` — imported `detect_geometry`, fetch OHLCV when `use_geometry` OR `use_technical`, call geometry branch when `use_geometry=True`

### Container verification
```
$ docker compose exec ai-signal-generator grep -n "geometry_data|use_geometry|detect_geometry" \
    /app/app/graph/nodes/node_ingest.py /app/app/graph/state.py

/app/app/graph/nodes/node_ingest.py:3:from app.data.geometry import detect_geometry
/app/app/graph/nodes/node_ingest.py:30:    geometry_data        = None
/app/app/graph/nodes/node_ingest.py:32:    if sc.get('use_technical') or sc.get('use_geometry'):
/app/app/graph/nodes/node_ingest.py:47:            if sc.get('use_geometry'):
/app/app/graph/nodes/node_ingest.py:49:                    geometry_data = detect_geometry(ohlcv_data['candles']) or None
/app/app/graph/nodes/node_ingest.py:113:        'geometry_data':        geometry_data,
/app/app/graph/state.py:28:    geometry_data:       Optional[dict]
```

### Live analysis cycle (strategy ai-btc-6f8c, use_geometry=true)
```
POST /internal/trigger {"strategy_id":"ai-btc-6f8c","trigger_reason":"phase2_geometry_verify"}
→ {"signal_log_id":229,"proposed_action":"hold","confidence":0.55,"gate_passed":false,
   "gate_rejection_reason":"hold_or_adjust","webhook_fired":false,"dry_run":true,
   "data_fetch_errors":[]}
```
`data_fetch_errors` is empty — geometry ran without exception.

### In-process geometry_data population test
```python
# ascending-channel candles (70/bar slope on 65000 asset → 0.108%/bar > FLAT_THR)
result = detect_geometry(candles)  # called inside simulated ingest branch
# → {"shape":"ascending_channel","upper_boundary":73330.0,"lower_boundary":71330.0,
#    "upper_touches":8,"lower_touches":8,"convergence_pct_per_bar":-0.0,
#    "pattern_age_bars":56,"position_in_range_pct":98.0,"fit_quality":"strong"}
# ASSERTION PASSED
```

---

## Phase 3 — Prompt section + new template

### Migration 036
```sql
-- db/migrations/036_geometric_range_template.sql
INSERT INTO public.ai_prompt_templates (id, name, description, system_prompt)
VALUES ('geometric_range', 'Geometric Range & Breakout', ..., $$...$$);
```

**Migration output:**
```
BEGIN
INSERT 0 1
COMMIT
psql: NOTICE:  Migration 036 verified OK: template "Geometric Range & Breakout" inserted (id=geometric_range)
DO
```

### Files changed
- `ai-signal-generator/app/prompt/builder.py` — added `_render_geometry()` section renderer and wired it into `build_prompt()` at section 2.5 (after technical, before sentiment)

### Guard assertions (in deployed container)
```
All _render_geometry guard assertions passed
Container builder.py path: /app/app/prompt/builder.py
```
Guards confirmed: `no_pattern` → empty; `use_geometry=False` → empty; `geometry_data=None` → empty.

### Actual build_prompt() output — 3 shapes

**Shape 1: Horizontal Channel (position_in_range_pct=53%, mid-range)**
```
═══════════════════════════════════════════════════════════
MATP AI ANALYSIS — BTC-USDT — 4h
Generated: 2026-07-01 19:01:10 UTC
Analysis Trigger: test
═══════════════════════════════════════════════════════════

GEOMETRIC PATTERN:
Detected Shape:       Horizontal Channel
Fit Quality:          strong
Upper Boundary:       68500.0
Lower Boundary:       63200.0
Upper Touches:        4
Lower Touches:        3
Position in Range:    53.0%  (0=at lower boundary, 100=at upper)
Pattern Age:          62 bars
Convergence Rate:     0 (parallel boundaries)

PORTFOLIO CONTEXT:
Account Balance:      (resolved at execution time)
Last Signal:          N/A

STRATEGY INSTRUCTIONS:
[geometric_range template — 4 phases as written in migration 036]

YOUR TASK: [standard task block]
```

**Shape 2: Rising Wedge (position=15.4%, 88 bars old, convergence=+0.094%/bar)**
```
GEOMETRIC PATTERN:
Detected Shape:       Rising Wedge
Fit Quality:          strong
Upper Boundary:       67100.0
Lower Boundary:       65800.0
Upper Touches:        3
Lower Touches:        3
Position in Range:    15.4%  (0=at lower boundary, 100=at upper)
Pattern Age:          88 bars
Convergence Rate:     +0.094% of price per bar (boundaries closing in)
```
→ LLM sees: near lower boundary, rising-wedge downside bias, 88 bars old (apex near) → should favour caution/small long or HOLD per Phase 3.

**Shape 3: Ascending Triangle (position=78%, 55 bars, convergence=+0.058%/bar)**
```
GEOMETRIC PATTERN:
Detected Shape:       Ascending Triangle
Fit Quality:          strong
Upper Boundary:       68200.0
Lower Boundary:       64100.0
Upper Touches:        5
Lower Touches:        4
Position in Range:    78.0%  (0=at lower boundary, 100=at upper)
Pattern Age:          55 bars
Convergence Rate:     +0.058% of price per bar (boundaries closing in)
```
→ LLM sees: approaching upper boundary (78%), ascending triangle upside bias, 5 touches on upper resistance — should caution against short per Phase 3, watch for breakout confirmation.

### Redeploy confirmation
```
./scripts/redeploy.sh ai-signal-generator
→ ✓ ai-signal-generator redeployed.
```
Container state: `Up` (health: healthy after warm-up).

---

## Phase 4 — Dashboard toggle

### Files changed
- `dashboard-ui/src/pages/Strategies.tsx` — added `use_geometry` to `AiFormState`, `AI_FORM_DEFAULTS`, `DATA_SOURCES`, `handleEdit`, `handleEditSubmit`, `handleAddStrategy`
- `dashboard-api/src/routes/ai.ts` — added `'use_geometry'` to `ALLOWED_CONFIG_FIELDS`; added `use_geometry: aiConfig.use_geometry` to `mockState.strategy_config` in the preview-prompt endpoint

### Verification

**Bundle contains new label:**
```
$ docker compose exec -T dashboard-ui grep -rl 'Geometric Pattern' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-CJSY5U9c.js
```

**API health:**
```
$ docker compose exec nginx wget -qO- http://dashboard-api:8003/health
{"status":"ok","service":"dashboard-api"}
```

**DB column present with correct values:**
```
$ docker compose exec -T postgres psql -U matp -d matp -c \
    "SELECT strategy_id, use_geometry FROM ai_strategy_config LIMIT 3;"
     strategy_id     | use_geometry
--------------------+--------------
 hype-breakout-da2e | f
 ai-btc-6f8c        | t
(2 rows)
```

**Config GET returns `use_geometry`:**
```
$ curl -s "http://localhost/api/ai/strategies/ai-btc-6f8c/config" | python3 -c "..."
use_geometry: True
```

**Redeploys:**
```
./scripts/redeploy.sh dashboard-api  → ✓ dashboard-api redeployed.
./scripts/redeploy.sh dashboard-ui   → ✓ dashboard-ui redeployed. (asset: index-CJSY5U9c.js)
```

### How it works
`DATA_SOURCES` is the single source of truth for the checkbox grid — both Add and Edit modals
iterate over it, so adding `{ key:'use_geometry', label:'Geometric Pattern Detection' }` to the
array is the only JSX change needed. The checkbox appears automatically in both modals.

The `TemplatePreview` "Active Data Sources" chip list also picks it up since it filters
`DATA_SOURCES` by the current form state.

---

## Summary of all migrations applied

| # | File | What |
|---|---|---|
| 035 | `035_use_geometry_flag.sql` | `use_geometry boolean DEFAULT false` on `ai_strategy_config` |
| 036 | `036_geometric_range_template.sql` | `geometric_range` template inserted into `ai_prompt_templates` |
