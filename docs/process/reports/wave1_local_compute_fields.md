# Wave 1 Report — Migration 045 + Three Local-Compute AI Data Fields

**Date:** 2026-07-06
**Scope:** `ai-signal-generator` + `db/migrations/` only, per the wave scope guard.
**Design authority:** `docs/design/ai_prompts/20_plumbing_specs.md` §3 (volume_profile_hvn_lvn),
§5 (momentum_divergence), §6 (volatility_regime), §10 (migration 045), §11 (cross-cutting rules);
`docs/design/ai_prompts/00_audit.md` §2 (delivered-field pattern).

Every claim below is backed by pasted command output from the live stack.

---

## 1. Migration applied: **045**

`ls db/migrations` confirmed `044_ai_signal_log_geometry_data.sql` as the highest existing
file, so the next free number was 045 as the spec expected. Written from spec §10 verbatim
to `db/migrations/045_ai_data_source_toggles.sql` — all 8 toggles, `DEFAULT false NOT NULL`,
`ADD COLUMN IF NOT EXISTS`, self-verifying `DO $$` block. (`use_economic_calendar`
intentionally excluded — the column already exists.)

Applied against the running DB (`docker compose exec -T postgres psql -U matp -d matp -v ON_ERROR_STOP=1 < db/migrations/045_ai_data_source_toggles.sql`):

```
BEGIN
ALTER TABLE
COMMIT
DO
NOTICE:  Migration 045 verified OK: 8 data-source toggle columns present, default=false
```

Column check (`docker compose exec postgres psql -U matp -d matp -c "\d ai_strategy_config" | grep -E "use_(mtf_structure|orderbook|volume_profile|cvd|momentum_divergence|volatility_regime|funding_history|liquidations)"`):

```
 use_mtf_structure           | boolean                  |           | not null | false
 use_orderbook               | boolean                  |           | not null | false
 use_volume_profile          | boolean                  |           | not null | false
 use_cvd                     | boolean                  |           | not null | false
 use_momentum_divergence     | boolean                  |           | not null | false
 use_volatility_regime       | boolean                  |           | not null | false
 use_funding_history         | boolean                  |           | not null | false
 use_liquidations            | boolean                  |           | not null | false
```

No redeploy was needed for the schema-only change (Phase 1); the service was redeployed in
Phases 2–3 for the code changes.

---

## 2. Three fields delivered, with render slots

| Field | Fetcher | State key | Renderer | Slot |
|---|---|---|---|---|
| `volatility_regime` | `app/data/volatility.py::compute_volatility_regime(candles, percentile_window=200)` | `volatility_regime` | `_render_volatility_regime` → `VOLATILITY REGIME:` | **2.2** (right after Technical) |
| `momentum_divergence` | `app/data/divergence.py::detect_momentum_divergence(candles, lookback=60)` (reuses `geometry.py::_find_swings`; 5-bar minimum swing separation against false pairings) | `momentum_divergence` | `_render_momentum_divergence` → `MOMENTUM DIVERGENCE:` | **2.3** |
| `volume_profile_hvn_lvn` | `app/data/volume_profile.py::compute_volume_profile(candles, num_bins=50)` | `volume_profile` | `_render_volume_profile` → `VOLUME PROFILE (lookback window):` | **2.4** (just before Geometry) |

All three are sync pure-local computations on `closed_candles`, wired inside the existing
`if closed_candles:` block in `node_ingest.py`, each gated by its `sc.get('use_*')` toggle,
each in try/except appending `<field>:{exc}` to `data_fetch_errors` — never fatal. The
OHLCV-fetch outer gate was extended to fire on the three new toggles so none is a dead
switch on a strategy without `use_technical`/`use_geometry`.

### 2.1 Code present in the built image (grep inside the running container)

```
$ docker compose exec ai-signal-generator grep -n "_render_volume_profile\|compute_volume_profile" /app/app/prompt/builder.py /app/app/data/volume_profile.py /app/app/graph/nodes/node_ingest.py
/app/app/prompt/builder.py:242:def _render_volume_profile(state: dict) -> str:
/app/app/prompt/builder.py:397:        vp = _render_volume_profile(state)
/app/app/data/volume_profile.py:35:def compute_volume_profile(candles: list[dict], num_bins: int = 50) -> dict | None:
/app/app/data/volume_profile.py:126:        logger.warning("compute_volume_profile error: %s", exc)
/app/app/data/volume_profile.py:151:    print(json.dumps(compute_volume_profile(candles), indent=2))
/app/app/graph/nodes/node_ingest.py:12:from app.data.volume_profile import compute_volume_profile
/app/app/graph/nodes/node_ingest.py:84:                    volume_profile = compute_volume_profile(closed_candles)
```

