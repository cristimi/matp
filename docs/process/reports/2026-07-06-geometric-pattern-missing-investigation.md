# Investigation: Missing GEOMETRIC PATTERN section in AI signal prompts

Read-only investigation. No code changes in this pass. Scope: `ai-signal-generator` only.

## What was checked

1. Resolved `ai-btc-6f8c` and `hype-breakout-da2e` in the DB (`strategies` + `ai_strategy_config`),
   confirming both are `geometric_range` / `use_geometry=true` strategies.
2. Pulled the last 20 `ai_signal_log` rows for each strategy and confirmed the schema has no
   column for the assembled prompt, `geometry_data`, or per-source fetch errors.
3. Read current source: `app/data/geometry.py` (`detect_geometry`), `app/prompt/builder.py`
   (`_render_geometry`), `app/graph/nodes/node_ingest.py`, `app/data/ohlcv.py`.
4. Noted that commits `a0c042b` (classify broadening geometry) and `32e2086` (surface
   strong-fit unclassified geometry as "Unclassified Structure") landed on `main` on
   2026-07-05 at 15:19 and 16:02 UTC respectively — **before** the symptom rows examined
   below, so those fixes were already live and did not resolve the reported symptom.
5. Ran an ephemeral probe inside the running `ai-signal-generator` container
   (`/tmp/probe_geo.py`, deleted after use, never committed) against live OHLCV data for
   both strategies' actual `exchange_id` / symbol / `cycle_interval` / `lookback_days`,
   reproducing `fetch_ohlcv` → `_find_swings` → `_polyfit_r2` → `detect_geometry` →
   `_render_geometry` step by step.

## Evidence

### 1. Strategy config

```
    strategy_id     | interval_no_position | interval_position_open | interval_at_risk | ... | template_id      | ... | use_geometry
--------------------+-----------------------+-------------------------+-------------------+-----+------------------+-----+--------------
 hype-breakout-da2e | 1h                    | 15m                     | 5m                | ... | geometric_range  | ... | t
 ai-btc-6f8c        | 1h                    | 15m                     | 5m                | ... | geometric_range  | ... | t
```

(full row output, key columns extracted for readability — both `lookback_days=90`,
`indicators={RSI,MACD,EMA50,EMA200,BB,VWAP}`, `use_technical=t`)

```
        id          |     name      |  symbol   | platform | interval |          account_id          | allow_quote_variants | allow_cross_charting
--------------------+---------------+-----------+----------+----------+-------------------------------+-----------------------+-----------------------
 hype-breakout-da2e | HYPE Breakout | HYPE-USDT | auto     | 1h       | blofin-blofin-demo-v5vr       | f                     | f
 ai-btc-6f8c        | AI BTC        | BTC-USDT  | auto     | 1h       | hyperliquid-hyperliquid-hqdy  | t                     | f
```

```
              id               |  exchange   | mode
-------------------------------+-------------+------
 blofin-blofin-demo-v5vr       | blofin      | demo
 hyperliquid-hyperliquid-hqdy  | hyperliquid | demo
```

So: `ai-btc-6f8c` → ccxt exchange `hyperliquid`, symbol `BTC/USDT`; `hype-breakout-da2e` →
ccxt exchange `blofin`, symbol `HYPE/USDT`. Both `lookback_days=90`.

### 2. Recent signal history (last 20 rows each)

`ai-btc-6f8c`:

```
         triggered_at          | cycle_interval | proposed_action | confidence | gate_passed | gate_rejection_reason |                data_sources_used
-------------------------------+----------------+------------------+------------+-------------+-----------------------+-------------------------------------------------
 2026-07-06 07:02:30.81073+00  | 1h             |                  |            | f           | llm_failed            | {technical,geometry,funding_rate,open_interest}
 2026-07-06 06:02:30.257237+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-06 05:02:30.388112+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-06 04:02:30.289215+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-06 03:02:30.185004+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-06 02:32:30.013909+00 | 1h             | hold             |      0.700 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-06 02:17:30.37201+00  | 15m            | close_long       |      0.800 | t           |                       | {technical,geometry,funding_rate,open_interest}
 2026-07-06 02:02:30.670304+00 | 15m            | partial_close    |      0.680 | t           |                       | {technical,geometry,funding_rate,open_interest}
 2026-07-06 01:02:30.201284+00 | 1h             | open_long        |      0.700 | t           |                       | {technical,geometry,funding_rate,open_interest}
 2026-07-06 00:02:30.247236+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-05 23:02:30.019391+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-05 22:54:29.843899+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-05 22:02:30.370355+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-05 21:02:30.283227+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-05 20:02:30.71382+00  | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-05 19:02:30.030433+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-05 18:02:30.503662+00 | 1h             | hold             |      0.900 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-05 17:02:30.038867+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-05 16:32:34.016799+00 | 1h             | hold             |      0.900 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
 2026-07-05 16:02:31.291811+00 | 1h             | hold             |      0.500 | f           | hold_or_adjust        | {technical,geometry,funding_rate,open_interest}
```

