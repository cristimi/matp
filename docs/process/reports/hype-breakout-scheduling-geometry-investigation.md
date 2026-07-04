# Investigation: `hype-breakout-da2e` — 1h cadence not honored + GEOMETRIC PATTERN missing

Investigation only. No code, schema, or data was changed. All queries below are `SELECT`;
all reproduction was done via throwaway Python snippets run inside the running
`ai-signal-generator` container, calling its existing code unmodified.

Strategy id resolved: the dashboard label `hype-breakout-da2e` **is the strategy's primary
key** (`strategies.id`), not a truncated name — its human name is "HYPE Breakout":

```
 id                  |     name      | enabled
 hype-breakout-da2e  | HYPE Breakout | t
```

Highest migration on `main` confirmed via `ls db/migrations`: `042_shadow_fired_at.sql`.

---

## Phase 1 — Scheduling / cadence

**Symptom:** strategy was intended to run on a 1h cadence but the AI Signal Log shows
`CYCLE INTERVAL: 4h`, and the visible trigger (dashboard "Today 18:02") does not fall on a
1h boundary.

**Evidence — live config:**

```
SELECT strategy_id, interval_no_position, interval_position_open, interval_at_risk,
       at_risk_threshold_pct, candle_close_buffer_seconds, use_geometry,
       use_technical, template_id, dry_run
FROM ai_strategy_config WHERE strategy_id = 'hype-breakout-da2e';

 strategy_id        | interval_no_position | interval_position_open | interval_at_risk | at_risk_threshold_pct | candle_close_buffer_seconds | use_geometry | use_technical | template_id      | dry_run
 hype-breakout-da2e | 4h                    | 15m                    | 5m                |                   1.50 |                          150 | t            | t             | geometric_range  | f
```

`interval_no_position` is **`4h`**, not `1h`.

**Evidence — position state** (determines which interval column applies):

```
SELECT status, side, opened_at, closed_at, entry_price, pnl_unrealized
FROM strategy_positions WHERE strategy_id = 'hype-breakout-da2e' ORDER BY opened_at DESC LIMIT 3;

(0 rows)
```

The strategy has never held a position. The scheduler is therefore always using
`interval_no_position`, confirming `4h` (not `interval_position_open`/`interval_at_risk`) is
the value that actually governs its cadence.

**Evidence — reconciling the "18:02" wake with the 4h boundary.** `ai_signal_log`, most
recent rows:

```
SELECT triggered_at, trigger_reason, cycle_interval, proposed_action, gate_passed, data_sources_used
FROM ai_signal_log WHERE strategy_id = 'hype-breakout-da2e' ORDER BY triggered_at DESC LIMIT 8;

 triggered_at                   | trigger_reason | cycle_interval | proposed_action | gate_passed | data_sources_used
 2026-07-04 16:02:30.476606+00  | scheduled      | 4h             | hold            | f           | {technical,fear_greed,funding_rate,open_interest,news}
 2026-07-04 13:35:13.126905+00  | config_reload  | 4h             | hold            | f           | {technical,fear_greed,funding_rate,open_interest,news}
 2026-07-04 12:02:30.417997+00  | scheduled      | 4h             | hold            | f           | {technical,fear_greed,funding_rate,open_interest,news}
 2026-07-04 08:02:30.202171+00  | scheduled      | 4h             | hold            | f           | {technical,fear_greed,funding_rate,open_interest,news}
 2026-07-04 04:02:30.818571+00  | scheduled      | 4h             |                 | f           | {technical,fear_greed,funding_rate,open_interest,news}
 2026-07-04 00:02:30.472287+00  | scheduled      | 4h             |                 | f           | {technical,fear_greed,funding_rate,open_interest,news}
```

Full row for the exact log entry the screenshot shows (dashboard "Today 18:02"):

