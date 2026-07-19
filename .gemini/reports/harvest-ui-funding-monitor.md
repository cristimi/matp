# Harvest UI: funding monitor + funding plans surfaced (2026-07-19)

Branch: `feat/spread-harvest`. Answer to "is the funding collector visible in
the UI as well?" — it wasn't (push notifications + internal endpoints only).
Now it is: the `/spread` page hosts both strategies and is renamed **"⚡
Harvest"** in the nav.

## Built

- **dashboard-api `spread.ts`**: two new routes —
  `GET /spread/funding-monitor` (proxy to ai-signal-generator's
  `/internal/funding-monitor/status`) and `GET /spread/funding-plans`
  (reads `funding_harvest_plans`).
- **`Spread.tsx`**: second monitor strip "Funding monitor — trailing 3d
  Binance funding, annualized" (12 coins, hot 🔥 flagged, thresholds in
  header), and a "Funding-harvest plans" table (signal vs HL live funding,
  legs, $/leg, est/day, breakeven) that renders only when plans exist and is
  explicitly labeled **informational — execution for this trade is not built**
  (unlike the spread trade, which has the Execute button). Spread sections
  retitled "Spread monitor / Spread plans / Spread positions" for clarity.

## Verification

```
$ docker compose exec -T dashboard-ui grep -l 'Funding monitor' /usr/share/nginx/html/assets/index-DvQm0GK_.js
/usr/share/nginx/html/assets/index-DvQm0GK_.js          <- in served bundle
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-DvQm0GK_.js                                        <- live asset = new build

$ curl /api/dashboard/spread/funding-monitor -> 12 coins, hottest LTC +6.9%/yr (cool)
$ curl /api/dashboard/spread/funding-plans   -> 2 plans: BTC cancelled, BTC expired (phase-1 tests)
```