`hype-breakout-da2e`:

```
         triggered_at          | cycle_interval | proposed_action  | confidence | gate_passed | gate_rejection_reason |                        data_sources_used
-------------------------------+----------------+-------------------+------------+-------------+-----------------------+-----------------------------------------------------------------
 2026-07-06 07:02:30.300293+00 | 1h             | hold              |      0.500 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-06 06:02:30.181509+00 | 1h             |                   |            | f           | llm_failed            | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-06 05:02:30.25248+00  | 1h             | hold              |      0.500 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-06 04:02:30.37913+00  | 1h             | hold              |      0.500 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-06 03:02:30.016577+00 | 1h             |                   |            | f           | llm_failed            | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-06 02:02:30.370621+00 | 1h             |                   |            | f           | llm_failed            | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-06 01:02:30.013259+00 | 1h             | hold              |      0.500 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-06 00:02:30.158771+00 | 1h             | hold              |      0.500 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 23:02:30.20357+00  | 1h             | hold              |      0.650 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 22:02:30.184361+00 | 1h             | hold              |      0.500 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 21:02:30.37542+00  | 1h             | amend_order       |      0.650 | t           |                       | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 20:02:30.409844+00 | 1h             | amend_order       |      0.650 | t           |                       | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 19:02:30.355365+00 | 1h             | hold              |      0.650 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 18:02:30.264358+00 | 1h             | amend_order       |      0.750 | t           |                       | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 17:02:30.268426+00 | 1h             | amend_order       |      0.700 | t           |                       | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 16:32:33.736362+00 | 1h             | place_limit_long  |      0.750 | t           |                       | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 16:02:30.89404+00  | 1h             | hold              |      0.500 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 15:02:30.502247+00 | 1h             | hold              |      0.650 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 14:02:30.205605+00 | 1h             | hold              |      0.500 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
 2026-07-05 13:02:32.329105+00 | 1h             | hold              |      0.500 | f           | hold_or_adjust        | {technical,geometry,fear_greed,funding_rate,open_interest,news}
```

`data_sources_used` includes `geometry` in every row for both strategies, including the
ones whose `reasoning` explicitly says the GEOMETRIC PATTERN section was missing (below).
This confirms the note in the task brief: **`data_sources_used` is derived from config
toggles, not from whether the section actually made it into the assembled prompt** — it is
not usable as evidence either way.

### `ai_signal_log` schema (the diagnostic gap)

```
                                            Table "public.ai_signal_log"
        Column         |           Type           | Collation | Nullable |                  Default
-----------------------+--------------------------+-----------+----------+--------------------------------------------
 id                    | bigint                   |           | not null | nextval('ai_signal_log_id_seq'::regclass)
 strategy_id           | character varying(100)   |           | not null |
 triggered_at          | timestamp with time zone |           | not null | now()
 trigger_reason        | character varying(50)    |           | not null |
 cycle_interval        | character varying(10)    |           |          |
 prompt_template       | character varying(50)    |           |          |
 data_sources_used     | text[]                   |           |          |
 context_tokens        | integer                  |           |          |
 proposed_action       | character varying(20)    |           |          |
 confidence            | numeric(4,3)             |           |          |
 reasoning             | text                     |           |          |
 gate_passed           | boolean                  |           | not null | false
 gate_rejection_reason | text                     |           |          |
 webhook_fired         | boolean                  |           | not null | false
 webhook_status        | integer                  |           |          |
 order_id              | uuid                     |           |          |
 dry_run               | boolean                  |           | not null | true
 outcome_pnl           | numeric                  |           |          |
 outcome_pct           | numeric                  |           |          |
 outcome_filled_at     | timestamp with time zone |           |          |
 llm_provider          | character varying(20)    |           |          |
 llm_model             | character varying(50)    |           |          |
```

There is no column for the assembled prompt, for `geometry_data`, or for per-source fetch
errors (`data_fetch_errors` from `node_ingest` is not persisted anywhere). The only signal
of what the LLM actually saw is the free-text `reasoning` column, which is why this
investigation had to reproduce detection live rather than read it back from history.

