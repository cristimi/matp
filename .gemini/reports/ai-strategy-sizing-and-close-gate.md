# AI strategies: risk sizing never applied, exits scratched at noise

**Date:** 2026-07-25
**Trigger:** "check the ai strategies. it seems to me they are loosing/winning too little"
**Services touched:** `ai-signal-generator`, `dashboard-api`, `postgres` (migrations 060, 061)

## Diagnosis

Three compounding causes, in order of impact.

### 1. `risk_per_trade` was dead — the margin cap bound on every signal

`node_guard._resolve_entry_sizing` caps notional at `margin_per_trade × leverage`. With
`margin_per_trade` at 10–20 that cap bound on **22 of 22** risk-mode sizings in the log window:

```
$ docker compose logs ai-signal-generator --since 720h | grep -oP 'sizing: mode=\w+' | sort | uniq -c
     14 sizing: mode=margin
     22 sizing: mode=risk
$ docker compose logs ai-signal-generator --since 720h | grep -c 'risk sizing clamped'
22
```

```
risk sizing clamped: target risk $5.00 needs notional 1724.14 but margin cap allows 100.00
(margin_per_trade=10.00 × lev=10) — effective risk $0.29
```

Effective risk distribution against a $5.00 target:

```
      1 0.29
      2 0.30
      1 0.40
      1 0.45
     16 0.50
      1 0.70
```

Per-trade risk as % of allocation, before the fix:

| strategy | mode | notional cap | SL dist | risk at SL | % of alloc |
|---|---|---|---|---|---|
| bnb-ai-scalper | risk | $100 | 0.48% | $0.48 | 0.50% |
| tao-range-rotation | margin | $100 | 1.39% | $1.39 | 1.35% |
| eth-geometric-range | margin | $200 | 0.81% | $1.62 | 1.67% |
| sol-trend-follower | margin | $200 | 1.10% | $2.19 | 2.22% |
| ai-btc-regime-router | risk | $200 | 1.84% | $3.68 | 3.72% |
| hype-mean-reversion | risk | $400 | 1.84% | $7.36 | 7.95% |

HYPE was the only strategy whose target notional ($332) fit under its cap ($400) — and the only
one with meaningful per-trade magnitude (worst −$6.77, best +$3.64). That confirms the mechanism.

### 2. Positions were scratched long before TP or SL

```
        strategy_id         |    close_reason    | count | avg_pct_of_notional
----------------------------+--------------------+-------+---------------------
 bnb-ai-scalper-edbb        | signal_close       |    17 |              -0.372
 bnb-ai-scalper-edbb        | Closed on exchange |     8 |              -0.307
 ai-btc-6f8c                | signal_close       |     1 |               0.083
 tao-ai-range-rotation-d257 | signal_close       |     5 |               0.084
```

TPs sit 0.8–5.4% away; realized moves were 0.08–0.4%. One BTC position opened and closed at
58157 → 58157 — exactly 0.000% — for −$0.044 of pure fees.

### 3. Fees dominated at that size

`pnl_realized` is net of both legs (`order-listener/app/reconciler.py:941`), so backing fees out:

| strategy | net P&L | fees | gross |
|---|---|---|---|
| ai-btc | −$1.18 | $2.39 | **+$1.21** |
| eth | −$3.02 | $1.90 | −$1.12 |
| bnb | −$3.26 | $1.65 | −$1.61 |

BTC's strategy was profitable gross; fees flipped it negative.

## Changes applied

### Fix 1 — make `risk_per_trade` govern (migration 061)

All 7 AI strategies moved to `sizing_mode='risk'`, uniform `risk_per_trade=5.00` (5% of the
~$100 per-strategy allocation), and `margin_per_trade` raised 10/20 → **50** so the cap becomes
the collateral *safety bound* migration 054 intended rather than the binding constraint.

```
        strategy_id         | sizing_mode | risk | margin_cap | lev | notional_cap | clamps_below_sl_pct | min_close | conf_ovr |   template_id
----------------------------+-------------+------+------------+-----+--------------+---------------------+-----------+----------+-----------------
 ai-btc-6f8c                | risk        | 5.00 |         50 |  20 |         1000 |                0.50 |      0.30 |    0.850 | regime_router
 bnb-ai-scalper-edbb        | risk        | 5.00 |         50 |  10 |          500 |                1.00 |      0.30 |    0.850 | flow_swing
 eth-ai-34d2                | risk        | 5.00 |         50 |  20 |         1000 |                0.50 |      0.30 |    0.850 | geometric_range
 hype-breakout-da2e         | risk        | 5.00 |         50 |  20 |         1000 |                0.50 |      0.30 |    0.850 | mean_reversion
 sol-ai-6486                | risk        | 5.00 |         50 |  20 |         1000 |                0.50 |      0.30 |    0.850 | trend_following
 tao-ai-range-rotation-d257 | risk        | 5.00 |         50 |  10 |          500 |                1.00 |      0.30 |    0.850 | range_rotation
 xrp-ai-3844                | risk        | 5.00 |         50 |  20 |         1000 |                0.50 |      0.30 |    0.850 | breakout
```