```
SELECT triggered_at, trigger_reason, cycle_interval, prompt_template, data_sources_used,
       context_tokens, proposed_action, confidence, gate_passed, gate_rejection_reason,
       dry_run, llm_provider, llm_model
FROM ai_signal_log WHERE strategy_id='hype-breakout-da2e'
  AND triggered_at = '2026-07-04 16:02:30.476606+00';

 triggered_at: 2026-07-04 16:02:30.476606+00
 trigger_reason: scheduled          cycle_interval: 4h        prompt_template: geometric_range
 data_sources_used: {technical,fear_greed,funding_rate,open_interest,news}
 context_tokens: 2004               proposed_action: hold     confidence: 0.700
 gate_passed: f                     gate_rejection_reason: hold_or_adjust
 dry_run: f                         llm_provider: google      llm_model: gemini-2.5-flash
```

This is an exact match to the screenshot (`2004` tokens, `geometric_range`, `gemini-2.5-flash`,
`hold_or_adjust` ≈ "hold or adjust", `dry_run=f` → "no") — the only discrepancy is the clock
display: **16:02:30 UTC = 18:02 local (UTC+2)**. So "Today 18:02" *is* the row above, and it
**is** exactly on the 4h grid (00/04/08/12/16/20 UTC + 150s buffer from
`candle_close_buffer_seconds`), reproduced precisely by `seconds_until_aligned_wake`
(`ai-signal-generator/app/scheduling.py`): `16:00:00 UTC + 150s = 16:02:30 UTC`. **There is no
discrepancy to explain** — the scheduler is behaving exactly as `scheduling.py` and
`scheduler.py` specify for a `4h` interval. The bug is entirely that the persisted interval
is `4h`, not that the scheduler mis-scheduled a `1h` value.

The spacing between all recent `scheduled` rows above is a clean 4h — confirms steady-state
operation on `4h`, not `1h` or irregular.

**Evidence — when the value actually became `4h`.** Broadening the query to the full window
the `ai-signal-generator` container has been running (45h uptime) shows the strategy was
disabled and re-enabled in between:

```
docker compose logs ai-signal-generator --since 2026-07-02T00:00:00 | grep hype-breakout-da2e | grep -iE "reconcile|config reload|started|stopped"

2026-07-02 20:47:30 [INFO] app.main: reconcile: stopped scheduler+watcher strategy=hype-breakout-da2e
2026-07-03 19:31:07 [INFO] app.main: reconcile: noop strategy=hype-breakout-da2e
2026-07-03 19:31:11 [INFO] app.main: reconcile: started scheduler+watcher strategy=hype-breakout-da2e
2026-07-04 13:35:13 [INFO] app.main: reconcile: reloaded (interrupted) strategy=hype-breakout-da2e
```

and the corresponding `ai_signal_log` rows around that window:

```
2026-07-02 20:02:30 | scheduled | 2h   <- interval was 2h before the strategy was disabled
2026-07-02 20:47:30            (disabled here — no config change, see below)
2026-07-03 19:31:11 | startup  | 4h   <- already 4h at the moment the scheduler restarted
```

So `interval_no_position` changed from `2h` to `4h` **before 2026-07-03 19:31 UTC**, i.e. it
predates today's (2026-07-04 13:35:13) edit that turned on `use_geometry` and switched the
template to `geometric_range` — that later edit left `interval_no_position` untouched at `4h`.
Confirmed the disable/re-enable itself cannot be the cause: `dashboard-api/src/routes/strategies.ts`
`/:id/start` only does `UPDATE strategies SET enabled = true, stop_reason = NULL,
allocation_peak = ...` — it never touches `ai_strategy_config`, so re-enabling could not by
itself have changed the interval. The `4h` value must have been written by an actual
`PUT /api/ai/strategies/:id/config` call sometime between 2026-07-02 20:47:30 and
2026-07-03 19:31:07.

**Evidence — write path is not at fault.** `dashboard-api/src/routes/ai.ts`:
- `interval_no_position` **is** in `ALLOWED_CONFIG_FIELDS` (line 13).
- The validation loop (lines 321–325) checks it against `INTERVAL_PATTERN =
  /^[0-9]+(m|h|d)$/`, which `'1h'` satisfies.