Sample `reasoning` text confirming the symptom is real and recent (both strategies, spanning
2026-07-05 13:02 through 2026-07-06 07:02, i.e. **after** commits `a0c042b`/`32e2086` had
already landed at 15:19/16:02 the previous day):

- `ai-btc-6f8c` 2026-07-06 05:02: *"No geometric pattern data was provided in the input,
  which is essential for identifying trade setups..."*
- `ai-btc-6f8c` 2026-07-06 03:02: *"The 'GEOMETRIC PATTERN' section, which is crucial for
  determining pattern validity, boundaries, and touch counts for placing or managing
  orders, is missing from the input."*
- `ai-btc-6f8c` 2026-07-05 21:02: *"The 'GEOMETRIC PATTERN' section... is entirely missing
  from the provided input."*
- `hype-breakout-da2e` 2026-07-06 05:02: *"The 'GEOMETRIC PATTERN' section is entirely
  missing from the analysis..."*
- `hype-breakout-da2e` 2026-07-06 00:02: *"The GEOMETRIC PATTERN section... is entirely
  missing from the input."*
- `hype-breakout-da2e` 2026-07-05 19:02: *"The GEOMETRIC PATTERN has a 'weak' fit_quality
  and 0 upper_touches, indicating an unreliable structure."* — one of the rare rows where
  the section **was** rendered (a classified shape with `fit_quality: weak`, which is not
  dropped — only `no_pattern` + `weak` is dropped).

### 3. Live reproduction

Probe run inside the container against live OHLCV for each strategy's actual
`exchange_id` / symbol / `lookback_days`, cycle_interval = `1h` (the interval both
strategies are currently cycling on, no open position):

```
================================================================================
CASE: ai-btc-6f8c  exchange=hyperliquid symbol=BTC/USDT interval=1h lookback_days=90
================================================================================
len(candles)        = 2210
len(closed_candles) = 2209
current_price       = 62926.0
first closed candle ts = 1775368800000
last  closed candle ts = 1783317600000
sample closed candle   = {'timestamp': 1783317600000, 'open': 63107.0, 'high': 63140.0, 'low': 62962.0, 'close': 62991.0, 'volume': 763.19963}
swing_highs count = 225  (MIN_SWINGS=2)
swing_lows  count = 229  (MIN_SWINGS=2)
upper_r2 = 0.1240  lower_r2 = 0.7761  (MIN_R2_PATTERN=0.3, STRONG_R2=0.7)
min(upper_r2, lower_r2) < MIN_R2_PATTERN ? True
detect_geometry() full return dict:
{'shape': 'no_pattern', 'upper_boundary': 63663.202193, 'lower_boundary': 62762.100334, 'upper_touches': 6, 'lower_touches': 10, 'convergence_pct_per_bar': -0.0007, 'pattern_age_bars': 33, 'position_in_range_pct': 25.4, 'fit_quality': 'weak'}
_render_geometry() output (empty string means section DROPPED):
''
================================================================================
CASE: hype-breakout-da2e  exchange=blofin symbol=HYPE/USDT interval=1h lookback_days=90
================================================================================
len(candles)        = 1440
len(closed_candles) = 1439
current_price       = 71.097
first closed candle ts = 1778140800000
last  closed candle ts = 1783317600000
sample closed candle   = {'timestamp': 1783317600000, 'open': 70.774, 'high': 71.25, 'low': 70.676, 'close': 71.206, 'volume': 4254.4}
swing_highs count = 124  (MIN_SWINGS=2)
swing_lows  count = 134  (MIN_SWINGS=2)
upper_r2 = 0.0015  lower_r2 = 0.0467  (MIN_R2_PATTERN=0.3, STRONG_R2=0.7)
min(upper_r2, lower_r2) < MIN_R2_PATTERN ? True
detect_geometry() full return dict:
{'shape': 'no_pattern', 'upper_boundary': 71.40829, 'lower_boundary': 68.476459, 'upper_touches': 1, 'lower_touches': 1, 'convergence_pct_per_bar': -0.0155, 'pattern_age_bars': 50, 'position_in_range_pct': 93.1, 'fit_quality': 'weak'}
_render_geometry() output (empty string means section DROPPED):
''
```

Both cases: no data/fetch failure (`fetch_ohlcv` returned full candle sets, thousands of
closed candles, no exception), no swing shortage (hundreds of swing highs/lows found — far
above `MIN_SWINGS=2`). Detection ran to completion, reached the R² gate
(`min(upper_r2, lower_r2) < MIN_R2_PATTERN`), and failed it — landing on `no_pattern` +
`fit_quality: weak`, which `_render_geometry` correctly drops per the current (intentional)
"genuinely noisy fit stays omitted" rule.

