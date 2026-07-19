# Spread harvest UI (2026-07-19)

Branch: `feat/spread-harvest`. Surfaces the staged pipeline in dashboard-ui —
the "one tap" of armed+confirm is now an actual button.

## Built

- **`dashboard-ui/src/pages/Spread.tsx`** (route `/spread`, nav entry "⚡ Spread"):
  - **Monitor strip**: all 24 coins as tiles with the live trailing 7d spread
    (%/yr, signed, hot coins flagged 🔥), sorted by |spread|; enter/exit
    thresholds and last-cycle time in the header.
  - **Plans table**: status chips (armed/executed/expired/cancelled/failed),
    spread, legs, $/leg, est/day, breakeven, abort band. Armed rows get an
    **Execute** button with a confirm dialog that spells out the real-order
    consequence; busy/error states surfaced inline.
  - **Positions table**: status chips (open/closed/aborted/leg_failed/
    close_failed), legs, size, entry prices, abort band, PnL, close reason.
    Open rows get a **Close** button (confirm dialog).
  - 30s polling; Tailwind styling consistent with the existing pages;
    responsive (tables scroll inside sections on mobile).
- **`dashboard-api`**: `GET /spread/monitor` proxy to the ai-signal-generator's
  internal status endpoint so the UI needs no new nginx routes (everything
  rides `/api/dashboard/`).

## Verification

```
$ docker compose exec -T dashboard-ui grep -rl 'Spread Harvest' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-Cx1QTWOh.js          <- string in served bundle
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-Cx1QTWOh.js                                        <- live asset = new build

$ curl http://localhost/api/dashboard/spread/monitor
{"enabled":true,"enter_ann":0.5,...,"coins":{"BTC":{"trailing_ann_pct":8.91,...  (24 coins)
$ curl http://localhost/api/dashboard/spread/plans      -> 5 plans, first: BTC executed
$ curl http://localhost/api/dashboard/spread/positions  -> 3 positions, first: BTC closed pnl -0.0216
```

The page's Execute/Close buttons call the same dashboard-api routes verified
live in the phases 2-3 report (real demo orders on both venues).

TypeScript compiled clean in the image build (a TS error fails
`npm run build` and the deploy).