- The `UPDATE`/`INSERT` path (lines 348–375) writes whatever was accepted straight to the
  column, with no coercion or default-substitution logic anywhere in the handler.

Tracing the code gives no mechanism by which submitting `'1h'` would be rejected, silently
dropped, or rewritten to `'4h'`. If `'1h'` had actually reached this endpoint, it would have
persisted as `'1h'`.

**Limitation (stated per verification discipline, not inferred):** the exact client request
that set `interval_no_position` to `4h` between 2026-07-02 20:47 and 2026-07-03 19:31 cannot
be recovered. `dashboard-api`'s container shows `Up 7 minutes` at investigation time (it was
redeployed for unrelated work after this window per `docker compose ps`), so its logs do not
extend back to the relevant time and cannot confirm the literal request body. `ai_signal_log`
does not store config-change history or request payloads (only `cycle_interval` at trigger
time). No other audit trail exists for `ai_strategy_config` writes. We can prove *what value
is persisted* and *that the write path does not corrupt a correct value*, but not *why* the
value that reached the server wasn't `1h` in the first place (client-side form state at the
time of that edit vs. genuine user action — either is possible, neither is provable now).

**Phase 1 conclusion:** The scheduler, `scheduling.py`, and the `ai.ts` write path all behave
correctly for whatever value is in `ai_strategy_config.interval_no_position`. The strategy is
not running hourly strictly because that column currently holds `'4h'`, not `'1h'` — a change
that happened before 2026-07-03 19:31 UTC and was not touched by today's
template/geometry edit. **Proposed fix (description only):** re-set
`interval_no_position = '1h'` for this strategy via the existing "Edit Strategy" UI (which,
per the code trace above, will persist correctly and trigger an immediate `config_reload`
cycle via `notifyConfigReload`/`interrupt()`); no code change is required for the cadence
symptom itself. Separately worth doing (not required to fix this instance): add basic
create/update audit logging on `ai_strategy_config` so future "I set X but Y persisted"
reports are diagnosable without relying on scheduler-cycle logs as a proxy.

---

## Phase 2 — Geometry (GEOMETRIC PATTERN missing)

**Symptom:** the AI Signal Log card's LLM reasoning has no `GEOMETRIC PATTERN` section, and
the data-source chip row shows no geometry chip, even though the strategy's `TEMPLATE` is
`geometric_range`.

**Leading suspect ruled out:** `use_geometry` for this strategy is **`true`** (see Phase 1
config dump), so the omission is *not* the `not sc.get('use_geometry')` branch of
`_render_geometry` (`ai-signal-generator/app/prompt/builder.py:246`).

**Evidence — no fetch/detection errors were logged for the relevant cycles.** Full,
unfiltered log output for both the `config_reload` cycle (13:35:13) and the `scheduled`
cycle matching the screenshot (16:02:30):

```
docker compose logs ai-signal-generator --since 2026-07-04T13:35:13 --until 2026-07-04T13:35:26
  ... fetch_open_interest error [blofin HYPE/USDT:USDT]: blofin fetchOpenInterest() is not supported yet
  ... GET https://api.coingecko.com/api/v3/news "HTTP/1.1 401 Unauthorized"
  ... (no "ohlcv:" or "geometry:" warning anywhere in the cycle)

docker compose logs ai-signal-generator --since 2026-07-04T16:02:30 --until 2026-07-04T16:02:46
  ... fetch_open_interest error [blofin HYPE/USDT:USDT]: blofin fetchOpenInterest() is not supported yet
  ... GET https://api.coingecko.com/api/v3/news "HTTP/1.1 401 Unauthorized"
  ... (no "ohlcv:" or "geometry:" warning anywhere in the cycle)
```

`node_ingest.py` wraps both the OHLCV fetch and `detect_geometry(...)` in `try/except` blocks
that log `"ohlcv:{exc}"` / `"geometry:{exc}"` on failure (lines 52–55, 71–75). Neither fired,
so OHLCV succeeded and `detect_geometry` returned normally (no exception) — the branch that
fired in `_render_geometry` must be `gd.get('shape') == 'no_pattern'` (or `not gd` for an
empty result), not a data-fetch failure.