**Verification — live `_resolve_entry_sizing` run inside the running container against real DB
config**, at each strategy's historical average stop distance:

```
strategy                        sl%  notional   margin   risk$  %alloc  clamped
--------------------------------------------------------------------------------
ai-btc-6f8c                   1.839    271.89    13.59    5.00    5.06  False
bnb-ai-scalper-edbb           0.476    500.00    50.00    2.38    2.48  True
bnb-ai-scalper-edbb           1.500    333.33    33.33    5.00    5.21  False
eth-ai-34d2                   0.810    617.28    30.86    5.00    5.16  False
hype-breakout-da2e            1.839    271.89    13.59    5.00    5.40  False
sol-ai-6486                   1.095    456.62    22.83    5.00    5.06  False
tao-ai-range-rotation-d257    1.394    358.68    35.87    5.00    4.85  False
xrp-ai-3844                   1.000    500.00    25.00    5.00    5.00  False
```

Effective risk goes from $0.29–$3.68 to a true $5.00 everywhere. Note BNB at its **old** 0.476%
stop still clamps to $2.38 — which is precisely why fix 3 is required; the two interlock.

When the cap does bind, the loss at the stop is still ≤ $5 — only the collateral posted is
larger, never the risk. The 50%-from-peak drawdown stop (`webhook_handler.py:128`, auto-disables
and flattens) is unchanged and now needs ~10 consecutive full stop-outs to trip.

### Fix 2 — discretionary-close floor (migration 060 + `node_guard.py`)

New `ai_strategy_config` columns: `min_close_move_pct` (default 0.30) and
`close_confidence_override` (default 0.850). A `close_long`/`close_short` is refused while price
sits within 0.30% of entry **in either direction** — a +0.08% scratch is as much noise as a
−0.08% one — unless confidence ≥ 0.85, i.e. genuine structural invalidation.

Safety: this gates only the LLM's discretionary exit. Every open position carries an
exchange-side guaranteed SL (`webhook_handler.py` "Guaranteed SL injection") plus its TP, so a
gated position still stops out on the exchange with no model involvement. **Downside protection
is untouched.** Missing entry/current price lets the close through — refusing an exit on absent
data is the more dangerous failure. `partial_close` is not gated.

**Verification — `node_guard` run inside the running container against live DB config:**

```
live config: min_close_move_pct=0.30 close_confidence_override=0.850

case                                  verdict   reason
------------------------------------------------------------------------------
+0.08% scratch, conf 0.70             BLOCK     close_below_min_move
-0.08% scratch, conf 0.70             BLOCK     close_below_min_move
+0.50% real move, conf 0.70           ALLOW     -
-0.60% real move, conf 0.70           ALLOW     -
+0.05%, conf 0.90 (invalidation)      ALLOW     -
```

### Fix 3 — widen the BNB scalper (migration 060 template + 061 config)

0.476% stops against 0.790% targets could not clear fees plus spread: 7 wins / 18 losses, with
17 of 25 exits scratched by signal. New `flow_swing` template keeps the order-flow entry edge
(VWAP anchoring, depth imbalance, wall interaction, liquidation-burst fades) but moves it to a
**1.0–2.0% stop with a 2:1 minimum reward** and a **12-hour** time stop instead of 2 hours.
Position management changed from "flow flips against you → close immediately" to closing only on
structural invalidation. Cycle intervals widened 15m → 1h (re-deciding every 15 minutes over a
12-hour hold is 48 chances to talk itself out of the trade); `cooldown_entry_minutes` 60 → 120,
`at_risk_threshold_pct` 1.50 → 1.00.

`scalper` is left intact in the template library — nothing else was using it.

**Verification — served prompt for BNB:**

```
STRATEGY INSTRUCTIONS:
You are a quantitative crypto analyst trading perpetual futures on a short-swing horizon (1H-4H).
Your edge is order-flow imbalance around VWAP; your discipline is structural stops wide enough to
survive noise, a minimum 2:1 reward-to-risk, and refusing to trade into scheduled events or dead tape.

Read this carefully: your stops are 1.0-2.0%, NOT sub-1%. A stop tighter than 1.0% is inside the
noise band on these instruments and will be taken out by spread and wick alone, and the round-trip
fee then eats what is left. ...
```