(after the Phase 3 rebuild, `_render_volume_profile` moved to builder.py:311 as the two new
renderers were added above it)

```
$ docker compose exec ai-signal-generator grep -n "_render_momentum_divergence\|detect_momentum_divergence\|_render_volatility_regime\|compute_volatility_regime" /app/app/prompt/builder.py /app/app/data/divergence.py /app/app/data/volatility.py /app/app/graph/nodes/node_ingest.py
/app/app/prompt/builder.py:242:def _render_volatility_regime(state: dict) -> str:
/app/app/prompt/builder.py:265:def _render_momentum_divergence(state: dict) -> str:
/app/app/prompt/builder.py:441:        vr = _render_volatility_regime(state)
/app/app/prompt/builder.py:447:        md = _render_momentum_divergence(state)
/app/app/data/divergence.py:88:def detect_momentum_divergence(candles: list[dict], lookback: int = 60) -> dict | None:
/app/app/data/divergence.py:148:        logger.warning("detect_momentum_divergence error: %s", exc)
/app/app/data/divergence.py:168:    print(json.dumps(detect_momentum_divergence(candles), indent=2))
/app/app/data/volatility.py:38:def compute_volatility_regime(candles: list[dict], percentile_window: int = 200) -> dict | None:
/app/app/data/volatility.py:83:        logger.warning("compute_volatility_regime error: %s", exc)
/app/app/data/volatility.py:108:    print(json.dumps(compute_volatility_regime(candles), indent=2))
/app/app/graph/nodes/node_ingest.py:6:from app.data.divergence import detect_momentum_divergence
/app/app/graph/nodes/node_ingest.py:13:from app.data.volatility import compute_volatility_regime
/app/app/graph/nodes/node_ingest.py:96:                    momentum_divergence = detect_momentum_divergence(closed_candles)
/app/app/graph/nodes/node_ingest.py:103:                    volatility_regime = compute_volatility_regime(closed_candles)
```

### 2.2 Functional proof on real market data

One-off invocations inside the running container, on real Binance BTC/USDT 4h candles
fetched via the production `fetch_ohlcv` path (589 closed candles).

Volume profile (Phase 2 run, current_price 63861.59):

```
compute_volume_profile -> {'poc_price': 63060.6501, 'value_area_high': 76086.5513, 'value_area_low': 58551.6843, 'hvn_levels': [60555.6691, 66567.6235, 77088.5437], 'lvn_levels': [65565.6311, 70074.5969, 79593.5247]}

VOLUME PROFILE (lookback window):
POC (Point of Control): 63060.6501
Value Area High:        76086.5513
Value Area Low:         58551.6843
HVN Levels:             60555.6691, 66567.6235, 77088.5437
LVN Levels:             65565.6311, 70074.5969, 79593.5247
```

Momentum divergence + volatility regime (Phase 3 run, current_price 63866.0):

```
detect_momentum_divergence -> {'rsi_divergence': 'bearish', 'rsi_divergence_bars_since': 5, 'macd_divergence': 'bearish', 'macd_divergence_bars_since': 5}

MOMENTUM DIVERGENCE:
RSI Divergence:       bearish (swing confirmed 5 bars ago)
MACD Divergence:      bearish (swing confirmed 5 bars ago)

compute_volatility_regime -> {'atr_percentile': 28.0, 'bb_width_percentile': 8.0, 'squeeze_flag': True}

VOLATILITY REGIME:
ATR(14) Percentile:   28.0 (vs trailing window)
BB Width Percentile:  8.0
Squeeze:              YES — Bollinger width compressed (bottom of window), expansion likely
```

### 2.3 Honest-absence proof (renderers return `''` on missing data)

```
honest absence (volume_profile=None): repr=''  empty=True
honest absence (momentum_divergence=None): repr=''  empty=True
honest absence (volatility_regime=None):   repr=''  empty=True
```

