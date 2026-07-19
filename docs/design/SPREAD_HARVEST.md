# Spread Harvest — cross-venue funding-spread capture, staged automation

Branch: `feat/spread-harvest`. The strategy validated by edge-research phases
5–6 (`research/README.md`, reports `edge-research-phase5-funding-spread.md`,
`edge-research-phase6-spread-gates.md`): the same perp carries different
funding on Hyperliquid vs Blofin; a delta-neutral two-leg position (long where
longs pay less / are paid, short where shorts are paid more) collects the
spread each settlement. Walk-forward OOS +14.2%/yr on notional; realistic
+12–16%/yr after the abort haircut. Market-neutral.

## Decision record

- **Staged automation** (same ladder as funding harvest): monitor → armed plan
  + notification → one-tap confirm execute → full auto only after a real
  episode is handled cleanly end-to-end.
- **Venues: Hyperliquid + Blofin** — both adapters already exist in
  order-executor; both legs are perps (no spot support needed anywhere).
- **Signal**: 168h trailing mean of the hourly-equivalent funding spread,
  annualized. Enter when |trailing| > 50%/yr, exit below 10%/yr — the config
  the walk-forward selected in every fold. Blofin's mixed 4h/8h settlement
  cadence is handled by inferring each settlement's interval from timestamp
  gaps (same as `research/spread_gates.py`).
- **Concurrency cap 3** (highest |trailing| wins), matching the research sim.
- **Sizing**: notional N per leg = capital/2 at 2x leg leverage → margin N/2
  per leg, leaving half the capital as top-up buffer. Default capital $200 →
  N=$100/leg.
- **Hard requirements from gate 4** (phases 3+): the **±25% abort rule**
  (close both legs when price runs 25% from episode entry — retains 86% of
  P&L, caps leg loss) and an **auto margin top-up watcher**. Any execution
  build without these two is out of spec.
- **Accounts**: both configured accounts are demo. Execution (phase 3)
  requires funded mainnet accounts on both venues + an operator capital
  decision. Phase 1 (this) is read-only.

## Phases

1. **Monitor + armed planner (BUILT 2026-07-19)** — `app/spread_monitor.py` in
   ai-signal-generator. Hourly, per coin: fetch last ~168h of HL hourly
   funding + Blofin settlements (2 public requests/coin), compute the trailing
   annualized spread, hysteresis per coin with state in Redis. On cool→hot
   (supported coin, concurrency slot free): build a plan — direction, leg
   sizes, live book walk on both venues to the target notional, est. daily
   collect, breakeven, abort prices — persist to `spread_plans` (migration
   058), notify with numbers (`spread.hot`). On hot→cool: expire armed plans,
   notify (`spread.cooled`). Endpoints: `GET /internal/spread-monitor/status`,
   `GET /internal/spread-harvest/plans`,
   `POST /internal/spread-harvest/plan/{coin}` (preview/test hook).
2. **Paired execution model (BUILT 2026-07-19)** — two-venue paired position representation in
   the executor; leg-failure rollback (if leg 2 fails, immediately flatten
   leg 1).
3. **Armed execute + episode management (BUILT 2026-07-19; see phase 2-3 report)** — one-confirmation execute; the
   abort watcher (±25%), margin top-up watcher, exit-on-cooled unwind.
4. **Full auto** — after ≥1 real episode handled cleanly through phase 3.

## Cost model

0.3% of notional per episode round trip (4 perp legs incl. slippage, per the
research grid); plan reports live book-walk slippage per venue. Breakeven days
= round trip / est. daily collect.

## Universe

The 24 research coins (BTC ETH SOL XRP DOGE AVAX LINK LTC DOT NEAR BNB ADA
APT ARB SUI TIA SEI JTO PYTH JUP APE BLUR STRK GMT) — all listed on both
venues as of 2026-07-19; the monitor tolerates delistings (a coin failing on
either venue is skipped that cycle).
