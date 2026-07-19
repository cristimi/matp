# Spread harvest phases 2-3: paired execution + episode management (2026-07-19)

Branch: `feat/spread-harvest`. Completes the executable half of the staged
pipeline: armed plans can now be confirmed into live two-leg positions, watched,
aborted, and unwound. **Verified end-to-end with real orders on the demo
accounts** (both legs are perps, so HL testnet + Blofin demo exercise the full
path; flipping to real capital is an account swap, not a code change).

## Built

- **`db/migrations/059_spread_positions.sql`** (applied): one row per executed
  episode; `open|closed|aborted|leg_failed|close_failed` lifecycle; one open
  position per coin (partial unique index); both legs' accounts, fills, order
  ids, abort band, close prices, PnL.
- **`order-executor/app/spread_trade.py`**:
  - `execute_plan()` — armed plan → live pair. Short leg first, then long;
    size quantized to the coarser venue's min step so both venues accept the
    same base size; **leg-failure rollback** (long fails → short is flattened,
    3 retries; rollback failure → 🚨 `spread.leg_failure` naked-position alarm).
  - `close_spread()` — closes both legs (reasons `cooled|abort|manual`);
    partial close failure → `close_failed` + 🚨 alarm.
  - `watcher_loop()` (60s) — **±25% abort band** from the plan (gate 4:
    mandatory) auto-closes both legs; a leg within 10% of its liquidation
    price emits `spread.margin_warning` (venues expose no margin-add API, so
    top-up is operator action — alerted, never silent).
  - Endpoints: `POST /spread/execute`, `POST /spread/close`,
    `GET /spread/positions`.
- **dashboard-api `src/routes/spread.ts`** (mounted at `/spread`): plans and
  positions lists + the operator confirm
  (`POST /api/dashboard/spread/plans/:id/execute`) and manual close
  (`POST /api/dashboard/spread/positions/:id/close`). No nginx change needed
  (`/api/dashboard/` covers it).
- **spread monitor** (ai-signal-generator): hot→cool now also **auto-unwinds**
  any open position via the executor (entries need confirmation; exits are
  automatic — the armed+confirm ladder).
- **notification-service**: renders for `spread.executed`, `spread.closed`,
  `spread.leg_failure` (never deduped), `spread.margin_warning`.

## Live verification (demo accounts, real orders)

Full cycle (third run, after two bug-fix iterations below):

```
plan 866c5689 armed (BTC, short hyperliquid / long blofin, $100/leg)
POST /api/dashboard/spread/plans/866c5689/execute
 -> {"position_id":"22d3de1e","coin":"BTC","size":0.0015,
     "short_fill":"64509.0","long_fill":"64469.2"}
HL:     BTC-USDT short 0.0015 @ 64537.0   (venue-confirmed)
Blofin: BTC-USDT long  0.0015 @ 64486.1   (venue-confirmed)
POST /api/dashboard/spread/positions/22d3de1e/close
 -> {"closed":true,"status":"closed","pnl":-0.0216}
both venues flat after close (venue-confirmed)
```

(-$0.02 = bid/ask spread + fees on a seconds-long hold — expected.)

Watcher observed the open position across cycles with no abort (mark ~64.5k
inside the 48,378–80,630 band) and survived a mid-position redeploy by
re-adopting the position from the DB. Notifications all `sent` through the
real pipeline: `spread.executed` ×3, `spread.closed`, and one genuine
`spread.leg_failure` (see below). Close-by-coin endpoint (the monitor's unwind
path) validated: `{"closed":false,"detail":"no open spread position found"}`.

## Bugs found and fixed during verification

1. Watcher margin check assumed dict positions; adapters return `Position`
   models → cycle error. Fixed with dual accessor.
2. `close_spread` UPDATE used `$2` both as value and in a `CASE` comparison →
   `asyncpg AmbiguousParameterError` **after** the venue closes succeeded,
   leaving rows `open` for already-flat legs (repaired manually, noted in
   `details`). Fixed by passing a dedicated boolean parameter. The two
   incomplete first-cycle rows are marked closed/manual with audit notes.
   Silver lining: the retry on flat venues genuinely exercised the
   `close_failed` + 🚨 alarm path.

## Remaining before real capital (phase 4 gate)

- Operator: fund non-demo accounts on both venues; set
  `SPREAD_CAPITAL_USD` to the real figure.
- Let the monitor arm + confirm one real episode end-to-end (phase 4 full-auto
  only after that).
- Known limitation: margin top-up is alert-only (no venue API for it) — the
  25% abort band is the hard protection, per gate 4 (retains 86% of P&L).
