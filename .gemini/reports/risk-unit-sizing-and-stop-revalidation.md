# Risk-unit position sizing + fill-price stop revalidation

Implements ROADMAP open question #2 (Position Sizing Method) plus the safety
prerequisites identified in the 2026-07-13 order analysis (4 of 33 filled AI
entries had degenerate or inverted stops).

## 1. Fill-price stop revalidation (order-listener)

New `app/stop_revalidation.py::revalidate_stops_for_fill(side, ref, fill, sl, tp)`:
any stop on the wrong side of, or within 0.1% of, the actual fill price is
re-anchored to the fill preserving its original fractional distance from the
reference (request) price. Valid stops pass through untouched.

Hooked into both fill paths:
- `webhook_handler._process_order` — market/immediate fills: after
  `_apply_position_fill`, re-anchored stops are pushed via
  `call_executor_modify_stops` and audited on the order row
  (`signal_metadata.stops_reanchored_to_fill`, `reanchor_landed`).
- `reconciler._reconcile_pending_orders` — resting-limit fills: stops are
  revalidated against the back-solved marginal fill price *before* the
  post-fill modify-stops call; corrected prices persisted on the order.

Covers the three live incident shapes verbatim (encoded as tests):
ETH short SL $0.09 from fill; BTC long filled below its own SL; BTC short
TP above its fill.

```
$ docker compose exec -T order-listener python -m pytest tests/test_stop_revalidation.py -v
tests/test_stop_revalidation.py::test_valid_stops_untouched_long PASSED
tests/test_stop_revalidation.py::test_valid_stops_untouched_short PASSED
tests/test_stop_revalidation.py::test_incident_1_short_sl_degenerate_after_price_improvement PASSED
tests/test_stop_revalidation.py::test_incident_2_long_filled_below_its_sl PASSED
tests/test_stop_revalidation.py::test_incident_3_short_tp_wrong_side_after_slippage PASSED
tests/test_stop_revalidation.py::test_stop_degenerate_at_request_time_gets_min_floor PASSED
tests/test_stop_revalidation.py::test_missing_stops_pass_through PASSED
tests/test_stop_revalidation.py::test_no_fill_price_no_op PASSED
tests/test_stop_revalidation.py::test_decimal_and_float_inputs_mix PASSED
============================== 9 passed in 0.36s ===============================
```

Full listener suite: `5 failed, 54 passed` — the 5 failures are pre-existing
on the previous image (verified by running the suite in the old container
before deploying: same 5), unrelated 422-vs-200 drift in webhook tests.

## 2. Guard-side wrong-side stop rejection (ai-signal-generator)

`node_guard` now rejects with `stop_wrong_side`:
- `adjust_stops`: long needs new_sl < current price < new_tp (short mirrored).
- `amend_order`: for the target order's side (from OPEN ORDERS context),
  buy needs sl < new limit < tp (sell mirrored). This is the hole the
  2026-07-10 ETH trade went through (amended long, TP below its own entry).
  If the target order isn't in context, validation is skipped (can't verify).

## 3. Risk-unit sizing (migration 054 + node_guard)

`ls db/migrations` confirmed 053 highest → created
`054_risk_unit_sizing.sql`. Apply output:

```
COMMENT
COMMIT
psql:<stdin>:73: NOTICE:  Migration 054 verified OK: risk-unit sizing columns exist
DO
 sizing_mode                 | character varying(10)    |           | not null | 'margin'::character varying
 risk_per_trade              | numeric(12,2)            |           |          |
    "ai_strategy_config_risk_per_trade_chk" CHECK (sizing_mode::text = 'margin'::text OR risk_per_trade IS NOT NULL AND risk_per_trade > 0::numeric)
    "ai_strategy_config_sizing_mode_chk" CHECK (sizing_mode::text = ANY (ARRAY['margin'::character varying, 'risk'::character varying]::text[]))
```

`node_guard._resolve_entry_sizing(sc, entry_price, sl_pct)`:
- **margin mode** (default): notional = margin_per_trade × leverage —
  bit-identical to the legacy formula (unit-tested); zero behavior change
  for existing strategies.
- **risk mode**: notional = risk_per_trade / sl_frac → a stop-out loses
  ≈ risk_per_trade dollars. Hard cap: notional ≤ margin_per_trade × leverage
  (margin_per_trade = collateral ceiling; the order-listener's independent
  margin clamp enforces the same bound, so the two layers agree). When the
  cap binds the effective risk is below target — WARNING logged and flagged.
- sl_pct is range-validated ([0.05, 50]) BEFORE sizing (guards the division;
  sizing moved after validation in both open_* and place_limit_* branches).
- Audit: `sizing_meta` flows through AgentState into the webhook's
  `signal_metadata.sizing` {sizing_mode, margin_usd, target/effective risk,
  risk_clamped_by_margin_cap} and a guard INFO log per sized entry.

```
$ python -m pytest tests/ -q   (python:3.11-slim disposable container)
1 failed, 78 passed  — failure = known pre-existing ccxt-drift test_ohlcv
$ python -m pytest tests/test_guard_sizing.py -v
14 passed (sizing math, clamp, legacy parity, guard integration,
adjust/amend side validation incl. the exact 2026-07-10 ETH shape)
```

## 4. Dashboard

- `ai.ts`: `sizing_mode` ∈ {margin, risk}; `risk_per_trade` positive ≤ 100000;
  setting `sizing_mode='risk'` without a stored/provided risk value fails fast
  with a clear 400 (instead of a DB constraint 500). `formatConfig` wraps
  `risk_per_trade` in `Number()`.
- `Strategies.tsx` edit form: "Position Sizing" mode select + "Risk per Trade"
  input (risk mode only) + collateral-cap hint; client-side validation.

Verification (live through nginx):

```
── validation: risk mode without value:
{"error":"sizing_mode='risk' requires risk_per_trade to be set"}
── validation: bad mode:
{"error":"sizing_mode must be 'margin' or 'risk'"}
── set risk mode on dry-run BNB strategy:
{'strategy_id': 'bnb-ai-scalper-edbb', 'sizing_mode': 'risk', 'risk_per_trade': 5, 'dry_run': True}
── GET persisted:
{'sizing_mode': 'risk', 'risk_per_trade': 5}
UI bundle OK   (grep 'Risk per Trade' in served /usr/share/nginx/html)
```

All other strategies confirmed still `sizing_mode='margin'` (no behavior
change). Deploys via `./scripts/redeploy.sh` for order-listener,
ai-signal-generator, dashboard-api, dashboard-ui; health checks passed.

## Runtime smoke limits (honest note)

A manual dry-run cycle on the risk-mode BNB strategy returned `hold`, so the
sizing code path (which only runs on opening actions) did not fire live
during the session window — an LLM open can't be forced. The sizing math is
covered end-to-end through `node_guard` by unit tests; the first real opening
signal will emit the `sizing: mode=risk ...` guard log and the
`signal_metadata.sizing` audit block.

## How to enable per strategy

Strategy edit form → Position Sizing → "Risk-based", set Risk per Trade ($).
Remember `margin_per_trade` becomes the collateral cap: for the target risk
to be achievable with ~1% stops at 20x, margin_per_trade must be ≥
risk × 100 / (sl_pct × leverage) — e.g. $5 risk, 1% stop, 20x → $25 margin cap.
BNB (dry-run) left configured as a demo: risk $5, cap $10×10x=$100 notional.
