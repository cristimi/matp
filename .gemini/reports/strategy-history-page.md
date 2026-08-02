# Strategy history / status page

Opened by **double-tapping** a strategy card in the tree. Replaces the old
`/strategy/:id` page, which was showing zeros because it read `strategy_stats` /
`strategy_performance` — two tables that exist but that nothing ever writes to.

## What changed

- `dashboard-api/src/routes/strategies.ts`
  - new `GET /strategies/:id/history?period=today|7d|30d|all` — everything the page
    needs, computed live from `strategy_positions` (closed trades), `orders` and
    `signal_log`. The dead stats tables are deliberately not used.
  - `GET /strategies/:id/positions` now accepts `scope=closed` and orders closed
    positions by `closed_at DESC` (was `opened_at`).
- `dashboard-ui/src/pages/StrategyDetail.tsx` — rewritten.
- `dashboard-ui/src/pages/StrategyTree.tsx` — `useLongPress` gained an optional
  double-tap callback; long-press still collapses the card, double-tap navigates to
  the history page and undoes the expand the first tap caused.
- `dashboard-ui/src/api.ts` — `StrategyHistory` types, `fetchStrategyHistory`,
  `fetchTreePositions` now takes scope `closed` and limit/offset.

## Page contents

Status header (running/paused + stop reason, account, exchange), divergence warning,
period switch, 15 headline stats, equity curve + drawdown chart, profit-per-day bars,
long-vs-short table, holding-time + MFE/MAE, "why trades ended" breakdown, signal /
order health with the last 20 signals, open positions, and the full closed-trade
history paged 50 at a time.

Honest-labelling notes rendered in the UI:
- "after fees" carries a footnote with the actual fee coverage when it is below 99%
- MFE/MAE block states how many trades it was measured on
- "Closed on exchange" is explained in plain words

## Verification

Type checks, both services clean:

```
$ cd dashboard-api && npx tsc --noEmit
$ cd dashboard-ui  && npx tsc --noEmit     # exit code 0
```

Endpoint through nginx, real strategy with 19 closed trades:

```
$ curl -s "http://localhost/api/dashboard/strategies/ai-btc-6f8c/history?period=all"
strategy: BTC AI Range Rotation
summary: {"trades": 19, "wins": 10, "losses": 9, "breakeven": 0,
 "win_rate": 52.63157894736842, "pnl_total": -1.183249, "avg_pnl": -0.06227626315789474,
 "avg_win": 0.7590037, "avg_loss": -0.9748095555555556, "best_trade": 2.209547,
 "worst_trade": -1.971779, "profit_factor": 0.8651304653695319, "gross_win": 7.590037,
 "gross_loss": 8.773286, "avg_hold_secs": 10037.655526315788, "min_hold_secs": 131.646,
 "max_hold_secs": 19078.509, "avg_leverage": 12.631578947368421, "max_win_streak": 2,
 "max_loss_streak": 2, "max_drawdown": 2.66144, "max_drawdown_pct": 2.6334583724060847,
 "open_count": 0, "open_pnl": 0}
close_reasons: [{'reason': 'unknown', 'count': 11, 'pnl': 2.1209629999999997},
 {'reason': 'Closed on exchange', 'count': 7, 'pnl': -3.71986},
 {'reason': 'signal_close', 'count': 1, 'pnl': 0.415648}]
orders: {'total': 60, 'filled': 41, 'pending': 0, 'not_filled': 19}
fees:   {'total': 2.386133, 'rows_with_fee': 22, 'rows_total': 60, 'coverage': 36.67}
```

Period switch narrows correctly (same strategy family, `period=7d`):

```
$ docker compose exec -T nginx wget -qO- \
    "http://dashboard-api:8003/strategies/bnb-ai-scalper-edbb/history?period=7d"
7d {'trades': 3, 'wins': 2, 'losses': 1, 'win_rate': 66.67, 'pnl_total': -4.03610676,
    'max_drawdown': 6.19099134, 'open_count': 1, 'open_pnl': 0.684372690712485}
```

Live snapshot enrichment works — `open_pnl` above came from the live-PnL snapshot,
not the DB column.

`scope=closed` on the positions endpoint:

```
$ docker compose exec -T nginx wget -qO- \
    "http://dashboard-api:8003/strategies/bnb-ai-scalper-edbb/positions?scope=closed&limit=3"
[{ "id": "ce29013f-...", "side": "short", "base_asset": "BNB", "size": 0.01,
   "entry_price": 572.37, "closing_price": 569.23, "realized_pnl": 0.86084184,
   "leverage": 10, "closed_at": "2026-07-29T05:01:53.889Z",
   "close_reason": "signal_close", "status": "closed", ... }, ...]
```

Deploy, new bundle is the one nginx serves:

```
$ ./scripts/redeploy.sh dashboard-api
NAME                   IMAGE                SERVICE         STATUS
matp-dashboard-api-1   matp-dashboard-api   dashboard-api   Up 5 seconds (health: starting)
✓ dashboard-api redeployed.

$ ./scripts/redeploy.sh dashboard-ui
matp-dashboard-ui-1   matp-dashboard-ui   dashboard-ui   Up 6 seconds
   live dashboard-ui asset: index-BYSYq9EG.js
✓ dashboard-ui redeployed.

$ docker compose exec -T dashboard-ui grep -rl 'Why trades ended' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-BYSYq9EG.js
$ docker compose exec -T dashboard-ui grep -rl 'How long trades stay open' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-BYSYq9EG.js
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-BYSYq9EG.js
```

## Known data gaps (shown, but labelled)

| Gap | Effect |
|---|---|
| `exchange_fee` present on 304/565 orders | "after fees" approximate, coverage % shown |
| `close_reason` NULL on 52/179 positions | grouped as "Unknown" |
| `mfe_r` / `mae_r` on 15/179 positions | block shows only when data exists, with coverage |
| `indicator_price` never written | no slippage-vs-signal metric on the page |
