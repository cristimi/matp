# Funding Harvest — staged automation design

Branch: `feat/funding-harvest`. Successor to the funding-regime monitor (on `main`)
and the edge research (`feat/edge-research`): the delta-neutral funding premium is
real but episodic, so the system arms and assists rather than trades blindly.

## Decision record

- **Automation level: staged "armed + confirm"** (user decision 2026-07-19).
  The system computes and persists a full trade plan when the regime fires and
  notifies with real numbers; execution requires one explicit confirmation.
  Full-auto only after the first real regime validates the pipeline end-to-end.
- **Venue: single-venue Hyperliquid** (spot + perp in one account). Cross-venue
  (Binance spot vs HL perp) rejected: two accounts, transfers, too much failure
  surface for a homelab system.
- **Universe: coins with liquid HL Unit spot** — BTC→UBTC, ETH→UETH, SOL→USOL,
  DOGE→UDOGE (AVAX→UAVAX, NEAR→ONEAR listed but thin; excluded until the
  liquidity probe proves otherwise). No ADA/LTC/DOT spot; XRP/BNB/LINK only as
  wrapped variants we don't trust. The regime *signal* stays Binance 8h funding
  (deep history, thresholds calibrated there); the *collection* economics use
  HL's own hourly funding, and the plan reports both so divergence is visible.
- **Trade shape**: short perp at 2x leverage + long spot, equal notional N.
  Capital C splits N (spot) + N/2 (perp margin) → N = C × 2/3. Low leverage on
  the short because a melt-up moves against the perp leg alone; margin top-up
  logic is part of phase 3, not optional.
- **Accounts**: both configured accounts are demo-mode. HL testnet has no Unit
  spot assets, so the execute path ultimately needs a funded **mainnet** account
  — an explicit operator decision before phase 3 goes live. Plans (phase 1) are
  read-only and safe to run now.

## Plan lifecycle

`armed` (regime hot, plan computed) → `executed` (operator confirmed; phase 3)
or `expired` (regime cooled / superseded by a fresher plan) or `cancelled`
(operator dismissed). One live armed plan per coin; a new fire supersedes.

## Phases

1. **Planner (this phase)** — in ai-signal-generator next to the monitor. On a
   coin's cool→hot transition (supported universe only): resolve HL spot pair,
   probe live spot + perp books to the target notional, price both legs, compute
   entry cost, HL-funding daily income, and break-even days; persist to
   `funding_harvest_plans` (migration 057); enrich the `funding.hot`
   notification with the numbers. On hot→cool: expire armed plans, enrich the
   cooled notice. Internal endpoints: `GET /internal/funding-harvest/plans`,
   `POST /internal/funding-harvest/plan/{coin}` (on-demand dry plan, also the
   test hook).
2. **Executor spot support** — HL adapter learns spot order placement, spot
   symbol resolution (Unit pairs), spot balances; paired-position representation.
3. **Armed execute + unwind** — one-confirmation endpoint (dashboard button)
   fires both legs with leg-failure rollback; unwind on `funding.cooled` or
   operator command; margin top-up watcher while a pair is open.
4. **Full auto** — only after ≥1 real regime handled cleanly through phase 3.

## Cost model (config-overridable)

HL perp taker 0.045%, HL spot taker 0.07%, plus book-walk slippage measured at
plan time. Round trip = 2×(spot+perp) legs. Break-even days =
entry+exit cost ÷ (current HL funding × N × 24h). Plans display Binance trailing
(the signal) next to HL live funding (the income) — if HL pays materially less,
the plan says so.

## Open questions (operator)

- Mainnet HL account + how much capital to commit when phase 3 lands
  (planner default assumption: $150 → N = $100).
- Whether AVAX/NEAR join the universe after liquidity probes.