**Evidence — live reproduction of `detect_geometry` on the same code path, same exchange
adapter, same symbol, same interval used by that cycle (`4h`, from `cycle_interval`):**

```
docker compose exec ai-signal-generator python -c "
import asyncio
from app.data.ohlcv import fetch_ohlcv
from app.data.geometry import detect_geometry
async def main():
    data = await fetch_ohlcv('blofin', 'HYPE/USDT', '4h', 90)
    closed = data.get('closed_candles')
    print(len(closed), detect_geometry(closed))
asyncio.run(main())
"

589 {'shape': 'no_pattern', 'upper_boundary': 67.688999, 'lower_boundary': 61.822267,
     'upper_touches': 1, 'lower_touches': 3, 'convergence_pct_per_bar': -0.0588,
     'pattern_age_bars': 49, 'position_in_range_pct': 100.0, 'fit_quality': 'weak'}
```

`shape` is `'no_pattern'` — confirms the omission branch is
`gd.get('shape') == 'no_pattern'` in `_render_geometry`, not `not use_geometry` and not a
data-fetch error.

**Evidence — why `detect_geometry` classified it `no_pattern`.** Reproducing the internal
trendline fit (`app.data.geometry._find_swings` / `_polyfit_r2`) on the same 120-candle
window:

```
swing_highs count: 10   swing_lows count: 9   (well above MIN_SWINGS=2 — not an
                                                insufficient-swings early-return)
upper_r2 0.178   lower_r2 0.275   MIN_R2_PATTERN (threshold) = 0.30
```

Both trendline fits are below `MIN_R2_PATTERN = 0.30`
(`ai-signal-generator/app/data/geometry.py:169`: `if min(upper_r2, lower_r2) <
MIN_R2_PATTERN: shape = 'no_pattern'`). The detector is explicitly designed to refuse to
classify a shape when the linear fit through the recent swing highs/lows is this noisy — this
fired as designed, not as a bug in the gating logic itself.

**Data-source chip — a separate, always-present bug, not diagnostic of this branch.**
`_data_sources_used()` (`ai-signal-generator/app/graph/nodes/node_dispatch.py:12-21`) builds
the chip array checked into `data_sources_used`:

```python
def _data_sources_used(sc: dict) -> list[str]:
    sources = []
    if sc.get('use_technical'):    sources.append('technical')
    if sc.get('use_fear_greed'):   sources.append('fear_greed')
    if sc.get('use_funding_rate'): sources.append('funding_rate')
    if sc.get('use_open_interest'): sources.append('open_interest')
    if sc.get('use_news'):         sources.append('news')
    if sc.get('use_btc_dominance'): sources.append('btc_dominance')
    if sc.get('use_macro'):        sources.append('macro')
    return sources
```

There is **no `'geometry'` case at all**, regardless of `use_geometry`. The dashboard's chip
row (`dashboard-ui/src/pages/AiSignalLog.tsx`) renders directly from this DB column, so a
geometry chip can **never** appear for any strategy, any time, independent of whether
`use_geometry` is on or whether a pattern was found. The task brief's assumption ("the
absence of a geometry chip should corroborate whichever branch you identified") does not
hold — the chip's absence is uninformative here; it would be absent even on a cycle that
*did* render a `GEOMETRIC PATTERN` section. This is a distinct, minor omission worth fixing
independently of the two reported symptoms.

**Phase 2 conclusion:** `_render_geometry` omits the section because `detect_geometry`
returned `shape: 'no_pattern'` on the `4h` HYPE/USDT candles — the swing-trendline fit was
too weak (R² 0.18 / 0.28, both under the 0.30 threshold) to classify any channel/wedge/
triangle shape. `use_geometry` is on, OHLCV succeeded, geometry ran without error — this is
the detector correctly declining to force-fit noise, not a defect in the `use_geometry` /
`geometry_data` / `no_pattern` gate in `_render_geometry`. **Proposed fix (description
only):** none needed in the gating logic itself; see Phase 3 for how the interval fix
changes this outcome. Separately, add a `'geometry'` entry to `_data_sources_used()` gated on
`sc.get('use_geometry')` so the chip row can ever reflect geometry's on/off state.