### API wiring

`dashboard-api/src/routes/ai.ts`: added both columns to `ALLOWED_CONFIG_FIELDS` and to
`formatConfig` numeric coercion, plus range validation mirroring the DB CHECKs. Without this the
new knobs would have been DB-only and silently untunable from the UI.

## Verification

Migrations applied clean, both self-verified:

```
$ docker compose exec -T postgres psql -U matp -d matp -v ON_ERROR_STOP=1 < db/migrations/060_ai_close_gate.sql
BEGIN
ALTER TABLE
DO
COMMENT
COMMENT
INSERT 0 1
COMMIT
NOTICE:  Migration 060 verified OK: close-gate columns + flow_swing template exist

$ ... < db/migrations/061_ai_sizing_retune.sql
BEGIN
UPDATE 7
UPDATE 7
UPDATE 1
COMMIT
NOTICE:  Migration 061 verified OK: risk sizing + margin ceiling + flow_swing applied
```

Tests — 8 new close-gate cases in `tests/test_guard_sizing.py`:

```
$ docker compose run --rm --no-deps -v .../ai-signal-generator:/src -w /src ai-signal-generator \
    sh -lc 'python -m pytest tests/test_guard_sizing.py -q'
......................                                                   [100%]
22 passed, 1 warning in 0.42s
```

Full suite: `1 failed, 95 passed`. The failure is
`test_ohlcv.py::test_fetch_ohlcv_separates_closed_candles_from_live_price` — a `_FakeExchange`
fixture missing `fetch_markets`. **Pre-existing**, confirmed by stashing the change and re-running
on clean HEAD: `1 failed, 4 passed`. Unrelated to this work; not fixed here.

Deploys — both via `./scripts/redeploy.sh`, new code confirmed *in the running containers*:

```
$ docker compose ps ai-signal-generator dashboard-api --format '{{.Service}}\t{{.Status}}'
ai-signal-generator	Up 2 minutes (healthy)
dashboard-api	Up 8 seconds (health: starting)
$ docker compose exec -T ai-signal-generator grep -c "close_below_min_move" /app/app/graph/nodes/node_guard.py
1
$ docker compose exec -T dashboard-api grep -c "min_close_move_pct" /app/dist/routes/ai.js
5
```

API serves the new fields, correctly typed:

```
{'sizing_mode': 'risk', 'risk_per_trade': 5, 'min_close_move_pct': 0.3,
 'close_confidence_override': 0.85, 'template_id': 'flow_swing',
 'interval_position_open': '1h', 'cooldown_entry_minutes': 120}
```

Validation rejects out-of-range and accepts valid:

```
{'min_close_move_pct': 25}        -> 400 {"error":"min_close_move_pct must be between 0 and 10 (0 disables the close gate)"}
{'close_confidence_override': 1.5} -> 400 {"error":"close_confidence_override must be between 0 (exclusive) and 1"}
{'min_close_move_pct': 0.45}       -> 200 {...}
```

(The 0.45 written by that test was reset to 0.30.)

Schedulers reloaded — BNB now wakes on the 1h candle (2603s), previously 15m:

```
Started 6 scheduler(s): ['xrp-ai-3844', 'eth-ai-34d2', 'sol-ai-6486',
                         'tao-ai-range-rotation-d257', 'bnb-ai-scalper-edbb', 'ai-btc-6f8c']
Scheduler strategy=bnb-ai-scalper-edbb sleeping 2603s until candle-close+buffer wake (43.4min)
```

Six, not seven, because `hype-breakout-da2e` is disabled; its config was retuned anyway and will
apply if re-enabled.

## Notes / not addressed

- Two redis timeouts at container boot (`spread_monitor`, `funding_monitor` first cycles, 13:17).
  Both monitors started fine and run hourly; these are startup races in modules this change does
  not touch, consistent with the known host-load behaviour. Not a regression from this work.
- `position_unrealized_pnl_pct` is hardcoded `None` at both call sites (`scheduler.py:197`,
  `main.py:337`) but is rendered into the prompt (`prompt/builder.py:45`) — the model is told its
  open P&L is unknown on every cycle. Left alone here; worth a follow-up.
- `db/init.sql` not regenerated — it is baselined at migration 037 and 038–059 were likewise
  applied as migrations only. Following existing practice.
- The $5 risk unit and the 0.30% / 0.85 close-gate defaults are single numbers, tunable per
  strategy from the UI now that the API carries them.
