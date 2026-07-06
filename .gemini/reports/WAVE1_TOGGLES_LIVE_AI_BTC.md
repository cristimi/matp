# Wave 1 follow-up — three local-compute toggles enabled live on `ai-btc-6f8c`

**Date:** 2026-07-06
**Change:** DB-only — no code changed. Enabled `use_volume_profile`,
`use_momentum_divergence`, `use_volatility_regime` on the AI BTC strategy and verified the
live prompt through the real pipeline (scheduler-identical state build → `node_ingest` →
`build_prompt` with the DB template), inside the running container. No LLM call was made —
the check stops after prompt assembly, so no signal/order could fire from it. The scheduler
re-reads `ai_strategy_config` fresh each cycle, so the next real cycle picks this up with
no reload needed.

## Toggle update

```
$ docker compose exec postgres psql -U matp -d matp -c "UPDATE ai_strategy_config SET use_volume_profile = true, use_momentum_divergence = true, use_volatility_regime = true WHERE strategy_id = 'ai-btc-6f8c' RETURNING strategy_id, use_volume_profile, use_momentum_divergence, use_volatility_regime;"
 strategy_id | use_volume_profile | use_momentum_divergence | use_volatility_regime
-------------+--------------------+-------------------------+-----------------------
 ai-btc-6f8c | t                  | t                       | t
(1 row)

UPDATE 1
```

`hype-breakout-da2e` untouched — all its new toggles remain false.

## Live prompt check (real ingest, hyperliquid BTC-USDT 1h, run in the container)

```
strategy=ai-btc-6f8c exchange=hyperliquid symbol=BTC-USDT template=geometric_range
toggles: vp=True md=True vr=True
position_open=False

data_fetch_errors: []
volume_profile:      {'poc_price': 63024.51, 'value_area_high': 76040.63, 'value_area_low': 58518.93, 'hvn_levels': [77542.49, 80546.21], 'lvn_levels': [65527.61, 68030.71, 70033.19]}
momentum_divergence: {'rsi_divergence': 'none', 'rsi_divergence_bars_since': None, 'macd_divergence': 'none', 'macd_divergence_bars_since': None}
volatility_regime:   {'atr_percentile': 74.5, 'bb_width_percentile': 90.0, 'squeeze_flag': False}

estimated_tokens: 1998
```

The assembled prompt contains the three new sections in the spec's slot order —
Technical (2) → Volatility Regime (2.2) → Momentum Divergence (2.3) → Volume Profile (2.4)
→ Geometric Pattern (2.5) → Open Orders (2.6):

```
VOLATILITY REGIME:
ATR(14) Percentile:   74.5 (vs trailing window)
BB Width Percentile:  90.0
Squeeze:              no

MOMENTUM DIVERGENCE:
RSI Divergence:       none detected
MACD Divergence:      none detected

VOLUME PROFILE (lookback window):
POC (Point of Control): 63024.51
Value Area High:        76040.63
Value Area Low:         58518.93
HVN Levels:             77542.49, 80546.21
LVN Levels:             65527.61, 68030.71, 70033.19

GEOMETRIC PATTERN:
Detected Shape:       No Reliable Pattern (weak trendline fit)
...
```

`data_fetch_errors: []` — no fetch failures; the sections rendered from real hyperliquid
candles via the production `fetch_ohlcv` path. Momentum divergence honestly reports
"none detected" (data present, no divergence) rather than being silently absent. The
`geometric_range` template text is unchanged (cutover is Wave 4) — the new sections are
pure added context.

Note: `momentum_divergence` and `volatility_regime` are not in `geometric_range`'s
consumption table in the Phase-1 design (volume_profile is); they're enabled here for
live verification. Flip them off if the extra ~10 lines of context aren't wanted
long-term on this strategy.