---

## Phase 3 — Cross-link

`node_ingest.py:42` — `interval = state.get('cycle_interval', '4h')` — feeds directly into
both the OHLCV fetch (line 52) and, when `use_geometry` is on, `detect_geometry` (lines
70–72). `cycle_interval` is set once per cycle in `scheduler.py`'s `_get_interval_label`
(line 227-230) from `interval_no_position` whenever the strategy is flat (true for this
strategy — confirmed 0 rows in `strategy_positions`). So yes: the same wrong `4h` value from
Phase 1 is exactly what geometry analyzed in Phase 2 — the hypothesis that the two symptoms
share cause **is confirmed as a data dependency, but they are not literally the same root
cause**: Phase 1's defect is "the persisted interval is wrong"; Phase 2's proximate cause is
"the fit on that (wrong) timeframe was too weak to classify," which is a legitimate
detector outcome, not itself a bug.

**Does fixing the interval alone resolve Phase 2, right now?** Reproduced `detect_geometry`
on `1h` candles (the user's originally intended interval) for the same symbol, same moment:

```
docker compose exec ai-signal-generator python -c "
import asyncio
from app.data.ohlcv import fetch_ohlcv
from app.data.geometry import detect_geometry
async def main():
    for tf in ['1h','4h']:
        data = await fetch_ohlcv('blofin', 'HYPE/USDT', tf, 90)
        closed = data.get('closed_candles') if data else None
        gd = detect_geometry(closed) if closed else {}
        print(tf, '-> shape:', gd.get('shape'), 'fit_quality:', gd.get('fit_quality'), 'n_closed:', len(closed or []))
asyncio.run(main())
"

1h -> shape: ascending_channel  fit_quality: strong  n_closed: 1439
4h -> shape: no_pattern         fit_quality: weak    n_closed: 589
```

On the current market data, `1h` candles for HYPE/USDT produce a **strong-fit
`ascending_channel`** — i.e. correcting `interval_no_position` back to `1h` would, right now,
also make the `GEOMETRIC PATTERN` section appear on the next cycle, with no code change.
This is a snapshot of current data, not a guarantee for all future cycles (a strong 1h
channel today doesn't mean every future 1h cycle will find a pattern) — but it directly
answers the brief's question.

**Phase 3 conclusion:** fixing the interval alone is sufficient to resolve *this instance* of
the missing geometry section, because `use_geometry` was already correctly on and
`detect_geometry` is already wired to run on whatever `cycle_interval` resolves to. No
`use_geometry`-gate code change and no interval-vs-geometry-timeframe decoupling is required
to fix what's currently visible. (Whether the platform *should* let geometry and cadence use
independently configurable timeframes — e.g. run signals hourly but detect geometry patterns
on 4h structure — is a separate design question, not a bug; flagging it only as a possible
future entry in `docs/ROADMAP.md`'s Open Design Questions, not acting on it.)

---

## Root cause summary

- **Cadence:** `ai_strategy_config.interval_no_position` for `hype-breakout-da2e` is
  persisted as `'4h'`, not the intended `'1h'` — a value change that occurred before
  2026-07-03 19:31 UTC (not part of today's template/geometry edit) and is not reproducible
  from any surviving log; the scheduler and dashboard-api write path both behave correctly
  for whatever value is stored, so the fix is a data correction (re-save the strategy's
  interval to `1h`), not a code fix.
- **Geometry:** `GEOMETRIC PATTERN` is omitted because `detect_geometry` classified the `4h`
  HYPE/USDT candles as `'no_pattern'` (swing-trendline R² 0.18/0.28, below the 0.30
  no-pattern threshold) — `use_geometry` is correctly on and the pipeline ran without error;
  this is the wrong-interval symptom cascading into geometry's input data, and on current
  market data, fixing the interval to `1h` alone would surface a real (`ascending_channel`,
  strong fit) pattern on the next cycle.