And the fetchers degrade to `None` below their candle floors instead of fabricating values:

```
detect_momentum_divergence(10 candles) -> None
compute_volatility_regime(10 candles)  -> None
```

### 2.4 Deploy verification

Rebuilt + force-recreated via `./scripts/redeploy.sh ai-signal-generator` after each phase
(never `docker compose restart`). Post-deploy health from inside the docker network:

```
$ docker compose exec nginx wget -qO- http://ai-signal-generator:8005/health
{"status":"ok","service":"ai-signal-generator"}
```

---

## 3. Untouched surfaces confirmed

Diff-stat of the wave's three commits — only `ai-signal-generator/` and `db/migrations/`
were modified; no file in `order-executor`, `order-listener`, `dashboard-api`,
`dashboard-ui`, or `strategy-tester` appears:

```
$ git diff --stat HEAD~3..HEAD
 ai-signal-generator/app/data/divergence.py         | 168 +++++++++++++++++++++
 ai-signal-generator/app/data/volatility.py         | 108 +++++++++++++
 ai-signal-generator/app/data/volume_profile.py     | 151 ++++++++++++++++++
 ai-signal-generator/app/graph/nodes/node_ingest.py |  40 ++++-
 ai-signal-generator/app/graph/state.py             |   3 +
 ai-signal-generator/app/prompt/builder.py          |  86 +++++++++++
 db/migrations/045_ai_data_source_toggles.sql       |  44 ++++++
 7 files changed, 597 insertions(+), 3 deletions(-)
```

`ai_prompt_templates` (prompt template text) was not edited — no code or SQL above touches
it; template cutover remains Wave 4. And because every new toggle defaults false and no
live strategy has enabled one, **no live strategy's prompt changes**:

```
$ docker compose exec postgres psql -U matp -d matp -c "SELECT count(*) AS strategies_with_new_toggles_on FROM ai_strategy_config WHERE use_volume_profile OR use_momentum_divergence OR use_volatility_regime OR use_mtf_structure OR use_orderbook OR use_cvd OR use_funding_history OR use_liquidations;"
 strategies_with_new_toggles_on
--------------------------------
                              0
(1 row)
```

Dashboard toggle exposure and tester schema parity remain deferred to their existing
ROADMAP items, per the wave scope guard.

---

## 4. Final section ordering in `build_prompt()`

From the deployed image:

```
$ docker compose exec ai-signal-generator grep -n "^    # [0-9]" /app/app/prompt/builder.py
432:    # 1. Header — always included; contains position warning if position_open
435:    # 2. Technical — only if toggled on and OHLCV data is available
439:    # 2.2. Volatility regime — right after Technical
445:    # 2.3. Momentum divergence
451:    # 2.4. Volume profile — just before Geometry, so boundary/HVN confluence reads adjacently
457:    # 2.5. Geometry — only if toggled on and geometry data is available
463:    # 2.6. Open orders — only if toggled on (geometry gates the range-working actions)
469:    # 3. Sentiment — only if at least one sentiment source is toggled on
475:    # 4. News — only if toggled on and digest is available
479:    # 5. Macro — only if at least one macro source is toggled on
485:    # 6. Portfolio — always included
488:    # 7. Data warnings — inserted between portfolio and instructions if errors occurred
494:    # 8. Strategy instructions — from DB template, always included
502:    # 9. Task section — always included
```

Slots 2.1 (mtf_structure), 2.7 (orderbook), 2.8 (cvd), 2.9 (liquidations) and 4.5
(economic calendar) remain open for later waves, per the spec's ordering table.

---

## 5. Standing constraints — how they were met

- **Non-fatal fetchers:** all three wrapped in try/except appending to `data_fetch_errors`;
  a failure surfaces in the DATA WARNINGS section, never aborts the cycle.
- **Honest absence:** all three renderers return `''` on missing/None data (§2.3 above);
  no neutral values fabricated.
- **No confidence scale / task text restated:** the new renderers emit data lines only;
  `_render_task` remains the sole source of the scale and task instruction.
- **Image verification:** every deploy verified by grep inside the running container after
  `./scripts/redeploy.sh` (rebuild + `up -d --force-recreate`), never `docker compose restart`.