Supplementary check across the other two cycle intervals each strategy actually runs on
(`interval_position_open=15m`, `interval_at_risk=5m` in `ai_strategy_config`), same
exchange/symbol/lookback, to see whether the 1h result was a one-off:

```
ai-btc-6f8c interval=1h  len(closed_candles)=2209 detect_geometry={'shape': 'no_pattern', ... 'fit_quality': 'weak'}
ai-btc-6f8c interval=15m len(closed_candles)=5002 detect_geometry={'shape': 'no_pattern', ... 'fit_quality': 'weak'}
ai-btc-6f8c interval=5m  len(closed_candles)=5009 detect_geometry={'shape': 'horizontal_channel', ... 'fit_quality': 'weak'}
hype-breakout-da2e interval=1h  len(closed_candles)=1439 detect_geometry={'shape': 'no_pattern', ... 'fit_quality': 'weak'}
hype-breakout-da2e interval=15m len(closed_candles)=1439 detect_geometry={'shape': 'no_pattern', ... 'fit_quality': 'weak'}
hype-breakout-da2e interval=5m  len(closed_candles)=1439 detect_geometry={'shape': 'no_pattern', ... 'fit_quality': 'weak'}
```

5 of the 6 (strategy, interval) combinations land on `no_pattern` + `weak` right now; the
one exception (`ai-btc-6f8c` at 5m) classifies as `horizontal_channel` but still with
`fit_quality: weak` — a case that *would* render (only `no_pattern` + `weak` is dropped),
consistent with the one `hype-breakout-da2e` reasoning row above where a weak-but-classified
shape did make it into the prompt.

Container logs during the probe runs (last 10 minutes) showed no `fetch_ohlcv`, `geometry:`,
or exception log lines — only an unrelated Gemini `503 UNAVAILABLE` on the LLM call for the
concurrent scheduled cycle (which is what produced the `llm_failed` rows in the signal log
above) and a `ccxt`-internal `RuntimeWarning: coroutine 'ClientResponse.json' was never
awaited`, neither of which is on the geometry/OHLCV path.

After the probe: `/tmp/probe_geo.py` and `/tmp/probe_geo_intervals.py` were deleted from the
container; `git status` on the host confirms `nothing to commit, working tree clean` — no
probe file was ever added to the repo tree.

## Verdict

**(B) — weak `no_pattern` from the R² gate — dominates for both strategies, and does so
consistently, not as an occasional edge case.**

- `ai-btc-6f8c` (BTC/USDT, hyperliquid): `min(upper_r2, lower_r2) < 0.30` on 1h and 15m
  (upper_r2 as low as 0.124 against a 0.776 lower_r2 — the *upper* trendline fit is the one
  failing the gate). Only at 5m does it clear the gate into a named shape, and even then
  `fit_quality` stays `weak` (below `STRONG_R2=0.70`).
- `hype-breakout-da2e` (HYPE/USDT, blofin): both R² values are far below the 0.30 gate at
  every interval tested (upper_r2 as low as 0.0015, lower_r2 as low as 0.0467) — this
  symbol's recent swing structure produces almost no linear trendline fit at all on either
  boundary.

**(A) is ruled out** for the current cycles: `fetch_ohlcv` returned thousands of candles
with no exceptions in both cases; `_find_swings` found well over `MIN_SWINGS=2` swing points
on both sides (225/229 for BTC, 124/134 for HYPE) — there is no data/compute failure
happening upstream of the R² check.

**(C) is not the current driver**, though it was a real bug fixed the day before
(`a0c042b`/`32e2086`): the strong-fit fallback path in `_render_geometry` is reachable (a
`no_pattern` + `fit_quality: strong` case would render as "Unclassified Structure" per the
current code at `builder.py:251-258`), but neither strategy is currently landing there —
both are hitting `fit_quality: weak` well before the strong/weak boundary matters, because
the R² values themselves are too low to be "strong" under any classification.

## Diagnostic-gap note

`ai_signal_log` has no column for the assembled prompt, `geometry_data`, or
`data_fetch_errors` (schema pasted above). `data_sources_used` is derived purely from
`strategy_config` toggles in `node_dispatch` — it says `geometry` was "used" even on rows
whose `reasoning` explicitly states the GEOMETRIC PATTERN section was missing. There is
currently no way to distinguish (A)/(B)/(C) — or even confirm the symptom exists at all —
from the DB alone; the only reason this investigation could pin down (B) is by reproducing
`detect_geometry()` against live OHLCV out-of-band. Historical cycles' exact geometry inputs
(the candles as they stood at that specific `triggered_at`) are not recoverable after the
fact for the same reason.
